#!/usr/bin/env python3
"""
myaiweb: transfer_role.py
建网机职能转移（搬家）—— 把「监听 + 写大屏」这份常驻职责 + 注册表数据从老机搬到新机。
控制能力本来就是共享的（主控 / 其它控制机都能控），所以转的只是 infra 那份活，不是"权力"。

只搬数据：把注册表（node:* / task:*）从老 注册中心 **复制**到新 注册中心（不删老的，搬的是副本）。
老 hub 已经够不到？→ 自动改用【主控本地镜像 registry_cache】喂新 hub（注册表只在老机时它兜底）。
其余几步复用现成路径，搬完打一张清单提示你做（脚本不自动改配置，免得误伤）。

★ 现实场景：你人在这个 LAN 里、搬给一台节点。远程 + 老 hub 硬 down 是搬不了的——
  节点不在 Tailscale 上、外面够不到，那种情况得先回到 LAN。

前置：
  ① 新机已按【建网路径】装好并**启动 注册中心**，且**上了 Tailscale + 注册中心 绑 0.0.0.0**
     （否则外网 / 别的 LAN 够不到这个新 hub）；
  ② 数据来源二选一：老 注册中心 还活着（默认从它复制），或主控存过 registry_cache 镜像（老死了用它）。

用法（在能同时够到新老 注册中心 的机器上跑，通常是主控或老建网机）：
  python3 transfer_role.py --old-host <老建网机IP> --new-host <新建网机IP>
  python3 transfer_role.py --old-host 192.168.1.10 --new-host 192.168.1.11 --dry-run
  python3 transfer_role.py --new-host 192.168.1.11 --from-mirror      # 老 hub 已死：用主控本地镜像喂新 hub
  python3 transfer_role.py --old-host ... --new-host ... --include-status   # 连 status:* 也搬（默认不搬）
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import os
import socket
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

sys.path.insert(0, str(Path(__file__).parent))
try:
    from registry_client import rkeys, rget, rset
except ImportError:
    print("❌ 找不到 registry_client.py，无法连接 注册中心", file=sys.stderr)
    sys.exit(1)


def reachable(host, port):
    if not host:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=3):
            return True
    except Exception:
        return False


def migrate(old_host, old_port, new_host, new_port, patterns, dry):
    """逐个键：从老 注册中心 读、写到新 注册中心（dry 时只数不写）。返回 {pattern: (老总数, 搬了多少)}。"""
    result = {}
    for pat in patterns:
        keys = rkeys(old_host, old_port, pat)
        moved = 0
        for k in keys:
            v = rget(old_host, old_port, k)
            if v is None:
                continue
            if dry:
                moved += 1
                continue
            if rset(new_host, new_port, k, v):
                moved += 1
            else:
                print(f"   ⚠️ 写新失败：{k}")
        result[pat] = (len(keys), moved)
    return result


def migrate_from_mirror(new_host, new_port, dry):
    """老 hub 够不到时：从主控本地镜像把 node:* 喂进新 hub。返回 (镜像里几张, 搬了几张, 镜像路径)。"""
    try:
        from registry_cache import load_card_strings, CACHE_PATH
    except Exception:
        return (0, 0, None)
    cards = load_card_strings()
    moved = 0
    for k, v in cards.items():
        if dry:
            moved += 1
            continue
        if rset(new_host, new_port, k, v):
            moved += 1
        else:
            print(f"   ⚠️ 写新失败：{k}")
    return (len(cards), moved, CACHE_PATH)


def main():
    p = argparse.ArgumentParser(description="myaiweb: 建网机职能转移（搬注册表 + 清单）")
    p.add_argument("--old-host", default="", help="老建网机 注册中心 地址（够不到就自动改用主控镜像）")
    p.add_argument("--old-port", type=int, default=27182)
    p.add_argument("--new-host", required=True, help="新建网机 注册中心 地址（须已装好 注册中心 + 上 Tailscale + 绑 0.0.0.0）")
    p.add_argument("--new-port", type=int, default=27182)
    p.add_argument("--from-mirror", action="store_true", help="强制用主控本地镜像当数据源（老 hub 已死时）")
    p.add_argument("--include-status", action="store_true",
                   help="连 status:* 也搬（默认不搬，巡检会在新 hub 上自动重建）")
    p.add_argument("--dry-run", action="store_true", help="只看会搬什么，不写")
    args = p.parse_args()

    dry = args.dry_run
    H_new, P_new = args.new_host, args.new_port

    # 新 注册中心 必须在（要往它写）
    if not reachable(H_new, P_new):
        print(f"❌ 新注册中心 {H_new}:{P_new} 连不上——先在新机按【建网路径】起好 registry_server.py（还要上 Tailscale + 绑 0.0.0.0）。")
        sys.exit(1)

    # 选数据源：老 注册中心 优先；够不到或 --from-mirror → 主控镜像兜底
    old_up = reachable(args.old_host, args.old_port)
    use_mirror = args.from_mirror or not old_up

    print(f"🚚 myaiweb 建网机转移 → {H_new}" + ("  （dry-run，不写）" if dry else ""))

    if use_mirror:
        if args.from_mirror:
            print("   数据源：主控本地镜像（--from-mirror）")
        else:
            print(f"   ⚠️ 老 hub {args.old_host or '(未指定)'} 够不到 → 自动改用主控本地镜像兜底")
        print(f"\n① 从镜像喂注册表（{'预览' if dry else '只 node:*；task 历史不在镜像里，可弃'}）：")
        total, moved, cache = migrate_from_mirror(H_new, P_new, dry)
        if total == 0:
            print("   ❌ 主控本地镜像是空的 / 没有 —— 无注册表可搬。")
            print("      · 还能连老 hub 时先跑：registry_cache.py --registry-host <老>，存一份再来；")
            print("      · 或放弃旧数据：直接在每台节点上 register_node.py --registry-host 新，让它们重新注册。")
            sys.exit(1)
        print(f"   node:*     镜像 {total} 个 → {'将搬' if dry else '已搬'} {moved} 个   （源 {cache}）")
        src_counts = {"node:*": (total, moved)}
        patterns = ["node:*"]
    else:
        patterns = ["node:*", "task:*"] + (["status:*"] if args.include_status else [])
        if not args.include_status:
            print("   （status:* 不搬——巡检会在新 hub 上 ~30s 内自动重建）")
        print(f"\n① 搬注册表（{'预览' if dry else '复制，不删老的'}）：")
        src_counts = migrate(args.old_host, args.old_port, H_new, P_new, patterns, dry)
        for pat, (total, moved) in src_counts.items():
            print(f"   {pat:<10} 老 {total} 个 → {'将搬' if dry else '已搬'} {moved} 个")

    if not dry:
        print("\n② 校验（新 ≥ 源 即可——新机自己的卡可能让新的更多）：")
        all_ok = True
        for pat in patterns:
            src_total = src_counts[pat][0]
            n = len(rkeys(H_new, P_new, pat))
            ok = n >= src_total
            all_ok = all_ok and ok
            print(f"   {pat:<10} 源 {src_total} → 新 {n}   {'✅' if ok else '❌ 新的少了，重跑一次'}")
        if not all_ok:
            print("   ⚠️ 有 pattern 没搬全，建议重跑（幂等，重复写同样的键无害）。")

    # 搬家清单：剩下几步复用现成路径，脚本不自动改，免得误伤
    old = args.old_host or "（老机）"
    print("\n📋 数据搬完了。剩下这几步（复用现成路径，脚本不替你做、免得误伤）：")
    print(f"   ③ 新建网机 {H_new} 起常驻服务（否则没人写大屏、没人把节点指过来）：")
    print(f"      · python3 dashboard.py                         —— 写大屏")
    print(f"      · python3 patrol.py --registry-host 127.0.0.1    —— 它会 SSH 各节点重注册，把节点（连同身份标记 central）一起指到新家")
    print(f"   ④ 改指向到新建网机 {H_new}（一般用它的 Tailscale 地址）：")
    print(f"      · 主控：       python3 identity.py --set --role 主控 --central {H_new}")
    print(f"      · 各次建网机： python3 setup_hub.py --main {H_new}（或把它 registry_server 的 --main-host 改成 {H_new}）—— 这才是把那个 LAN 同步给新主的")
    print(f"   ⑤ 老建网机 {old} 自动降为主控（保留控制能力，只卸 infra，不是降成节点）：")
    print(f"      · 停它的 dashboard / patrol / 注册中心（不再当 hub）")
    print(f"      · python3 identity.py --set --role 主控 --central {H_new}   —— 标记改「主控 / central={H_new}」，照样控全网、只是不扛 infra")
    print(f"      · （它本就是控制端，不用降成节点；真要彻底走人才用 leave_network.py）")
    print(f"   ⑥ 新机身份写一笔更稳： python3 identity.py --set --role 建网机   （节点标记在 ③ 起 patrol 后自动刷新）")
    print(f"\n   老数据没动（搬的是副本）——③~⑥ 都确认无误后，再关老 注册中心 不迟。")


if __name__ == "__main__":
    main()
