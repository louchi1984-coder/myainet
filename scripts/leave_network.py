#!/usr/bin/env python3
"""
myainet: leave_network.py
退网：让本机（或指定节点）干净离开 myainet 网络，尽量回到入网前状态。
  ① 删掉它在建网机 注册中心 里的注册卡；
  ② 退出 tailnet 并停用/卸载 Tailscale（仅当装了，且只对【本机】生效）；
  ③ 删机器级身份标记 ~/.myainet/identity.json（只对【本机】生效，否则下次组网还自认在老网里）；
  ④ SSH 钥匙撤销暂时给手动提示（等"自动换钥匙"建好再自动）。

用法：
  python3 leave_network.py --registry-host <建网机IP>                 # 退本机
  python3 leave_network.py --registry-host <建网机IP> --node-name nuc # 替已死节点删卡（不碰本机 Tailscale）
  python3 leave_network.py --registry-host <建网机IP> --purge         # 连 Tailscale 软件一起卸
  python3 leave_network.py --registry-host <建网机IP> --dry-run       # 只看会做什么，不动手
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LIN = sys.platform.startswith("linux")

DRY = False


def do(desc, *cmd):
    print(f"   {'[dry-run] ' if DRY else '$ '}{' '.join(cmd)}   # {desc}")
    if DRY:
        return
    try:
        subprocess.run(list(cmd), timeout=60)
    except Exception as e:
        print(f"     ⚠️ {e}")


def sudo_prefix():
    if IS_WIN:
        return []
    try:
        if os.geteuid() == 0:
            return []
    except AttributeError:
        pass
    return ["sudo"] if shutil.which("sudo") else []


def tailscale_bin():
    for c in ("tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"):
        found = shutil.which(c) or (c if Path(c).exists() else None)
        if found:
            return found
    return None


def remove_card(host, port, name):
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from registry_client import rdel, rget
    except ImportError:
        print("   ⚠️ 找不到 registry_client.py，跳过删卡")
        return
    key = f"node:{name}"
    if DRY:
        print(f"   [dry-run] 删 注册中心 键 {key} @ {host}:{port}")
        return
    existed = rget(host, port, key) is not None
    if rdel(host, port, key):
        print(f"   ✅ 已删注册卡 {key}" if existed else f"   ℹ️ {key} 本就不在（已干净）")
    else:
        print(f"   ⚠️ 删卡失败（连不上建网机 注册中心 {host}:{port}？）")


def remove_identity():
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from identity import IDENTITY_PATH as ident_path
    except ImportError:
        ident_path = Path.home() / ".myainet" / "identity.json"
    if not ident_path.exists():
        print("   ℹ️ 本机没有身份标记（已干净）")
        return
    if DRY:
        print(f"   [dry-run] 删身份标记 {ident_path}")
        return
    try:
        ident_path.unlink()
        print(f"   ✅ 已删身份标记 {ident_path}（本机不再自认在网内）")
    except OSError as e:
        print(f"   ⚠️ 删身份标记失败：{e}")


def remove_tailscale(purge):
    ts = tailscale_bin()
    if not ts:
        print("   ℹ️ 本机没装 Tailscale，跳过")
        return
    s = sudo_prefix()
    do("退出 tailnet（从网络移除本设备）", ts, "logout")
    if IS_MAC:
        do("移除 tailscaled 系统服务", *s, "tailscaled", "uninstall-system-daemon")
    elif IS_LIN:
        do("停用 tailscaled 服务", *s, "systemctl", "disable", "--now", "tailscaled")
    elif IS_WIN:
        do("停止 Tailscale 服务", "powershell", "-NoProfile", "-Command",
           "Stop-Service Tailscale -ErrorAction SilentlyContinue")
    if purge:
        if IS_MAC and shutil.which("brew"):
            do("卸载 Tailscale（Homebrew）", "brew", "uninstall", "tailscale")
        elif IS_LIN:
            do("卸载 Tailscale（apt；其它发行版自行调整）", *s, "apt-get", "remove", "-y", "tailscale")
        elif IS_WIN:
            do("卸载 Tailscale 客户端", "powershell", "-NoProfile", "-Command",
               "winget uninstall --id tailscale.tailscale -e")
    else:
        print("   ℹ️ 只退出+停服务，保留软件；要连软件一起卸载请加 --purge")


def main():
    global DRY
    p = argparse.ArgumentParser(description="myainet: 退网")
    p.add_argument("--registry-host", required=True, help="建网机 注册中心 地址")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--node-name", default=None, help="要退网的节点名（默认本机 hostname）")
    p.add_argument("--purge", action="store_true", help="连 Tailscale 软件一起卸载")
    p.add_argument("--keep-tailscale", action="store_true", help="不动 Tailscale，只删卡")
    p.add_argument("--dry-run", action="store_true", help="只显示会做什么，不动手")
    args = p.parse_args()
    DRY = args.dry_run

    name = args.node_name or socket.gethostname()
    is_local = (args.node_name is None) or (args.node_name.lower() == socket.gethostname().lower())
    print(f"🚪 myainet 退网：{name}" + ("  （dry-run，不动手）" if DRY else ""))

    print("① 删注册卡")
    remove_card(args.registry_host, args.registry_port, name)

    print("② Tailscale 退网 / 清理")
    if args.keep_tailscale:
        print("   ℹ️ 跳过（--keep-tailscale）")
    elif is_local:
        remove_tailscale(args.purge)
    else:
        # 安全闸：退别人的节点时绝不碰本机 Tailscale
        print(f"   ℹ️ {name} 不是本机——本脚本只清【本机】的 Tailscale，不会动它。")
        print(f"      要清 {name} 的 Tailscale：在它上面跑本命令，或从 Tailscale 后台移除该设备。")

    print("③ 机器级身份标记")
    if is_local:
        remove_identity()
    else:
        # 安全闸：退别人的节点时绝不碰本机身份
        print(f"   ℹ️ {name} 不是本机——它的 ~/.myainet/identity.json 要在它自己上面清。")

    print("④ SSH 钥匙")
    print("   ℹ️ 装钥匙已自动（keysync.py）；撤钥匙半自动：")
    print("      · 退的是控制方(建网机) → 从 注册中心 删 pubkey:<它的hostname>，新节点就不会再装它；")
    print("      · 各机器 authorized_keys 里 keysync 装的行带 `myainet:` 标记，要彻底清就 grep 这个删掉。")

    print("\n完成。" + ("（以上为 dry-run 预览，未动手）" if DRY else f"  {name} 已退网。"))


if __name__ == "__main__":
    main()
