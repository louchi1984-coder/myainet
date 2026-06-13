#!/usr/bin/env python3
"""
myaiweb: dispatch.py
派任务 —— 把主控（AI）选好的「在某台节点上跑某条命令」真正执行，并记进 注册中心 task:*。

定位：它是「手」，不是「脑」。挑哪台、写什么命令、是直接 shell 还是委托给那台的 agent——
都是主控 AI 按事实卡判断好了的；本脚本只负责【跑 + 记账 + 回显】，大屏的任务栏就读这些 task:*。

  命令整体加引号（跟 ssh host "cmd" 一个规矩，免得 -h/-c 被当选项、引号被拆）：
  · 直接命令：     dispatch.py --registry-host <主IP> --node nas-box  "df -h /data"
  · 委托给 agent： dispatch.py --registry-host <主IP> --node mac  "claude -p '下载并装好 ollama'"
                  （委托=命令本身就写成那个 agent 的非交互调用，脑子是远端 agent，dispatch 照跑）
  · 长任务甩后台： dispatch.py --registry-host <主IP> --node gpu-rig --detach --name 夜训  "python train.py"
  · 起名/超时：    --name <任务名>（= task:<名>）  --timeout <秒，默认 300，同步用>
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import base64
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
try:
    from registry_client import rset, rget, rkeys
except ImportError:
    print("❌ 找不到 registry_client.py，无法连接 注册中心", file=sys.stderr)
    sys.exit(1)
try:
    from identity import is_hub_like as _is_hub_like   # 角色判定中英都认（hub/建网）
except Exception:
    def _is_hub_like(role):
        r = (role or "").lower()
        return "建网" in r or "hub" in r


def _local_ids():
    ids = {socket.gethostname().lower()}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ids.add(s.getsockname()[0])
    except Exception:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ids.add(info[4][0])
    except Exception:
        pass
    return ids


def reachable(host, port, timeout=3):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _match(c, want, node):
    return c.get("hostname", "").lower() == want or c.get("network", {}).get("lan_ip", "") == node


def _default_registry_host() -> str | None:
    """没传 --registry-host 时，从本机身份标记的 central 读建网机地址 —— agent 少拼一段、也不会填错。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from identity import read_identity
        c = (read_identity() or {}).get("central")
        return c if c and str(c).strip().lower() not in ("", "none") else None
    except Exception:
        return None


def _all_cards(host, port):
    """拿全部卡（注册中心够得着用它，够不着回退本地镜像）+ 来源标记，供精确/模糊匹配共用。"""
    if reachable(host, port):
        out = []
        for k in rkeys(host, port, "node:*"):
            v = rget(host, port, k)
            if not v:
                continue
            try:
                out.append(json.loads(v))
            except Exception:
                continue
        return out, "registry"
    try:
        from registry_cache import load_cards
        return list(load_cards()), "mirror"
    except Exception:
        return [], "none"


def _has_gpu(card) -> bool:
    """卡里有没有【可调度独显】（NVIDIA/AMD 独显）。排除空/none，也排除 Apple M 系等集成 GPU——
    `--node gpu` 本意是「派给有独显能跑 CUDA/训练的机器」，Mac 的集成显卡不该被算进来。"""
    g = str((card.get("hardware") or {}).get("gpu") or "").strip().lower()
    if not g or g.startswith(("none", "no ", "无", "-")):
        return False
    if any(k in g for k in ("apple", "m1", "m2", "m3", "m4", "integrated", "集成", "intel")):
        return False
    return any(k in g for k in ("nvidia", "geforce", "rtx", "gtx", "tesla", "quadro", "amd", "radeon", "cuda"))


def _fuzzy_match(cards, node):
    """模糊匹配：① gpu/显卡 → 真有 GPU 的机器（按卡判，不靠字面）② 机名子串 / 硬件型号(如 2070) / 角色关键词。"""
    want = node.lower()
    hits, seen = [], set()
    for c in cards:
        hn = (c.get("hostname") or "").lower()
        hw = " ".join(str(v) for v in (c.get("hardware") or {}).values()).lower()
        role = (c.get("primary_role") or c.get("role") or "").lower()
        ok = False
        if want in ("gpu", "显卡"):
            ok = _has_gpu(c)
        else:
            ok = (want in hn) or (want in hw) or (want in role)
        if ok and hn not in seen:
            seen.add(hn)
            hits.append(c)
    return hits


def resolve_node(host, port, node):
    """先连建网机 注册中心 找卡；够不到 → 回退主控本地镜像。先精确（机名/IP），再模糊（子串/GPU/角色）。
    模糊命中多台 → 返回 ('AMBIGUOUS', [候选名]) 让调用方明确，绝不替 agent 瞎猜。"""
    cards, src = _all_cards(host, port)
    want = node.lower()
    for c in cards:                                   # ① 精确：机名 / IP
        if _match(c, want, node):
            if src == "mirror":
                print("ℹ️ 建网机 注册中心 够不到，改用主控本地镜像解析节点（命令照跑，任务记账会缺）", file=sys.stderr)
            return c
    fuzzy = _fuzzy_match(cards, node)                 # ② 模糊：子串 / GPU / 角色
    if len(fuzzy) == 1:
        print(f"ℹ️ 模糊匹配「{node}」→ {fuzzy[0].get('hostname')}", file=sys.stderr)
        return fuzzy[0]
    if len(fuzzy) > 1:
        return "AMBIGUOUS", [c.get("hostname") for c in fuzzy]
    return None


def _delegate_command(card, goal):
    """委托：按目标节点卡的 agents 自动挑一个（codex→claude→opencode，只在它装了的里挑），
    包成非交互调用 —— agent 只说「委托 X 做 Y」，不用关心那台装了哪个 agent。"""
    avail = set()
    for a in (card.get("agents") or []):
        name = str(a).split(":", 1)[0].strip().lower()
        ok = ":" not in str(a) or str(a).split(":", 1)[1].strip().lower() in ("yes", "true", "1")
        if ok:
            avail.add(name)
    for name in ("codex", "claude", "opencode"):
        if name in avail:
            g = goal.replace('"', "'")               # 目标里的双引号降级成单引号，避免破坏外层包裹
            if name == "codex":
                return f'codex exec --skip-git-repo-check "{g}"', name
            if name == "claude":
                return f'claude -p "{g}"', name
            return f'opencode run "{g}"', name
    return None, None
    return None


def ssh_target(card):
    net = card.get("network", {})
    s = net.get("ssh", "")
    if s.startswith("ssh "):
        return s[4:].strip()
    return net.get("lan_ip", "") or None


_JUMP = None   # 异地控制：经建网机跳到节点（main 里探明 LAN 不通时设置）


def _ssh_base(target):
    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]
    if _JUMP:
        cmd += ["-J", _JUMP]   # 主控→建网机→节点（SKILL.md 跳转铁律的代码落地；节点不需要 Tailscale）
    return cmd + [target]


def _hub_jump_target(H, P, card):
    """节点 LAN 直连不通时，找它的建网机当跳板。优先 hub 的 Tailscale 地址（异地唯一够得到的路）。"""
    hub_name = card.get("belongs_to", "")
    hub = resolve_node(H, P, hub_name) if hub_name else None
    if not hub and reachable(H, P):      # 没填归属 → 找第一张建网机卡
        for k in rkeys(H, P, "node:*"):
            v = rget(H, P, k)
            try:
                c = json.loads(v) if v else None
            except Exception:
                c = None
            if c and _is_hub_like(c.get("role") or c.get("primary_role") or ""):
                hub = c
                break
    if not hub or hub.get("hostname") == card.get("hostname"):
        return None
    net = hub.get("network", {})
    s = net.get("ssh_tailscale") or net.get("ssh") or ""
    if s.startswith("ssh "):
        return s[4:].strip()
    return net.get("tailscale_ip") or net.get("lan_ip") or None


def _win_remote_cmd(command, shell):
    """把要在 Windows 节点跑的命令包成「SSH argv 上只有 ASCII」的形式——根治长中文经
    cmd.exe GBK 控制台被截断（真实事故）。argv 走 base64，远端解回 UTF-8 再执行。
      · bash(Git Bash)：base64 → base64 -d → bash，bash 全程 UTF-8 无损
      · 其余(cmd/powershell/未知)：PowerShell -EncodedCommand（UTF-16LE base64，专治此事、长度无限、
        codepage 免疫）。cmd 登录 shell 也照样能起 powershell，故未知一律走这条最稳。
    输出端先把 OutputEncoding 设 UTF-8，回程 stdout 干净（我们按 utf-8 解）。"""
    if shell == "bash":
        b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
        return f"echo {b64} | base64 -d | bash"
    # exit $LASTEXITCODE：把原生程序(nvidia-smi/python…)的真实退出码透回，否则 powershell 永远报 0
    inner = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command + "; exit $LASTEXITCODE"
    b64 = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {b64}"


def run_sync(target, is_local, command, timeout, win_shell=None):
    """同步跑一条命令，返回 (exit_code, 合并输出)。本机 shell / 远端 SSH。
    win_shell 非空=目标是 Windows 节点：命令走 base64 包装，绕开 GBK 控制台截断。"""
    try:
        if is_local:
            r = subprocess.run(command, shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
        else:
            remote = _win_remote_cmd(command, win_shell) if win_shell else command
            r = subprocess.run(_ssh_base(target) + [remote], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"[超时 >{timeout}s]"
    except Exception as e:
        return 255, f"[执行出错] {e}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if err:
        out = (out + "\n[stderr] " + err) if out else ("[stderr] " + err)
    return r.returncode, out


def run_detach_posix(target, is_local, command, logpath):
    """posix 长任务：后台 nohup 起，返回 pid（拿不到返回空）。"""
    inner = f"nohup sh -c {shlex.quote(command)} > {logpath} 2>&1 & echo $!"
    try:
        if is_local:
            r = subprocess.run(inner, shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=15)
        else:
            r = subprocess.run(_ssh_base(target) + [inner], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=15)
    except Exception as e:
        return "", f"[起后台失败] {e}"
    pid = ""
    for tok in (r.stdout or "").split():
        if tok.isdigit():
            pid = tok
    return pid, ""


def run_detach_win(target, is_local, command, tid):
    """Windows 长任务甩后台：用计划任务(schtasks)拉起，与 SSH 会话彻底解耦。
    根因：Windows OpenSSH 断开会杀子进程树，Start-Process 的子进程活不过会话（真实事故）；
    schtasks 拉的进程归任务计划服务管，SSH 断了照活。
    盯法：计划任务跑 myaiweb-<tid>.ps1（运行进程命令行里带这串）→ 登记 check=match:myaiweb-<tid>，
    patrol 用 Win32_Process.CommandLine -like 盯；跑完 .ps1 自删计划任务，不留垃圾。
    返回 (marker, '') 成功 / ('', err) 失败。"""
    tn = f"myaiweb-{tid}"
    # .ps1 内容整体在 Python 里拼好（日志重定向 + 跑完自删任务），再整体 base64 —— 引导只管解码写盘，少一层嵌套引号
    ps1 = (
        "& {\n" + command + "\n} *> \"$env:TEMP\\" + tn + ".log\" 2>&1\n"
        + f'schtasks /delete /tn "{tn}" /f *> $null\n'
    )
    ps1_b64 = base64.b64encode(ps1.encode("utf-8")).decode("ascii")
    boot = (
        f'$p="$env:TEMP\\{tn}.ps1";'
        f"[IO.File]::WriteAllText($p,[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{ps1_b64}')),[Text.Encoding]::UTF8);"
        f'schtasks /create /tn "{tn}" /tr "powershell -NoProfile -ExecutionPolicy Bypass -File `"$p`"" /sc once /st 00:00 /f *>$null;'
        f'schtasks /run /tn "{tn}" *>$null;'
        f"if($?){{'OK'}}else{{'FAIL'}}"
    )
    b64 = base64.b64encode(boot.encode("utf-16-le")).decode("ascii")
    remote = f"powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand {b64}"
    try:
        if is_local:
            r = subprocess.run(remote, shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
        else:
            r = subprocess.run(_ssh_base(target) + [remote], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
    except Exception as e:
        return "", f"[起计划任务失败] {e}"
    if "OK" in (r.stdout or ""):
        return f"myaiweb-{tid}", ""
    return "", (r.stderr or r.stdout or "schtasks 未确认成功").strip()[:200]


def _ws_cd_prefix(shell, wd):
    """按工作区自报的 SSH 登录 shell 出 cd 前缀——登录 shell 可能是 cmd / powershell / Git-bash，
    cd 语法和路径写法各不同，绝不假设。"""
    if shell == "powershell":
        return f'Set-Location "{wd}"; '
    if shell == "bash":
        # Git-bash/MSYS：Windows 盘路径 D:\ws → /d/ws（盘符小写、反斜杠转正斜杠）；本就是 posix 路径则原样
        if len(wd) >= 2 and wd[1] == ":" and wd[0].isalpha():
            wd = "/" + wd[0].lower() + "/" + wd[2:].lstrip("\\/").replace("\\", "/")
        return f'cd "{wd}" && '
    return f'cd /d "{wd}" && '                 # cmd（含未知/默认；OpenSSH 出厂默认就是 cmd）


def main():
    p = argparse.ArgumentParser(description="myaiweb: 派任务（跑 + 记 task:*）")
    p.add_argument("--registry-host", default=None,
                   help="主建网机 注册中心 地址；不填=从本机身份标记的 central 读（agent 不用每次拼）")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--node", required=True,
                   help="在哪台节点上跑：机名/IP 精确，或子串/GPU/角色模糊（如 gpu-box / 2070 / gpu）")
    p.add_argument("--name", default=None, help="任务名（= task:<名>，重名即覆盖；默认按 节点+时间 生成）")
    p.add_argument("--detach", action="store_true", help="长任务甩后台（posix=nohup / Windows=计划任务，与 SSH 解耦），自动登记给 patrol 盯死活")
    p.add_argument("--workspace", action="store_true", help="在节点工作区 work_dir 里跑（读卡按 os 自动 cd，省得 agent 手写跨 OS 路径）")
    p.add_argument("--delegate", default=None, metavar="目标",
                   help="委托模式：把模糊目标甩给节点本地 agent（自动按卡挑 codex/claude/opencode 并包非交互调用）。"
                        "与位置参数 command 二选一")
    p.add_argument("--timeout", type=int, default=300, help="同步执行超时秒数（默认 300）")
    p.add_argument("--check", action="store_true",
                   help="只判节点死活（经 SSH 实连，不跑命令、不记账）——判节点在不在线就用这个，"
                        "别自己跑 ping（ICMP 常被防火墙拦，会把活机器误判成 down）")
    p.add_argument("command", nargs="?", default=None,
                   help="要跑的具体命令（直连模式）；委托用 --delegate 替代")
    args = p.parse_args()

    if not args.check and not args.command and not args.delegate:
        print("❌ 要么给具体命令（直连），要么 --delegate \"目标\"（委托），要么 --check（只判死活）。")
        sys.exit(2)

    H = args.registry_host or _default_registry_host()
    if not H:
        print("❌ 没传 --registry-host，本机身份标记里也没 central（建网机地址）。"
              "先 setup_control.py --central <建网机> 配好身份，或显式传 --registry-host。")
        sys.exit(2)
    P = args.registry_port

    resolved = resolve_node(H, P, args.node)
    if isinstance(resolved, tuple) and resolved[0] == "AMBIGUOUS":
        print(f"❌ 「{args.node}」模糊匹配到多台：{', '.join(resolved[1])}。请用更精确的名字。")
        sys.exit(2)
    card = resolved
    if not card:
        print(f"❌ 注册表里找不到节点 {args.node}（先确认它注册过、名字/IP/关键词对得上）")
        sys.exit(1)

    # 委托模式：按目标节点卡挑 agent + 包非交互调用（命令随后照常走直连那套：记账/GBK包装/跳转/detach）
    if args.delegate and not args.check:
        command, picked = _delegate_command(card, args.delegate)
        if not command:
            print(f"❌ {card.get('hostname')} 卡里没装 codex/claude/opencode，没法委托。先在它上面装并登录一个 agent。")
            sys.exit(1)
        print(f"ℹ️ 委托 {card.get('hostname')} 的 {picked}：{args.delegate}")
    else:
        command = args.command

    hostname = card.get("hostname", args.node)
    is_win = "windows" in (card.get("hardware", {}).get("os", "") or "").lower()
    local_ids = _local_ids()
    is_local = hostname.lower() in local_ids or card.get("network", {}).get("lan_ip", "") in local_ids
    target = None if is_local else ssh_target(card)
    if not is_local and not target:
        print(f"❌ 拿不到 {hostname} 的 SSH 目标（注册卡里没 ssh/lan_ip）")
        sys.exit(1)

    # 异地控制：节点 LAN 直连不通（控制方不在它那个网）→ 先试节点自己的 Tailscale，再不行经建网机跳转
    if not is_local:
        net = card.get("network", {})
        ip = net.get("lan_ip", "")
        if ip and not reachable(ip, 22, timeout=2):
            ts_ip = net.get("tailscale_ip", "")
            if ts_ip and reachable(ts_ip, 22, timeout=2):
                s = net.get("ssh_tailscale", "")
                target = s[4:].strip() if s.startswith("ssh ") else ts_ip
                print(f"ℹ️ LAN 直连不通 → 走节点自己的 Tailscale（{target}）")
            else:
                jump = _hub_jump_target(H, P, card)
                if jump:
                    global _JUMP
                    _JUMP = jump
                    print(f"ℹ️ LAN 直连不通 → 经建网机跳转（-J {jump}）")

    # Windows 执行 shell：dispatch 一律经 bash(Git Bash) 或 powershell 跑——cmd/未知都规整成 powershell
    # （它在每台 Windows 都在；命令走 base64 包装绕开 GBK 截断，见 _win_remote_cmd）。
    win_shell = None
    if is_win:
        detected = (card.get("workspace") or {}).get("shell") or "cmd"
        win_shell = "bash" if detected == "bash" else "powershell"

    # ── --check：判死活，唯一权威方式。判据=经我实际控制它的那条 SSH 路实连，绝不看 ICMP ──
    if args.check:
        if is_local:
            print(f"✅ {hostname} = 本机（控制方自己），必然在线。")
            return
        where = f"-J {_JUMP} → {target}" if _JUMP else target
        code, out = run_sync(target, is_local, "echo MYAIWEB_PROBE_OK", 12, win_shell=win_shell)
        if code == 0 and "MYAIWEB_PROBE_OK" in (out or ""):
            print(f"✅ {hostname} 活着、够得到 —— 经 SSH 实连成功（{where}）。")
            print("   判据 = SSH/22 口实连，非 ICMP。ping 不通 ≠ down（Windows 防火墙默认拦 ICMP）。")
            return
        # 失败要分清「节点的事」vs「我的位置/路径的事」：异地纯节点经建网机跳板够到，
        # 跳板失败时先探建网机本身——建网机活=节点真有问题；建网机也够不到=路径断了，从这里根本判不了节点死活。
        if _JUMP:
            hub_ip = _JUMP.split("@")[-1]
            hub_up = reachable(hub_ip, 22, timeout=3) or reachable(H, P, timeout=3)
            if hub_up:
                print(f"❌ {hostname} 大概率真 down —— 建网机({hub_ip})活着、但经它跳板够不到这台节点（code={code}）。")
                print("   多半是节点本身：关机 / 断网 / SSH 没起。要救得有人在它那个 LAN 现场看，或它自己回来。")
            else:
                print(f"❓ 判不了 {hostname} 死活 —— 连建网机({hub_ip})本身都够不到（code={code}）。")
                print("   这是【你的位置/路径】问题，不是节点的事：你不在节点的 LAN 里，纯 LAN 节点只能经建网机跳板够到。")
                print("   先把建网机弄通（它在不在 Tailscale 上？Tailscale 通不通？），再来判节点。别归因成「节点硬 down」。")
        else:
            print(f"❌ {hostname} 够不到 —— LAN 直连 / Tailscale 实连都失败（code={code}）。")
            if out:
                print(f"   {out[:120]}")
            print("   可能：真 down / SSH 没起。")
        print("   ⚠️ 以上是判据（SSH 实连），别再自己跑 ping——ICMP 常被防火墙拦，会把活机器误判成硬 down。")
        sys.exit(1)

    if args.workspace:                       # 在工作区里跑：读卡 work_dir + 自报 shell，出对应 cd（agent 不手写跨 OS 路径/shell 语法）
        ws = card.get("workspace") or {}
        wd = ws.get("work_dir")
        if wd:
            cd_shell = win_shell if is_win else "bash"   # 和执行 shell 一致：cmd 节点经 powershell 跑→cd 也出 powershell 语法
            command = _ws_cd_prefix(cd_shell, wd) + command
        else:
            print(f"⚠️ {hostname} 卡里没工作区（先 setup_workspace.py 设好自报）——不 cd、直接跑。")

    tid = args.name or f"{hostname}-{int(time.time())}"
    key = f"task:{tid}"
    now = int(time.time())
    by = socket.gethostname()

    base = {
        "id": tid, "node": hostname, "cmd": command,
        "description": command[:80], "by": by,
        "started_at": now, "status": "running",
        "source": "dispatch",                # task:* 三种来源（dispatch/watch/report）写法不同，标上省得消费端猜
    }
    rset(H, P, key, json.dumps(base, ensure_ascii=False))   # 先记成 running，大屏立刻看得到
    where = "本机" if is_local else f"SSH→{target}"
    print(f"🚀 派任务 [{tid}] @ {hostname}（{where}）：{command}")

    # ── 长任务：甩后台 + 交给 patrol 盯 ──────────────────────────────────────
    if args.detach:
        if is_win:
            # Windows：schtasks 拉起，与 SSH 解耦（断开不被杀）；patrol 用 match 盯进程命令行里的 myaiweb-<tid>
            marker, err = run_detach_win(target, is_local, command, tid)
            if not marker:
                print(f"❌ 起后台失败：{err}")
                base["status"] = "failed"; base["error"] = err or "schtasks failed"
                rset(H, P, key, json.dumps(base, ensure_ascii=False))
                sys.exit(1)
            base.update({"watch": True, "check": {"type": "match", "value": marker},
                         "log": f"%TEMP%\\{marker}.log", "last_seen": int(time.time())})
            rset(H, P, key, json.dumps(base, ensure_ascii=False))
            print(f"   ✅ 已用计划任务后台启动（{marker}，与 SSH 解耦）；日志 节点 %TEMP%\\{marker}.log")
            print(f"   建网机巡检会盯它死活；查看：watch_job.py --registry-host {H} --list")
            return
        logpath = f"/tmp/myaiweb-{tid}.log"
        pid, err = run_detach_posix(target, is_local, command, logpath)
        if not pid:
            print(f"❌ 起后台失败：{err}")
            base["status"] = "failed"; base["error"] = err or "no pid"
            rset(H, P, key, json.dumps(base, ensure_ascii=False))
            sys.exit(1)
        base.update({"watch": True, "check": {"type": "pid", "value": int(pid)},
                     "log": logpath, "last_seen": int(time.time())})
        rset(H, P, key, json.dumps(base, ensure_ascii=False))
        print(f"   ✅ 已后台启动 pid={pid}，日志 {logpath}（节点上）")
        print(f"   建网机巡检会盯它死活；查看：watch_job.py --registry-host {H} --list")
        return

    # ── 同步：跑完拿结果 ────────────────────────────────────────────────────
    code, output = run_sync(target, is_local, command, args.timeout, win_shell=win_shell)
    base.update({
        "status": "done" if code == 0 else "failed",
        "exit_code": code,
        "output": output[-4000:],          # 大屏只留尾部，别撑爆 注册中心
        "finished_at": int(time.time()),
    })
    rset(H, P, key, json.dumps(base, ensure_ascii=False))

    print("─" * 60)
    print(output or "(无输出)")
    print("─" * 60)
    print(f"{'✅ done' if code == 0 else '❌ failed'}  exit={code}   task:{tid} 已记账（大屏任务栏可见）")
    sys.exit(0 if code == 0 else 1)


if __name__ == "__main__":
    main()
