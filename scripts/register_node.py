#!/usr/bin/env python3
"""
myaiweb: register_node.py
将节点卡写入 注册中心。默认长期保存；显式传 --ttl 时才注册为临时节点。
用法：python3 register_node.py --registry-host 192.168.1.x
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

# Windows GBK 终端编码修复
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def parse_sysinfo(output: str) -> dict:
    """解析 sysinfo.py 的 key=value 输出"""
    data = {}
    for line in output.strip().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip()
    return data


# 注：原 _detect_always_on / score_roles（七维评分）已移除。
# 节点卡只存事实（硬件 / agent / 已装工具 / 是否常驻），角色与路由交给主控的 AI 按事实判定。


def derive_problems(info: dict, agents: list, cli: list) -> list:
    """从事实推出『卡住任务的待解决项』——不是凭空写，是 facts 一摆就成立的。AI 评估时可再补角色相关的。"""
    p = []
    try:
        avail = float(info.get("disk_avail_gb", 0) or 0)
        total = float(info.get("disk_total_gb", 0) or 0)
        if total > 0 and avail / total < 0.10:
            p.append(f"盘快满（剩 {avail:.0f}/{total:.0f}GB）→ 放数据/工作区前先清")
    except (TypeError, ValueError):
        pass
    if "python3" not in cli and "python" not in cli:
        p.append("缺 python 运行时 → 自动化/部署/很多脚本跑不了")
    gpu = (info.get("gpu_model", "") or "").lower()
    if gpu and gpu not in ("none", "") and "ollama" not in cli:
        p.append("有 GPU 但没装 ollama → 本地算力没启用")
    if not agents:
        p.append("没装 agent → 云端 AI / 编程干不了")
    if info.get("net_reach_anthropic", "?") == "no":
        p.append("够不到 Claude API → 云端 AI 受限")
    return p


def build_node_card(info: dict, node_name: str = None, role: str = "",
                    belongs_to: str = "", link: dict = None) -> dict:
    """构建节点卡——只存事实，不算评分。两条分类轴：① 拓扑角色 role ② 能力三层（硬件/环境/网速 link）。"""
    hostname = node_name or info.get("hostname", socket.gethostname())
    lan_ip = info.get("lan_ip", "unknown")
    user = info.get("user", "user")
    network = {
        "lan_ip": lan_ip,
        "ssh": f"ssh {user}@{lan_ip}",
    }
    tailscale_ip = info.get("tailscale_ip", "").strip()
    if tailscale_ip:
        network["tailscale_ip"] = tailscale_ip
        network["ssh_tailscale"] = f"ssh {user}@{tailscale_ip}"

    agents    = [a for a in info.get("agents", "").split(",") if a]
    cli       = [t for t in info.get("cli", "").split(",") if t]
    gui       = [g for g in info.get("gui", "").split(",") if g]
    try:                                  # 新格式：JSON [{"name":..,"ok":bool}]
        models = json.loads(info.get("models") or "[]")
        models = models if isinstance(models, list) else []
    except Exception:                     # 旧格式回退：逗号分隔的纯名字
        models = [{"name": m, "ok": True} for m in info.get("models", "").split(",") if m]
    models = [({"name": m, "ok": True} if isinstance(m, str) else m) for m in models if m]
    try:                                  # 原生远程工作区（sysinfo 自报标记 → OS 契约）；无=null
        workspace = json.loads(info.get("workspace") or "null")
    except Exception:
        workspace = None
    try:                                  # 每块盘容量/空闲（供主控选盘挑最空那块当工作区）
        disks = json.loads(info.get("disks") or "[]")
    except Exception:
        disks = []
    always_on = info.get("is_always_on", "?")
    problems  = derive_problems(info, agents, cli)

    return {
        "hostname":     hostname,
        "primary_role": "⬜ 未分配",          # 角色交给主控/AI 按事实判定，不再自动算
        "sub_roles":    [],
        "is_infra_candidate": always_on == "yes",   # 事实：常驻设备=建网候选
        "role":         role,         # 拓扑角色：主控 / 建网机 / 次建网机 / 节点（setup 时定，可兼任）
        "belongs_to":   belongs_to,   # 节点归哪台建网机（路由键 + 继承它的外网 link）；空=直连/本地/它自己是建网机
        "hardware": {
            "cpu":     f"{info.get('cpu_model','?')} ({info.get('cpu_cores','?')}C/{info.get('cpu_threads','?')}T)",
            "gpu":     f"{info.get('gpu_model','none')} {info.get('gpu_vram_gb','0')}GB [{info.get('gpu_framework','none')}]",
            "ram_gb":  info.get("ram_gb", "?"),
            "storage": f"{info.get('disk_type','?')} {info.get('disk_avail_gb','?')}/{info.get('disk_total_gb','?')}GB",
            "disks":   disks,    # 每块盘 [{mount,total_gb,avail_gb}]，主控选盘挑 avail_gb 最大的
            "os":      info.get("os", "?"),
        },
        "agents":    agents,    # 装了哪些 AI agent（带版本）
        "cli":       cli,       # CLI 工具/运行时（SSH 命令可控）
        "gui":       gui,       # GUI 应用（要靠 computer-use 控）
        "models":    models,    # 本地大模型（运行时无关：ollama / LM Studio / HF 缓存）
        "workspace": workspace, # 原生远程工作区（自报标记 → OS 契约 os/shell/work_dir/python/gpu）；全网据此知道这台有工作区，dispatch --workspace 进去派活。无=null
        "python":    info.get("python", ""),   # 这台机器自报的 Python 解释器路径。跨平台调它上面的脚本读这个，别猜 python/python3
        "scripts_dir": str(Path(__file__).resolve().parent),  # 这台机器上 skill 脚本的真实目录（装在哪自报）；patrol 远程刷新卡时用它，不假设 ~/myaiweb
        "always_on":     always_on,
        "reach_claude":  info.get("net_reach_anthropic", "?"),  # 能否访问 Claude API（墙内外）
        "link":          link or {},  # 层3 网速：外网底子(net_class/cellular/nat/isp)+带宽，【建网机自测】；节点空=继承 belongs_to 那台的
        "problems":      problems,    # 卡住任务的待解决项（从事实 derive，re-register 刷新；AI 评估可补角色相关的）。任务路由前先看它、能解决先解决
        "network":       network,
        "registered_at": int(time.time()),
    }


def write_to_registry(card: dict, host: str, port: int, ttl: int | None = None) -> bool:
    """将节点卡写入注册中心。零依赖裸 socket RESP（见 registry_client）。
    全失败也不丢卡——暂存本地，建网机 注册中心 起来后重跑本命令即可补注册。"""
    key = f"node:{card['hostname']}"
    value = json.dumps(card, ensure_ascii=False)

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from registry_client import rset
    except ImportError:
        rset = None

    if rset and rset(host, port, key, value, ttl):
        return True

    # 兜底：连不上也别丢卡
    fallback = Path(__file__).parent / f"{card['hostname']}-pending-register.json"
    try:
        fallback.write_text(value, encoding="utf-8")
        print(f"⚠️  连不上 注册中心 {host}:{port}，节点卡已暂存：{fallback}", file=sys.stderr)
        print("   （建网机 注册中心 起来后，重跑本命令即可补注册）", file=sys.stderr)
    except Exception:
        pass
    return False


def print_ascii_card(card: dict):
    W = 56
    def line(left="", right="", fill=" "):
        content = f"  {left:<{W-4}}" if not right else f"  {left:<22}{right:<{W-26}}"
        return f"║{content:{fill}<{W}}║"

    print("╔" + "═" * W + "╗")
    print(f"║{'  myaiweb NODE CARD':<{W}}║")
    print("╠" + "═" * W + "╣")
    print(line(f"Node     : {card['hostname']}"))
    print(line(f"Role     : {card['primary_role']}"))
    for sr in card["sub_roles"]:
        print(line(f"SubRole  : {sr}"))
    if card["is_infra_candidate"]:
        print(line("           ★ 建议作为建网机"))
    print("╠" + "═" * W + "╣")
    hw = card["hardware"]
    print(line(f"CPU      : {hw['cpu'][:W-14]}"))
    print(line(f"GPU      : {hw['gpu'][:W-14]}"))
    print(line(f"RAM      : {hw['ram_gb']}GB"))
    print(line(f"Storage  : {hw['storage'][:W-14]}"))
    print(line(f"OS       : {hw['os'][:W-14]}"))
    print("╠" + "═" * W + "╣")
    print(line(f"Agents   : {(', '.join(card['agents'])[:W-14]) if card['agents'] else '—'}"))
    print(line(f"CLI      : {(', '.join(card['cli'])[:W-14]) if card['cli'] else '—'}"))
    print(line(f"GUI      : {(', '.join(card['gui'])[:W-14]) if card['gui'] else '—'}"))
    _ms = ", ".join((m.get("name", "?") + ("" if m.get("ok", True) else " ✗不可用")) if isinstance(m, dict) else str(m)
                    for m in card.get("models", []))
    print(line(f"Models   : {(_ms[:W-14]) if _ms else '—'}"))
    print(line(f"Python   : {(card.get('python','') or '—')[:W-14]}"))
    _ws = card.get("workspace")
    if _ws:
        _wl = f"{_ws.get('work_dir','?')} [{_ws.get('os','?')}/{_ws.get('shell','?')}]" + (" +GPU" if _ws.get("gpu") else "")
        print(line(f"Workspace: {_wl[:W-14]}"))
    print("╠" + "═" * W + "╣")
    print(line(f"IP       : {card['network']['lan_ip']}"))
    if card["network"].get("tailscale_ip"):
        print(line(f"Tailscale: {card['network']['tailscale_ip']}"))
    print(line(f"SSH      : {card['network']['ssh']}"))
    if card["network"].get("ssh_tailscale"):
        print(line(f"TS SSH   : {card['network']['ssh_tailscale']}"))
    print("╚" + "═" * W + "╝")


DEFAULT_SPEED_URL = "https://speed.cloudflare.com/__down?bytes=20000000"


def measure_bandwidth(url=DEFAULT_SPEED_URL, max_seconds=8):
    """粗测下行带宽（Mbps）。绕系统代理直连——建网机一般没代理，测的就是真上行。失败返回 None。"""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "myaiweb"})
        with opener.open(req, timeout=12) as r:
            while True:
                b = r.read(65536)
                if not b:
                    break
                got += len(b)
                if time.time() - t0 > max_seconds:
                    break
    except Exception:
        return None
    dt = time.time() - t0
    if dt <= 0 or got < 100000:
        return None
    return round((got * 8 / 1e6) / dt, 1)


def measure_link(script_dir, speed_url=None):
    """【建网机自测】本 LAN 外网底子（跑 netprobe 取 net_class/cellular/nat/isp）+ 下行带宽。本 LAN 节点继承这份。"""
    link = {}
    try:
        r = subprocess.run([sys.executable, str(script_dir / "netprobe.py"), "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
        for line in reversed((r.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                np = json.loads(line)
                link.update({
                    "net_class":     np.get("net_class"),       # cellular/public-ipv4/public-ipv6/cgnat-fixed
                    "cellular":      np.get("net_cellular"),
                    "nat_symmetric": np.get("net_nat_symmetric"),
                    "cgnat":         np.get("net_cgnat"),
                    "isp":           np.get("net_isp"),
                    "country":       np.get("net_country"),
                })
                break
    except Exception:
        pass
    mbps = measure_bandwidth(speed_url or DEFAULT_SPEED_URL)
    if mbps is not None:
        link["bandwidth_mbps"] = mbps     # 测不到就不写这条（中国直连飘，best-effort）
    link["measured_at"] = int(time.time())
    return link


def main():
    parser = argparse.ArgumentParser(description="myaiweb: 注册节点到 注册中心")
    parser.add_argument("--registry-host", default=None, help="建网机 注册中心 地址；不给则局域网广播自动发现（建网机自己用 127.0.0.1）")
    parser.add_argument("--registry-port", type=int, default=27182)
    parser.add_argument("--ttl", type=int, default=0, help="临时节点有效期（秒）；默认 0 表示长期保存")
    parser.add_argument("--node-name", default=None, help="自定义节点名称")
    parser.add_argument("--sysinfo", default=None, help="sysinfo 输出文件路径（不传则自动采集，走 sysinfo.py）")
    parser.add_argument("--dry-run", action="store_true", help="只打印名片，不写 注册中心")
    parser.add_argument("--output", default=None, help="保存节点卡 JSON 的路径")
    parser.add_argument("--enable-ssh", action="store_true", help="注册前自动开启本机 SSH 服务（需管理员）")
    parser.add_argument("--role", default="", help="拓扑角色：主控 / 建网机 / 次建网机 / 节点（可兼任，逗号分隔）")
    parser.add_argument("--belongs-to", default="", help="（节点用）归哪台建网机的主机名；主控穿过它控这台、并继承它的外网 link。不填=直连")
    parser.add_argument("--measure-link", action="store_true", help="（建网机用）自测本 LAN 外网底子+带宽 → 写进 link（节点继承，不用自测）")
    parser.add_argument("--speed-url", default="", help="测带宽的下载端点（默认 Cloudflare；中国直连飘时可换成你能稳定够到的大文件 URL）")
    args = parser.parse_args()

    # 没给建网机地址 → 局域网广播自动发现（这才是「不输 IP 就入网」）。建网机=自己的注册中心，用 127.0.0.1、不找自己。
    if not args.registry_host:
        if "建网" in (args.role or "") or "hub" in (args.role or "").lower():
            args.registry_host = "127.0.0.1"
        else:
            try:
                from discover import discover_hub
            except Exception:
                discover_hub = None
            print("🔍 没给建网机地址 → 局域网广播自动发现…")
            found = discover_hub() if discover_hub else None
            if found:
                args.registry_host = found[0]
                args.registry_port = found[1] or args.registry_port
                print(f"   ✅ 发现建网机：{args.registry_host}")
            else:
                print("   ❌ 没找到建网机（确认它在同一局域网且在运行；跨网用 Tailscale 名字、或手动 --registry-host <IP>）",
                      file=sys.stderr)
                sys.exit(2)

    # 可选：先自动开启 SSH（节点要能被建网机/主控控制）
    if args.enable_ssh:
        ssh_script = Path(__file__).parent / "enable_ssh.py"
        if ssh_script.exists():
            print("🔌 开启 SSH 服务...")
            subprocess.run([sys.executable, str(ssh_script)])
        else:
            print("⚠️  找不到 enable_ssh.py，跳过自动开启 SSH")

    # 采集硬件数据
    if args.sysinfo:
        raw = Path(args.sysinfo).read_text()
    else:
        script_dir = Path(__file__).parent
        sysinfo_py = script_dir / "sysinfo.py"
        print("🔍 采集硬件信息...")
        # 用当前 Python 解释器执行，三平台通用（不依赖 bash）
        result = subprocess.run(
            [sys.executable, str(sysinfo_py)],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        raw = result.stdout

    info = parse_sysinfo(raw)
    # 身份统一：角色 + 名字都优先读机器级 identity（钉死、不绑目录、不随 gethostname 飘）。
    #  · 角色——避开「中文角色经 SSH argv 被 GBK 搞乱 / 漏传把建网机 role 冲成空」；
    #  · 名字——避开「macOS gethostname() 换网就在 X.local / X 之间飘 → 同一台注册成多张卡」。
    ident = {}
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from identity import read_identity
        ident = read_identity() or {}
    except Exception:
        pass
    role = args.role or ident.get("role", "") or ""
    node_name = args.node_name or ident.get("name") or None
    link = None
    if args.measure_link:
        print("🌐 建网机自测：外网底子(netprobe) + 下行带宽...")
        link = measure_link(Path(__file__).parent, args.speed_url or None)
    card = build_node_card(info, node_name, role, args.belongs_to, link)

    # 打印名片
    print()
    print_ascii_card(card)
    print()

    # 保存 JSON
    if args.output:
        Path(args.output).write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"📄 节点卡已保存：{args.output}")

    # 写入 注册中心
    if not args.dry_run:
        print(f"📡 注册到 注册中心 {args.registry_host}:{args.registry_port} ...")
        ttl = args.ttl if args.ttl > 0 else None
        ok = write_to_registry(card, args.registry_host, args.registry_port, ttl)
        if ok:
            if ttl:
                print(f"✅ 临时节点 {card['hostname']} 已注册到 myaiweb，TTL {ttl}s")
            else:
                print(f"✅ 节点 {card['hostname']} 已注册到 myaiweb（长期保存）")
            print(f"   刷新命令：{sys.executable} {__file__} --registry-host {args.registry_host} --node-name {card['hostname']}")
            # 写机器级身份标记：注册成功 = 认得自己（名 + 归属 + 中央）；
            # patrol 重注册时只带 --registry-host，所以 central 会自愈 —— 转移建网机后节点自然指向新家。
            try:
                from identity import write_identity
                write_identity(role=(role or "节点"),
                               central=args.registry_host,
                               name=card["hostname"],
                               belongs_to=(args.belongs_to or None))
            except Exception:
                pass
            # 换钥匙焊进注册：注册=自动发布本机公钥(主控/建网机)+装控制方公钥(建网机/节点)。
            # 配合 patrol 每轮 auto_keys（晚入伙的钥匙下轮自动补）→ 组网不挑先后、零单独钥匙命令。
            try:
                _auto_keysync(role, args.registry_host, args.registry_port)
            except Exception:
                pass
        else:
            print("⚠️  注册中心 注册失败，节点卡仍可本地使用")
    else:
        print("（dry-run 模式，未写入 注册中心）")


def _auto_keysync(role: str, host, port) -> None:
    """把 SSH 换钥匙焊进注册（失败不影响注册，调用方已 try 包住）：
       主控→发布自己公钥；建网机/次→发布+装控制方公钥；节点→装控制方公钥。
    配合 patrol 每轮 auto_keys（晚入伙的钥匙下一轮自动补）→ 组网不挑先后、无单独 keysync 命令。"""
    import io
    from contextlib import redirect_stdout
    import keysync
    r = role or "节点"
    is_master = "主控" in r or "master" in r.lower()
    is_hub = "建网" in r or "hub" in r.lower()
    published = installed = 0
    with redirect_stdout(io.StringIO()):                  # keysync 内部会 print，吞掉，只报一行
        if is_master or is_hub:
            publine, _ = keysync.ensure_keypair()
            keysync.publish(host, port, publine, dry=False)
            published = 1
        if is_hub or not is_master:                       # 建网机+节点装控制方公钥；主控只发不装
            installed = keysync.install(host, port, dry=False) or 0
    bits = []
    if published:
        bits.append("已发布本机公钥")
    if is_hub or not is_master:
        bits.append(f"装入 {installed} 把控制方公钥" if installed else "控制方公钥已最新")
    if bits:
        print("  🔑 换钥匙：" + "；".join(bits))


if __name__ == "__main__":
    main()
