#!/usr/bin/env python3
"""
myainet: patrol.py
巡检推送 —— 在「次建网机」上跑：ping 本 LAN 够得着的节点，把在线状态 push 到主建网机的 注册中心。

为什么需要它：主建网机 ping 不到别的 LAN 的私网 IP（192.168.x），所以别的 LAN 里
节点的在线状态，只能由那个 LAN 自己的 hub 本地探完、推上来（pull→push）。

规则：只推「自己够得着的」节点（ping 通才推）——够不到的【不写】，交给那个 LAN 的 hub。
      silence ≠ offline：主建网机靠状态过期（stale）判离线，不会因为某个 hub 没推就误标离线。

用法（在次建网机上，--registry-host 指主建网机的 Tailscale IP）：
  python3 patrol.py --registry-host 100.x.x.x                 # 默认每 30s 推一次
  python3 patrol.py --registry-host 100.x.x.x --interval 20 --hub lan-office
  python3 patrol.py --registry-host 100.x.x.x --once          # 只探一遍就退出（调试）
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
if sys.stdout is None:                      # pythonw 后台启动无 stdout → print 会崩，兜成 devnull
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
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


def ping(ip: str, timeout: float = 1.5):
    """ping 一台：通返回延迟 ms（解析不到具体值返回 0.0），不通返回 None。三平台通用。"""
    if not ip or ip in ("unknown", ""):
        return None
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    elif sys.platform == "darwin":
        cmd = ["ping", "-c", "1", "-t", str(max(1, int(timeout))), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout + 2)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        for marker in ("time=", "time<", "时间=", "时间<"):
            if marker in line:
                raw = line.split(marker)[1].split()[0].lower().rstrip("ms").strip()
                try:
                    return round(float(raw), 1)
                except ValueError:
                    return 0.5
    return 0.0   # 通了但没解析到具体延迟


def tcp_ping(ip: str, port: int = 22, timeout: float = 1.5):
    """ICMP 被节点防火墙拦时的兜底探活（Win 默认拦 ping 但 SSH 通）：TCP 摸 22 口，连上=在线。"""
    if not ip or ip in ("unknown", ""):
        return None
    try:
        t0 = time.time()
        socket.create_connection((ip, port), timeout=timeout).close()
        return round((time.time() - t0) * 1000, 1)
    except OSError:
        return None


def read_nodes(host, port):
    nodes = []
    for key in rkeys(host, port, "node:*"):
        val = rget(host, port, key)
        if not val:
            continue
        try:
            nodes.append(json.loads(val))
        except Exception:
            continue
    return nodes


def _local_ids():
    """本机的标识集合（hostname + 各 IP），用来判断「这活儿就在本机」→ 直接本地查、不用 SSH。"""
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


def _ssh_target(card):
    """从节点卡取 SSH 目标 user@ip（卡里存的是 'ssh user@ip'）。"""
    if not card:
        return None
    net = card.get("network", {})
    s = net.get("ssh", "")
    if s.startswith("ssh "):
        return s[4:].strip()
    return net.get("lan_ip", "") or None


def _ps_encoded(script):
    """把一段 PowerShell 包成 -EncodedCommand（base64/UTF-16LE）——彻底绕开 ssh→cmd→powershell 的转义地狱。"""
    b = base64.b64encode(script.encode("utf-16-le")).decode()
    return f"powershell -NoProfile -EncodedCommand {b}"


def _build_check(ctype, cval, is_win):
    """生成「还在不在」的检查命令：活着输出 ALIVE、没了输出 GONE。区分 posix / Windows。"""
    if ctype == "pid":
        try:
            pid = int(cval)
        except (TypeError, ValueError):
            return None
        if is_win:
            return _ps_encoded("if (Get-Process -Id %d -ErrorAction SilentlyContinue)"
                               "{'ALIVE'}else{'GONE'}" % pid)
        return "kill -0 %d 2>/dev/null && echo ALIVE || echo GONE" % pid
    if ctype == "match":
        pat = str(cval)
        if is_win:
            p = pat.replace("'", "''")   # PowerShell 单引号转义：'' 表示一个 '
            return _ps_encoded("if (Get-CimInstance Win32_Process | Where-Object "
                               "{$_.CommandLine -like '*%s*'}){'ALIVE'}else{'GONE'}" % p)
        # pgrep -f 会把「pgrep -f <pat>」这条检查命令本身也匹配上（它的 cmdline 含 pat）→ 永远 ALIVE。
        # 首字符套 []：regex `[t]rain` 匹配进程「train」，但不匹配检查命令里的字面串 `[t]rain`，规避自匹配。
        bp = f"[{pat[0]}]{pat[1:]}" if pat and pat[0].isalnum() else pat
        return "pgrep -f %s >/dev/null 2>&1 && echo ALIVE || echo GONE" % shlex.quote(bp)
    return None


def _check_alive(job, card, is_local):
    """查一个活儿还在不在：在→True，确实没了→False，查不出（SSH 不通/没明确结果）→None（不下结论）。
    job 带 container 时：不管宿主什么 OS，都 docker exec 进【Linux 容器】里查（posix）——
    因为容器进程在宿主进程表上看不见（Docker Desktop 下尤其，跑在 WSL2 VM 里）；
    宿主是 Windows 就把整条 docker 命令 PowerShell 编码（绕开 cmd/ssh 的引号坑，跟现有路径一致）。"""
    chk = job.get("check") or {}
    is_win = (sys.platform == "win32") if is_local \
        else "windows" in (card.get("hardware", {}).get("os", "") if card else "").lower()
    container = job.get("container")
    if container:
        posix = _build_check(chk.get("type"), chk.get("value"), is_win=False)  # 容器内永远 posix
        if posix is None:
            return None
        # 把 posix 检查 base64 喂进容器里解码执行 —— 一举两得：
        # ① 跨「host shell → docker → 容器 sh」三层引号（尤其 Windows 的 cmd/PowerShell 规则全不同）全绕开，
        #    b64 只含安全字符；② pattern 藏在 b64 里、不出现在检查命令的 cmdline 上 → pgrep 不会自匹配。
        b = base64.b64encode(posix.encode("utf-8")).decode()
        # 解码用 base64(coreutils/busybox 都有)，没有再退 python3 —— 别把容器盯守绑死在 python3 上
        dc = (f"docker exec {container} sh -c "
              f"'echo {b} | (base64 -d 2>/dev/null || python3 -m base64 -d) | sh'")
        inner = _ps_encoded(dc) if is_win else dc
    else:
        inner = _build_check(chk.get("type"), chk.get("value"), is_win)
        if inner is None:
            return None
    try:
        if is_local:
            r = subprocess.run(inner, shell=True, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=8)
        else:
            target = _ssh_target(card)
            if not target:
                return None
            ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=4",
                       "-o", "BatchMode=yes", target, inner]
            r = subprocess.run(ssh_cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=12)
    except Exception:
        return None
    out = r.stdout or ""
    if "ALIVE" in out:
        return True
    if "GONE" in out:
        return False
    return None   # 没拿到明确结果 → 别瞎判 stopped


def check_jobs(host, port, hub, reachable, local_ids):
    """盯 task:* 里登记在册、还在 running 的活儿——只查【我够得着的节点】上的，更新死活。"""
    checked = 0
    for jkey in rkeys(host, port, "task:*"):
        val = rget(host, port, jkey)
        if not val:
            continue
        try:
            job = json.loads(val)
        except Exception:
            continue
        if not job.get("watch") or job.get("status") != "running":
            continue
        key = (job.get("node") or "").lower()
        is_local = key in local_ids
        card = reachable.get(key)
        if not is_local and not card:
            continue   # 这活儿的节点不在我够得着的范围 → 交给那个 LAN 的 hub
        alive = _check_alive(job, card, is_local)
        if alive is None:
            continue   # 查不出 → 保持原状，不误判
        now = int(time.time())
        job["last_seen"] = now
        job["hub"] = hub
        if not alive:
            job["status"] = "stopped"
            job["stopped_at"] = now
        rset(host, port, jkey, json.dumps(job, ensure_ascii=False))
        checked += 1
    return checked


def _lan_ip():
    """本机 LAN 出站 IP——让节点拿这个当 --registry-host（建网机=它的 注册中心，本 LAN 节点都够得到）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def _diff_card(old, new):
    """新旧卡关键字段 diff → 「人话变化」列表。只报留痕的【集合变化】（装/卸 工具·agent·模型），
    盘/内存这类自然波动的不报、免刷屏。这是「另一个任务偷偷改了机器」的被动兜底
    （抓得到留痕的；装了又退的 transient 抓不到，那个靠 actor 自己 report）。"""
    def names(lst):
        out = set()
        for x in (lst or []):
            if isinstance(x, str):
                out.add(x.split(":")[0])
            elif isinstance(x, dict):
                out.add(x.get("name", ""))
        return out - {""}
    ch = []
    for o, n, label in (
        (set(old.get("cli") or []), set(new.get("cli") or []), "工具"),
        (names(old.get("agents")),  names(new.get("agents")),  "agent"),
        (names(old.get("models")),  names(new.get("models")),  "模型"),
    ):
        ch += [f"+{label} {x}" for x in sorted(n - o)]
        ch += [f"-{label} {x}" for x in sorted(o - n)]
    return ch


def refresh_nodes(reachable, local_ids, hub_lan_ip, host, port):
    """周期性让够得着的节点重新注册，刷新它们的卡（防卡烂：装卸工具/盘/link/problems 都会变）。
    节点指向 hub 的 LAN IP（它够得到的中央或桥）。本机不这样刷（建网机自己有 dashboard 自刷）。
    顺带【漂移检测】：重注册后读新卡跟旧卡 diff，留痕的集合变了就写一条 note（大屏可见）。"""
    done = set()
    n = 0
    for card in reachable.values():
        hostname = card.get("hostname", "")
        h = hostname.lower()
        if not hostname or h in done or h in local_ids:
            continue
        done.add(h)
        target = _ssh_target(card)
        if not target:
            continue
        # 节点上 skill 脚本的真实目录从卡读（注册时自报）；老卡没有就退回 ~/myainet（向后兼容）
        sdir = card.get("scripts_dir") or "~/myainet/scripts"
        reg = f'"{sdir}/register_node.py" --registry-host {hub_lan_ip} --node-name {hostname}'
        cmd = f"python {reg} || python3 {reg}"   # python||python3 兜 Win/posix
        ssh_cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=8",
                   "-o", "BatchMode=yes", target, cmd]
        try:
            subprocess.run(ssh_cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=45)
            n += 1
        except Exception:
            continue
        try:                                       # 漂移检测：读新卡、跟旧卡 diff、变了写 note
            nv = rget(host, port, f"node:{hostname}")
            changes = _diff_card(card, json.loads(nv)) if nv else []
            if changes:
                now = int(time.time())
                note = {"id": f"{hostname}-drift", "node": hostname, "status": "note",
                        "message": "🔀 卡变了：" + "；".join(changes[:8]) + (f"…共 {len(changes)} 项" if len(changes) > 8 else ""),
                        "last_seen": now, "ts": now, "hub": "patrol-drift"}
                rset(host, port, f"task:{hostname}-drift",
                     json.dumps(note, ensure_ascii=False), ttl=7 * 86400)
        except Exception:
            pass
    return n


def sweep(host, port, hub, ttl):
    """探一遍：① ping 通的节点 push 在线状态；② 顺带查我够得着的节点上登记的活儿。"""
    nodes = read_nodes(host, port)
    local_ids = _local_ids()
    reachable = {}   # hostname/ip（小写）-> 节点卡，供「盯活儿」找 SSH 目标
    pushed = 0
    for node in nodes:
        hostname = node.get("hostname", "")
        ip = node.get("network", {}).get("lan_ip", "")
        if not hostname or not ip:
            continue
        is_local = hostname.lower() in local_ids or ip in local_ids
        ms = 0.0 if is_local else ping(ip)
        if ms is None:
            ms = tcp_ping(ip)   # Win 节点防火墙常拦 ICMP（ping 不通但 SSH 通）→ 摸 22 口兜底
        if ms is None:
            continue   # 够不到 → 不是我这个 LAN 的；silence≠offline，留给那个 LAN 的 hub
        status = {
            "hostname":   hostname,
            "online":     True,
            "latency_ms": ms,
            "last_seen":  int(time.time()),
            "hub":        hub,
            "lan_ip":     ip,
        }
        rset(host, port, f"status:{hostname}", json.dumps(status, ensure_ascii=False), ttl=ttl)
        pushed += 1
        reachable[hostname.lower()] = node
        reachable[ip] = node
    checked = check_jobs(host, port, hub, reachable, local_ids)
    return len(nodes), pushed, checked, reachable


def main():
    p = argparse.ArgumentParser(description="myainet: 建网机巡检推送（主/单局域网那台跑）")
    p.add_argument("--registry-host", required=True, help="本机 注册中心 地址（主/单局域网那台填 127.0.0.1）")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--interval", type=int, default=30, help="多少秒推一次（默认 30）")
    p.add_argument("--hub", default=None, help="本 hub 名（默认本机 hostname）")
    p.add_argument("--once", action="store_true", help="只探一遍就退出（调试）")
    p.add_argument("--refresh-every", type=int, default=120,
                   help="每多少轮触发一次『够得着的节点重注册』刷新卡（默认 120 轮≈1 小时；0=关）")
    args = p.parse_args()

    hub = args.hub or socket.gethostname()
    # 状态键活得比一个周期长，几次漏推不闪断；最终靠 dashboard 端的新鲜度判离线。
    ttl = max(args.interval * 5, 120)

    print(f"🩺 myainet 巡检推送 → 注册中心 {args.registry_host}:{args.registry_port}  hub={hub}  "
          f"每 {args.interval}s{'（单次）' if args.once else ''}")

    hub_lan = _lan_ip()
    round_n = 0
    while True:
        round_n += 1
        try:
            total, pushed, checked, reachable = sweep(args.registry_host, args.registry_port, hub, ttl)
            line = f"   [{time.strftime('%H:%M:%S')}] 探活 {total} 台→在线推送 {pushed}；盯活儿 {checked} 个"
            # 补钥匙不再归巡检管：registry_server 收到 pubkey:* 即事件驱动装门（2026-06-12 真 win 合成验证过），
            # 这里原来每轮跑一遍 keysync.install 的兜底已彻底冗余，拆。
            if args.refresh_every and round_n % args.refresh_every == 0:
                refreshed = refresh_nodes(reachable, _local_ids(), hub_lan, args.registry_host, args.registry_port)
                line += f"；刷新 {refreshed} 台卡"
            print(line)
        except Exception as e:
            print(f"   ⚠️ 本轮巡检出错：{e}", file=sys.stderr)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
