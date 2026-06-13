#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
myainet: setup_control.py
一条命令把「主控」配好 —— 和 setup_hub 对称，确定性：每步自验、不跳步、不靠 agent 现场拼命令。
主控 = 控制全网但不扛 infra（不起注册中心/大屏/巡检）；借建网机的注册中心 + 持本地镜像抗建网机掉线。

为什么要这个脚本：主控原来是四角色里唯一没有确定性入口的 —— 身份/Tailscale/注册/镜像全靠 agent
照文档手敲，最容易出错（真实事故：central 被写成 127.0.0.1 自指 → 裸加载 skill 查不到注册中心 →
误判「注册表空」）。固化成脚本后，agent 的活从「手敲 5 步易错命令」缩成「跑这一个 + 看结果」。

它依次做：① 装 Tailscale（脚本下载+装，登你自己账号）② 写身份（主控，central=建网机地址）
         ③ 开 SSH（让主控也能被反控/转移）④ 注册自己进卡（顺带发布主控公钥）
         ⑤ 存本地镜像（dispatch 在建网机掉线时回退直驱）⑥ 自检 + 如实报告。

用法：
  python3 setup_control.py --central <建网机地址>      # 同 LAN 填它 lan_ip，异地填它 Tailscale IP
  python3 setup_control.py --central <地址> --skip-ssh  # 跳过开 SSH
  python3 setup_control.py --central <地址> --verify    # 只自检，看缺哪步、不动手
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
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
sys.path.insert(0, str(SCRIPTS))

# 复用 setup_hub 的确定性零件（Tailscale 装+登、前台跑脚本、端口探活、本机 IP）—— 单一来源，不重写
from setup_hub import _fg, _tailscale, _ts_exe, _port_up   # noqa: E402

_LOCAL = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


def _reachable(host, port):
    try:
        from registry_client import reachable
        return reachable(host, port)
    except Exception:
        return _port_up(host, port, tries=2)


def _my_card_in_registry(host, port) -> bool:
    """主控自己的卡有没有注册进建网机注册中心。"""
    try:
        from registry_client import rget
        return rget(host, port, f"node:{socket.gethostname()}") is not None
    except Exception:
        return False


def _identity_ok(central) -> tuple[bool, str]:
    """身份标记对不对：role=主控 且 central=给定的建网机地址（非自指毒值）。"""
    try:
        from identity import read_identity, is_control
        m = read_identity() or {}
        if not is_control(m.get("role")):
            return False, f"role={m.get('role')!r}（应为 主控 / control）"
        if (m.get("central") or "").strip().lower() in _LOCAL:
            return False, f"central={m.get('central')!r}（自指毒值/空 —— 应填建网机地址）"
        return True, f"role=主控 central={m.get('central')}"
    except Exception as e:
        return False, str(e)


def _verdict(central, port, ssh_ok, ts_ok):
    """如实报告：全齐报就绪；缺啥说啥，没配完 exit(1)（调用方拿到失败信号，不误判成功）。"""
    print()
    reach = _reachable(central, port)
    id_ok, id_msg = _identity_ok(central)
    registered = _my_card_in_registry(central, port) if reach else False
    print(f"   注册中心 {central}:{port} " + ("✅ 连得上" if reach else "❌ 连不上（地址对吗？建网机在跑吗？异地走 Tailscale 地址）"))
    print(f"   身份标记 " + ("✅ " if id_ok else "❌ ") + id_msg)
    print(f"   注册自己 " + ("✅ 卡在注册中心" if registered else "❌ 没注册上"))
    print(f"   SSH      " + ("✅ 22 在听" if ssh_ok else "⚠️ 22 没在听（主控不被反控/不可转移；要的话去掉 --skip-ssh 重跑）"))
    print(f"   Tailscale " + ("✅ 已登录上线" if ts_ok else "⚠️ 没装上或没登录"))
    core_ok = reach and id_ok and registered
    if core_ok and ts_ok:
        print("\n✅ 主控就绪：能读全网 + 借建网机注册中心控全网 + 本地镜像抗掉线。")
        return
    miss = []
    if not reach:      miss.append("够不到建网机注册中心")
    if not id_ok:      miss.append("身份标记没写对")
    if not registered: miss.append("没注册进注册中心")
    if not ts_ok:      miss.append("Tailscale（异地控制要它）")
    print("\n❌ 主控没配完，还差：" + "；".join(miss))
    print(f"   去掉 --verify 跑一遍补上：python3 setup_control.py --central {central}")
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description="一条命令配『主控』（确定性，和 setup_hub 对称）")
    ap.add_argument("--central", required=True,
                    help="建网机地址（主控的注册中心在它那）：同 LAN 填它 lan_ip，异地填它 Tailscale IP")
    ap.add_argument("--registry-port", type=int, default=27182)
    ap.add_argument("--skip-ssh", action="store_true", help="跳过开 SSH（不需要被反控/转移）")
    ap.add_argument("--verify", action="store_true", help="只自检，不动手")
    args = ap.parse_args()
    central, port = args.central.strip(), args.registry_port

    # central 卫士：主控的 central 必须是【建网机】地址，绝不能自指（这正是当初毒值事故的源头）
    if central.lower() in _LOCAL:
        print(f"❌ --central 不能是 {args.central!r}（指向自己）—— 主控要填【建网机】的地址：同 LAN 填它 lan_ip，异地填它 Tailscale IP。",
              file=sys.stderr)
        sys.exit(1)

    if args.verify:
        print("🩺 主控自检：")
        ssh_ok = args.skip_ssh or _port_up("127.0.0.1", 22, tries=2)
        ts_ok = _tailscale(do_install=False)
        _verdict(central, port, ssh_ok, ts_ok)
        return

    print("🎛️  主控一键配置（确定性脚本；每步自己验，不跳步）\n")

    # 先确认够得到建网机注册中心 —— 够不到就别往下写身份/注册了（否则注册必失败、身份留半截）
    if not _reachable(central, port):
        print(f"❌ 够不到建网机注册中心 {central}:{port}。先确认：① 地址对不对（同 LAN 用 lan_ip / 异地用 Tailscale IP）"
              f"② 建网机在不在跑 ③ 异地是否要先装 Tailscale。", file=sys.stderr)
        sys.exit(1)
    print(f"① 建网机注册中心 {central}:{port} ✅ 连得上")

    # ② 身份标记（主控，central=建网机地址；identity 卫士会再挡一次自指毒值）
    print(f"② 写身份标记（主控，central={central}）…")
    _fg("identity.py", "--set", "--role", "主控", "--central", central)

    # ③ 开 SSH（让主控也能被反控/转移；不需要可 --skip-ssh）
    if args.skip_ssh:
        print("③ 跳过开 SSH（--skip-ssh）")
    else:
        print("③ 开 SSH 服务（可能弹管理员授权，点允许）…")
        _fg("enable_ssh.py")

    # ④ 注册自己进卡（role=主控；register_node 顺带发布主控公钥，节点/建网机据此装免密门）
    print("④ 注册本机进卡（主控）…")
    _fg("register_node.py", "--registry-host", central, "--role", "主控")

    # ⑤ 存本地镜像（建网机掉线时 dispatch 回退这份、直驱够得着的节点）
    print("⑤ 存全网本地镜像（抗建网机掉线）…")
    _fg("registry_cache.py", "--registry-host", central, "--registry-port", str(port))

    # ⑥ Tailscale（异地控制要它；脚本下载+装，登你自己账号）
    print("⑥ Tailscale（异地控制要它；脚本帮你下载+装，登录登你自己账号）：")
    ts_ok = _tailscale(do_install=True)
    if ts_ok:
        print("   把 Tailscale IP 刷进本机卡 …")
        _fg("register_node.py", "--registry-host", central, "--role", "主控")

    # ⑦ 自检 + 如实报告
    print("\n⑦ 自检：")
    ssh_ok = args.skip_ssh or _port_up("127.0.0.1", 22, tries=2)
    _verdict(central, port, ssh_ok, ts_ok)


if __name__ == "__main__":
    main()
