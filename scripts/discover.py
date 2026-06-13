#!/usr/bin/env python3
"""
myainet: discover.py
零依赖局域网自动发现建网机 —— 新机器不输 IP 也能找到建网机（这才叫自动组网）。

机制：建网机的注册中心起一个 UDP 应答器（serve_discovery，registry_server 自动起它）；
新机器广播一句「建网机在哪?」，建网机回一条 JSON（它的 LAN IP + 注册中心端口）。
只在同一局域网内有效（广播不跨路由器）；跨局域网走 Tailscale 名字（另见 SKILL）。

用法：
  python3 discover.py            # 广播找建网机，找到就打印它的 registry-host（一行 IP），没找到非 0 退出
  （register_node 不带 --registry-host 时自动调 discover_hub()）
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
try:
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DISCOVERY_PORT = 27182                     # UDP（和注册中心 TCP 同号；27182=e 前五位，冷门、不撞 Redis/常见服务）
PROBE = b"MYAINET_DISCOVER_HUB?"          # 探测口令；应答器只认这一句


def lan_ip() -> str:
    """本机 LAN 出站 IP（连 8.8.8.8 不真发包，只为问内核挑哪张网卡）。
    隔离网络（无外网路由）UDP trick 会失败——退而枚举本机网卡挑私网 IPv4，
    别落到 127.0.0.1（那会让后面的 /24 扫描整个报废）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip.startswith(("192.168.", "10.")) or \
               (ip.startswith("172.") and ip.split(".")[1].isdigit() and 16 <= int(ip.split(".")[1]) <= 31):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


def discover_hub(timeout: float = 2.5, registry_port: int = 27182):
    """广播找建网机。找到返回 (registry_host, registry_port)，超时没找到返回 None。
    每 0.5s 重发一次探测（UDP 会丢包），收到合法应答立刻返回。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.5)
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                s.sendto(PROBE, ("255.255.255.255", DISCOVERY_PORT))
            except OSError:
                pass
            try:
                while True:                       # 把这 0.5s 窗口里收到的都读掉，挑出 hub 应答
                    data, _ = s.recvfrom(2048)
                    try:
                        info = json.loads(data.decode("utf-8", "replace"))
                    except Exception:
                        continue
                    if info.get("role") == "hub" and info.get("registry_host"):
                        return info["registry_host"], int(info.get("registry_port", registry_port))
            except socket.timeout:
                continue                            # 这轮没等到，再发一次
        return None
    finally:
        s.close()


def serve_discovery(registry_port: int = 27182, disc_port: int = DISCOVERY_PORT):
    """建网机后台线程：收到探测就回自己的 LAN IP（registry_server 起它）。
    端口被占（多半已有应答器在跑）→ 安静退出，不影响注册中心主职。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("", disc_port))
    except OSError:
        return
    while True:
        try:
            data, addr = s.recvfrom(2048)
        except OSError:
            continue
        if data.strip() == PROBE:
            reply = json.dumps({
                "role": "hub",
                "registry_host": lan_ip(),
                "registry_port": registry_port,
                "name": socket.gethostname(),
            }, ensure_ascii=False).encode("utf-8")
            try:
                s.sendto(reply, addr)
            except OSError:
                pass


if __name__ == "__main__":
    found = discover_hub()
    if found:
        print(found[0])                            # 只打印 IP，方便 skill/agent 直接拿去用
        sys.exit(0)
    print("未发现建网机（确认它在同一局域网且在运行；跨网用 Tailscale 名字或手填 IP）", file=sys.stderr)
    sys.exit(1)
