#!/usr/bin/env python3
"""
myainet: assemble_network.py
从 注册中心 读取所有节点，输出原始硬件数据 + 评分。
角色分配由主控上运行 skill 的 AI 来判断，不在此处自动分配。
用法：python3 assemble_network.py --registry-host 192.168.1.x
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Windows GBK 终端编码修复，避免 emoji/中文输出崩溃。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def read_all_nodes(host: str, port: int) -> list[dict]:
    """从注册中心读取所有 node:* 键（零依赖裸 socket RESP，见 registry_client）"""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from registry_client import rmap, reachable
    except ImportError:
        print("❌ 找不到 registry_client.py，无法连接 注册中心", file=sys.stderr)
        sys.exit(1)

    # 先分清「够不着」vs「空」：rmap 连不上会静默返回 {}，和「真没节点」一模一样 —— 别让 agent 误判成「注册表空了要恢复」
    if not reachable(host, port):
        print(f"❌ 连不上注册中心 {host}:{port} —— 这不是「注册表空」，是够不着。", file=sys.stderr)
        print("   依次查：① 地址/端口对不对（默认 27182，不是 6379）② 建网机注册中心在不在跑 "
              "③ 异地是否走 Tailscale 地址。连通后再读，别据此做「恢复/重注册」。", file=sys.stderr)
        sys.exit(2)

    nodes = []
    for value in rmap(host, port, "node:*").values():   # 一条连接拉全，省中继往返
        try:
            nodes.append(json.loads(value))
        except Exception:
            continue
    return nodes


def _ram_gb(n: dict) -> float:
    try:
        return float(n.get("hardware", {}).get("ram_gb", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def pick_infra(nodes: list[dict]) -> dict | None:
    """找建网机候选：常驻设备优先（事实 is_infra_candidate），并列按内存（越大越像服务器）。"""
    pool = [n for n in nodes if n.get("is_infra_candidate")] or nodes
    return max(pool, key=_ram_gb) if pool else None


def print_raw_map(nodes: list[dict], infra: dict | None):
    """打印原始网络状态（硬件 + 真装的 agent/工具，不分配角色）"""
    W = 66

    print()
    print("🌐 myainet 网络原始状态")
    print("━" * W)

    if infra:
        ts_ip = infra["network"].get("tailscale_ip", "未配置")
        print(f"🧱 建网机    : {infra['hostname']:<20} LAN {infra['network']['lan_ip']}  TS {ts_ip}")
        iws = infra.get("workspace")
        if iws:
            wssh = infra['network'].get('ssh_tailscale') or infra['network'].get('ssh', 'ssh ?')
            print(f"             工作区: {iws.get('work_dir','?')} [{iws.get('os','?')}/{iws.get('shell','?')}]{'  +GPU' if iws.get('gpu') else ''}  →  {wssh} 后 cd 进去")
            ist = iws.get("state") or {}
            if ist:
                print("             状态: " + "  ".join(f"{k} {v}" for k, v in ist.items()))
            for ha in iws.get("host_access") or []:
                print(f"             本机直用: {ha.get('name')} — {ha.get('via')}")
    else:
        print("⚠️  未找到建网机候选（没有常驻设备），建议先运行建网路径")

    print("━" * W)

    work_nodes = [n for n in nodes if not infra or n["hostname"] != infra["hostname"]]

    for node in work_nodes:
        hw = node.get("hardware", {})
        ip = node["network"]["lan_ip"]

        print(f"\n节点: {node['hostname']:<24} IP: {ip}")
        print(f"  CPU    : {hw.get('cpu', '?')}")
        print(f"  GPU    : {hw.get('gpu', 'none')}")
        print(f"  RAM    : {hw.get('ram_gb', '?')}GB    存储: {hw.get('storage', '?')}")
        print(f"  OS     : {hw.get('os', '?')}")
        print(f"  Python : {node.get('python') or '（老卡片未声明，重注册即补上）'}")
        ws = node.get("workspace")
        if ws:
            wssh = node['network'].get('ssh', 'ssh ?')
            print(f"  工作区 : {ws.get('work_dir', '?')} [{ws.get('os','?')}/{ws.get('shell','?')}]{'  +GPU' if ws.get('gpu') else ''}  →  {wssh} 后 cd 进去")
            st = ws.get("state") or {}
            if st:
                print("           状态: " + "  ".join(f"{k} {v}" for k, v in st.items()))
            for ha in ws.get("host_access") or []:
                print(f"           本机直用: {ha.get('name')} — {ha.get('via')}")
        print(f"  Agents : {', '.join(node.get('agents', [])) or '—'}")
        print(f"  CLI    : {', '.join(node.get('cli', [])) or '—'}")
        print(f"  GUI    : {', '.join(node.get('gui', [])) or '—'}")

        if node.get("is_infra_candidate"):
            print("  ★ 常驻设备，可作为建网机候选")

    print()
    print("━" * W)
    print(f"共 {len(nodes)} 个节点在线，待主控分配角色")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="myainet: 读取节点原始数据，供主控 skill 分配角色"
    )
    parser.add_argument("--registry-host", default="127.0.0.1")
    parser.add_argument("--registry-port", type=int, default=27182)
    parser.add_argument("--output", default="network-raw.json",
                        help="原始数据 JSON 输出路径（供 skill 读取）")
    args = parser.parse_args()

    print(f"📡 连接 注册中心 {args.registry_host}:{args.registry_port}，读取节点清单...")
    nodes = read_all_nodes(args.registry_host, args.registry_port)

    if not nodes:
        print("❌ 注册中心 中没有已注册节点。")
        print("   请先在每台机器上运行：python3 register_node.py --registry-host <建网机IP>")
        print("   或在主控上运行远程触发命令让节点发送名片。")
        sys.exit(1)

    print(f"✅ 读取到 {len(nodes)} 个节点\n")

    infra = pick_infra(nodes)

    # 打印原始网络状态
    print_raw_map(nodes, infra)

    # 保存原始数据 JSON，供 skill AI 读取后生成 myainet-network-config.md
    raw_data = {
        "infra_hostname": infra["hostname"] if infra else None,
        "nodes": nodes,
        "generated_at": int(time.time()),
        "registry_host": args.registry_host,
    }
    out_path = Path(args.output)
    out_path.write_text(json.dumps(raw_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📄 原始数据已保存：{out_path}")
    print("   skill 将读取此文件，给出角色建议，等待用户确认后生成 myainet-network-config.md")


if __name__ == "__main__":
    main()
