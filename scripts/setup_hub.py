#!/usr/bin/env python3
"""
myainet: setup_hub.py
一条命令把「建网机」建起来 —— 确定性：不靠 agent 现场拼命令、不跳步、每步自己验。
能脚本化的全做完：起注册中心 → 写身份 → 开 SSH → 起 dashboard + patrol → 注册自己 → 自检。
Tailscale：脚本帮你下载 + 装（过个管理员授权），只有「登你自己的账号」（开浏览器）那一下要你来。

为什么要这个脚本：原来让 agent 现场编排 6 步 raw 命令 —— 会跳步、会写错 OS 命令（pythonw 无 stdout 崩等）。
固化成确定性代码后，agent 的活从「编排 6 个易错步骤」缩成「跑这一个 + 看结果」，对任意模型都稳。

用法：
  python3 setup_hub.py                  # 本机起建网机（注册中心 127.0.0.1）
  python3 setup_hub.py --skip-ssh       # 跳过开 SSH（已开过）
  python3 setup_hub.py --verify         # 只自检，看缺哪步、不动手
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPTS = Path(__file__).resolve().parent
PY = sys.executable
IS_WIN = os.name == "nt"

sys.path.insert(0, str(SCRIPTS))
try:
    from healthcheck import proc_running   # 复用 Win32_Process/pgrep 进程检查（判 ④ 是否已在跑）
except Exception:
    def proc_running(_pattern):            # 退化：查不出 → 一律照起，不因导入失败崩
        return None


# ── 确定性地起后台常驻进程（关键：把 agent 现场拼的那套挪进测过的代码）─────────────
def _bg(script: str, *args) -> int:
    """后台起一个常驻脚本：脱离本进程、无窗口、stdout→devnull（子进程拿到有效 stdout，emoji 不崩）。"""
    cmd = [PY, str(SCRIPTS / script), *map(str, args)]
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if IS_WIN:
        # DETACHED_PROCESS=脱离父进程的控制台（守护进程）；stdout=DEVNULL 已给有效 stdout，不会 None-崩。
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw).pid


def _fg(script: str, *args) -> int:
    """前台跑（输出给用户看，如 enable_ssh / register 的提示、管理员弹窗）。返回 returncode。"""
    return subprocess.run([PY, str(SCRIPTS / script), *map(str, args)]).returncode


def _port_up(host, port, tries=40) -> bool:
    for _ in range(tries):
        try:
            socket.create_connection((host, int(port)), timeout=0.4).close()
            return True
        except OSError:
            time.sleep(0.25)
    return False


def _lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _dash_url():
    """大屏地址：dashboard 启动时把端口写在 tempdir/myainet-dashboard-<port>.pid，读它即知。"""
    import glob, re, tempfile
    for f in glob.glob(str(Path(tempfile.gettempdir()) / "myainet-dashboard-*.pid")):
        m = re.search(r"dashboard-(\d+)\.pid$", f)
        if m and _port_up("127.0.0.1", int(m.group(1)), tries=1):
            return f"http://{_lan_ip()}:{m.group(1)}"
    return None


def _win_fw_rule_exists(name: str) -> bool:
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def _ensure_win_firewall(rules, dry: bool = False) -> None:
    """Windows 防火墙开入站口（幂等，按规则名查重）。Win 默认 BlockInbound——不开的话
    同 LAN 的机器够不到注册中心/大屏（之前全靠 Tailscale 的接口豁免兜着，LAN 直连一直是断的）。
    失败只警告不拦路：Tailscale 路不受影响，但要如实说 LAN 直连不通。"""
    if not IS_WIN:
        return
    for name, ports in rules:
        if _win_fw_rule_exists(name):
            print(f"   防火墙 {name}（TCP {ports}）✅ 已有入站规则")
            continue
        if dry:
            print(f"   防火墙 {name}（TCP {ports}）❌ 没有入站规则 —— 同 LAN 直连会被拦（Tailscale 不受影响）")
            continue
        try:
            r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}",
                                "dir=in", "action=allow", "protocol=TCP", f"localport={ports}"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                print(f"   防火墙 {name}（TCP {ports}）✅ 已开入站")
            else:
                print(f"   防火墙 {name}（TCP {ports}）⚠️ 开失败（多半要管理员）——LAN 直连会被拦，Tailscale 不受影响。"
                      f"手动补：netsh advfirewall firewall add rule name={name} dir=in action=allow protocol=TCP localport={ports}")
        except Exception as e:
            print(f"   防火墙 {name} ⚠️ {e}")


def _fw_rules(P, sec):
    """要开的入站口：注册中心端口；主建网机再加大屏的自动选口段（dashboard.find_free_port 7700-7799）。"""
    return [("myainet-registry", str(P))] + ([] if sec else [("myainet-dashboard", "7700-7799")])


def _ts_exe():
    import shutil
    if shutil.which("tailscale"):
        return shutil.which("tailscale")
    if IS_WIN and Path(r"C:\Program Files\Tailscale\tailscale.exe").exists():
        return r"C:\Program Files\Tailscale\tailscale.exe"
    return None


def _ts_up(exe) -> bool:
    try:
        r = subprocess.run([exe, "status"], capture_output=True, text=True, timeout=8)
        return r.returncode == 0 and "100." in (r.stdout or "")
    except Exception:
        return False


def _run(*cmd) -> int:
    try:
        return subprocess.run(list(cmd)).returncode
    except FileNotFoundError:
        return 127


def _tailscale(do_install: bool) -> bool:
    """没装且 do_install → 下载+装（过管理员授权）；装了没登录 → 拉起 `tailscale up`（开浏览器登你账号）。
    脚本替你做：下载 + 安装。只有「登你自己的账号」这步是你来。"""
    exe = _ts_exe()
    if not exe and do_install:
        print("   没装 → 下载安装（约 40MB，会弹管理员授权，点允许）…")
        if IS_WIN:
            if _run("winget", "install", "-e", "--id", "Tailscale.Tailscale",
                    "--accept-source-agreements", "--accept-package-agreements") != 0:
                print("   ⚠️ winget 装不了 → 手动 https://tailscale.com/download/windows 下 .exe 装")
        elif sys.platform == "darwin":
            _run("brew", "install", "tailscale")
            _run("sudo", "tailscaled", "install-system-daemon")
        else:
            _run("sh", "-c", "curl -fsSL https://tailscale.com/install.sh | sh")
        exe = _ts_exe()
    if not exe:
        print("   Tailscale ❌ 没装上 —— 跑 `setup_hub.py` 自动装，或手动 https://tailscale.com/download 装完再 `--verify`")
        return False
    if _ts_up(exe):
        print("   Tailscale ✅ 已登录上线")
        return True
    if do_install:
        print("   起 `tailscale up`（开浏览器登你账号 —— 这一步只能你来）…")
        try:
            # 限 90s：登录是人来点的，可能很慢；不能让它把整个 setup_hub 堵死（agent 跑命令有超时）。
            subprocess.run([exe, "up"], timeout=90)
        except subprocess.TimeoutExpired:
            print("   ⏳ 90 秒没等到登录完成（登录链接在上面）—— 先继续后面的步骤，登完再 `setup_hub.py --verify`")
        except FileNotFoundError:
            pass
        if _ts_up(exe):
            print("   Tailscale ✅ 登录成功、上线")
            return True
    print("   Tailscale ⚠️ 装了但没登录 → `tailscale up` 登一下，再 `setup_hub.py --verify`")
    return False


def _verdict(core_ok: bool, ssh_ok: bool, ts_ok: bool, sec: str = None):
    """统一如实报告：全在=报建成；缺啥说啥，且没建完 exit(1)（让 agent/调用方拿到失败信号，不会误判成功）。
    sec 非空 = 次建网机（精简版，无大屏/巡检，措辞相应变）。"""
    print()
    label = "次建网机" if sec else "建网机"
    services = "本地注册中心 / SSH / Tailscale（同步给主）" if sec else "注册中心 / 大屏 / 巡检 / SSH / Tailscale"
    url = None if sec else _dash_url()
    if core_ok and ssh_ok and ts_ok:
        print(f"✅ {label}建成：{services} 全部就位。")
        if url:
            print(f"   大屏：{url} （同网手机 / 浏览器直接开）")
        return
    miss = []
    if not core_ok:
        miss.append("注册中心" if sec else "核心服务（注册中心/大屏/巡检，见上 ❌）")
    if not ssh_ok:
        miss.append("SSH（22 端口没在听 —— 远程够不到这台）")
    if not ts_ok:
        miss.append("Tailscale（没装上或没登录）")
    print(f"❌ {label}没建完，还差：" + "；".join(miss))
    print(f"   按上面提示补好，再跑 `python3 setup_hub.py{(' --main ' + sec) if sec else ''} --verify` 确认。")
    if url:
        print(f"   （大屏已在跑：{url}）")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="一条命令建『建网机』（确定性，不靠 agent 拼命令）")
    ap.add_argument("--registry-host", default="127.0.0.1")
    ap.add_argument("--registry-port", type=int, default=27182)
    ap.add_argument("--main", default=None,
                    help="建『次建网机』：填主建网机的 Tailscale 地址（或 'auto' 列 tailnet 自动探）。"
                         "给了=本地注册中心同步给主、不起大屏/巡检（建网机的精简版）")
    ap.add_argument("--skip-ssh", action="store_true", help="跳过开 SSH（已开过 / 不需要）")
    ap.add_argument("--verify", action="store_true", help="只自检，不动手")
    args = ap.parse_args()
    H, P = args.registry_host, args.registry_port
    sec = args.main                       # 次模式 = 建网机精简版（本地注册中心 + 同步给主，无大屏/巡检）
    role = "次建网机" if sec else "建网机"

    if args.verify:
        if sec:
            core_ok = _port_up("127.0.0.1", P, tries=2)
            print("注册中心 " + ("✅ 在听" if core_ok else "❌ 没起"))
        else:
            core_ok = _fg("healthcheck.py") == 0
        if IS_WIN:
            _ensure_win_firewall(_fw_rules(P, sec), dry=True)   # verify 只查不动手
        ssh_ok = _port_up("127.0.0.1", 22, tries=2)
        print("Tailscale：")
        ts_ok = _tailscale(do_install=False)
        _verdict(core_ok, ssh_ok, ts_ok, sec)
        return

    print(f"🧱 {role}一键搭建（确定性脚本；每步自己验，不跳步）\n")

    # ① 注册中心（次：带 --main-host 把本地数据同步给主）
    print("① 起注册中心 …")
    if _port_up(H, P, tries=2):
        print("   已在跑 ✅")
    else:
        pid = _bg("registry_server.py", *(["--main-host", sec] if sec else []))
        if _port_up(H, P, tries=40):
            print(f"   起好 ✅（pid {pid}，{P} 在听" + (f"，同步给主 {sec}" if sec else "") + "）")
        else:
            print("   ❌ 注册中心没起来，停在这。手动看报错：python registry_server.py")
            sys.exit(1)
    if IS_WIN:
        print("   开防火墙入站口（LAN 直连用；Tailscale 路不需要）…")
        _ensure_win_firewall(_fw_rules(P, sec))

    # ② 身份标记
    print(f"② 写身份标记（{role}）…")
    _fg("identity.py", "--set", "--role", role, "--central", (sec or H))

    # ③ 开 SSH（要管理员；前台让你看到弹窗）
    if args.skip_ssh:
        print("③ 跳过开 SSH（--skip-ssh）")
    else:
        print("③ 开 SSH 服务（可能弹管理员授权，点允许）…")
        _fg("enable_ssh.py")

    # ④ 大屏 + 巡检（次不起：精简版，本地数据随注册中心同步给主，主那块唯一大屏汇总）
    if sec:
        print("④ 次建网机不起大屏/巡检（精简版；本地数据随注册中心同步给主）")
    else:
        print("④ 起大屏 + 巡检（常驻）…")
        for script, name in (("dashboard.py", "大屏"), ("patrol.py", "巡检")):
            if proc_running(script) is True:      # 仅在「确定在跑」时跳过；查不出(None)也照起
                print(f"   {name} 已在跑 ✅（跳过）")
            else:
                print(f"   {name} 起好（pid {_bg(script, '--registry-host', H)}）")

    # ⑤ 注册自己进卡（role 由 ② 写的身份标记带出）
    print("⑤ 注册本机进卡 …")
    _fg("register_node.py", "--registry-host", H)

    # ⑥ Tailscale（脚本下载+装；只有「登你自己账号」要你）
    print("⑥ Tailscale（远程接入；脚本帮你下载+装，登录登你自己账号）：")
    ts_ok = _tailscale(do_install=True)
    if ts_ok:
        # ⑤ 注册时 Tailscale 还没装，卡里 tailscale_ip 是空的 → 上线后重注册一次刷进卡（幂等覆盖）
        print("   把 Tailscale IP 刷进节点卡 …")
        _fg("register_node.py", "--registry-host", H)

    # ⑦ 自检 + 如实报告（次只验注册中心在听；主走 healthcheck 全套。缺一就不报成功、exit 1）
    print("\n⑦ 自检：")
    ssh_ok = args.skip_ssh or _port_up("127.0.0.1", 22, tries=2)   # --skip-ssh 时不强求
    if sec:
        core_ok = _port_up("127.0.0.1", P, tries=2)
        print("   注册中心 " + ("✅ 在听" if core_ok else "❌ 没起"))
    else:
        core_ok = _fg("healthcheck.py") == 0      # 0 = 注册中心/大屏/巡检 全在
    _verdict(core_ok, ssh_ok, ts_ok, sec)


if __name__ == "__main__":
    main()
