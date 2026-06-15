#!/usr/bin/env python3
"""
myainet: wake.py —— 远程唤醒(WoL)一台睡着的节点。

读节点卡的 wake.mac + belongs_to（它的建网机）→ 经 dispatch 让【建网机】在它本地 LAN
发 WoL magic packet（建网机和节点同 LAN、24h 常驻，正好当发包点）→ 再轮询 --check 直到上线。

铁律前提：WoL 只能唤【睡眠/休眠】，唤不了【已断电】；且要节点 BIOS+网卡都开了 WoL、用有线网。
卡里 wake=null（无有线网卡 / WiFi 笔记本 / 未武装）的节点，本脚本会直接拒。

用法：python3 wake.py --node <名> [--registry-host H] [--timeout 90]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
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

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))


def _central() -> str:
    try:
        return json.loads((Path.home() / ".myainet" / "identity.json").read_text(encoding="utf-8")).get("central", "")
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description="远程唤醒(WoL)一台睡着的节点")
    ap.add_argument("--node", required=True, help="要唤醒的节点名")
    ap.add_argument("--registry-host", default="", help="注册中心地址；不填=从本机身份的 central 读")
    ap.add_argument("--registry-port", type=int, default=27182)
    ap.add_argument("--timeout", type=int, default=90, help="发包后等它上线的最长秒数")
    a = ap.parse_args()

    try:
        from registry_client import rget
    except ImportError:
        print("❌ 找不到 registry_client.py", file=sys.stderr); sys.exit(1)

    host = a.registry_host or _central()
    if not host:
        print("❌ 没有注册中心地址（给 --registry-host，或先把本机配成主控）", file=sys.stderr); sys.exit(1)

    try:
        card = json.loads(rget(host, a.registry_port, f"node:{a.node}") or "{}")
    except Exception as e:
        print(f"❌ 读注册中心失败（{host}:{a.registry_port}）：{e}", file=sys.stderr); sys.exit(1)
    if not isinstance(card, dict) or not card.get("hostname"):
        print(f"❌ 注册中心查不到节点 {a.node}", file=sys.stderr); sys.exit(1)

    wake = card.get("wake") or {}
    mac = (wake.get("mac") or "").replace(":", "").replace("-", "").lower()
    if len(mac) != 12:
        print(f"❌ {a.node} 不可唤醒：卡里没有有线网卡 MAC（WiFi 笔记本 / 未武装 WoL / 无网卡）。", file=sys.stderr)
        sys.exit(1)
    if wake.get("armed") is False:
        print(f"⚠️ {a.node} 的网卡 magic packet 未武装，可能唤不醒——仍尝试发包。", file=sys.stderr)

    hub = card.get("belongs_to") or ""
    if not hub:
        print(f"❌ {a.node} 卡里没有 belongs_to（它的建网机）。WoL 必须由同 LAN 的建网机发，定位不到发包点。", file=sys.stderr)
        sys.exit(1)
    try:
        hub_card = json.loads(rget(host, a.registry_port, f"node:{hub}") or "{}")
    except Exception:
        hub_card = {}
    hubpy = (hub_card.get("python") or "python3").replace("\\", "/")   # Git Bash 吃正斜杠

    # WoL 发包脚本：在建网机本机 LAN 广播 magic packet（6×FF + MAC×16，端口 9/7 各发几次）
    wol_src = (
        "import socket\n"
        f"m=bytes.fromhex('{mac}')\n"
        "p=b'\\xff'*6+m*16\n"
        "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1)\n"
        "[s.sendto(p,('255.255.255.255',x)) for x in (9,7) for _ in range(3)]\n"
        "print('WOL_SENT')\n"
    )
    b64 = base64.b64encode(wol_src.encode()).decode()        # base64 裹住，绕开跨 shell 引号/反斜杠坑
    wol_cmd = f"{hubpy} -c \"import base64;exec(base64.b64decode('{b64}').decode())\""

    disp = str(SCRIPTS / "dispatch.py")
    base = [sys.executable, disp, "--registry-host", host, "--registry-port", str(a.registry_port)]

    print(f"📡 经建网机 {hub} 向 {a.node}（{wake.get('mac')}）发 WoL magic packet …")
    r = subprocess.run(base + ["--node", hub, "--name", f"wake-{a.node}", wol_cmd],
                       capture_output=True, text=True)
    if "WOL_SENT" not in (r.stdout + r.stderr):
        print(f"⚠️ 发包没确认成功（建网机够不到？）：\n{(r.stdout + r.stderr)[-600:]}", file=sys.stderr)

    print(f"⏳ 等 {a.node} 上线（最多 {a.timeout}s）…")
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        time.sleep(6)
        chk = subprocess.run(base + ["--node", a.node, "--check", "--timeout", "8"],
                             capture_output=True, text=True)
        if "✅" in (chk.stdout + chk.stderr):
            print(f"✅ {a.node} 已唤醒、SSH 够得到。")
            return
        print("   还没起，继续等…")
    print(f"❌ {a.timeout}s 内没等到 {a.node} 上线。可能：它本就断电(非睡眠) / BIOS 没开 WoL / 不在同 LAN / 快速启动挡了 S5。", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
