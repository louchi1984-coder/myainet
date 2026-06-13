#!/usr/bin/env python3
"""
myaiweb: netprobe.py
探测本机所在网络的"远程可达性画像"，输出网络分类 + 推荐的远程接入方案。
把手动那套探测固化下来：公网 v4（是否 CGNAT）、公网 v6、NAT 类型（是否对称）、
是否蜂窝、墙内/墙外，据此给出该走哪条远程接入路线。

核心原则：
  1. 所有外部探测都【绕过系统代理】。Clash/Surge 等会把出口 IP、地理位置带歪
     （例如出口变成代理落地的境外 IP），导致判断全错。
  2. 纯标准库，跨平台（macOS / Linux / Windows），不依赖 pip。
  3. 探不到的就老实说"未知"，不臆断。

用法：python3 netprobe.py        # 人类可读 + key=value
      python3 netprobe.py --json # 追加一行 JSON，供 skill 解析
"""
import json
import os
import socket
import struct
import sys
import urllib.request

# Windows GBK 终端会让 emoji/中文崩溃，强制 UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 绕过代理的 HTTP（关键：否则拿到的是代理落地的 IP/国家）──────────────────
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": "myaiweb-netprobe/1.0"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# ── 本机地址 ──────────────────────────────────────────────────────────────────

def local_v4():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return ""


def global_v6():
    """本机出站全局 IPv6（2000::/3）。没有公网 v6 则返回空串。"""
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as s:
            s.connect(("2400:3200::1", 80))  # 阿里 DNS v6，仅选源地址，不发包
            ip = s.getsockname()[0].split("%")[0]
        first = int(ip.split(":")[0], 16)
        if 0x2000 <= first <= 0x3FFF:        # 全局单播
            return ip
    except Exception:
        pass
    return ""


def tcp_reach(host, port, family, timeout=4):
    """真的去 TCP 连一下，证明可达（不只是有地址）。"""
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def is_private_v4(ip):
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except Exception:
        return False
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)


def is_cgnat_v4(ip):
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
    except Exception:
        return False
    return a == 100 and 64 <= b <= 127      # 100.64.0.0/10


# ── 出口 IP + 运营商 + 是否蜂窝 + 国家（ip-api 一次给齐）──────────────────────

def ip_intel():
    out = {"ext_v4": "", "isp": "", "org": "", "country": "", "mobile": None}
    # ① ip-api：直接给 mobile 布尔（最理想）
    try:
        d = http_json("http://ip-api.com/json/?fields=query,country,countryCode,isp,org,mobile,proxy", timeout=8)
        out["ext_v4"] = d.get("query", "")
        out["isp"] = d.get("isp", "")
        out["org"] = d.get("org", "")
        out["country"] = d.get("countryCode", "") or d.get("country", "")
        out["mobile"] = bool(d.get("mobile", False))
        return out
    except Exception:
        pass
    # ② ip.sb/geoip：给 isp/org/country（无 mobile 字段，蜂窝由运营商名推断）
    try:
        d = http_json("https://api.ip.sb/geoip", timeout=8)
        out["ext_v4"] = d.get("ip", "")
        out["isp"] = d.get("isp", "")
        out["org"] = d.get("organization", "") or d.get("asn_organization", "")
        out["country"] = d.get("country_code", "") or d.get("country", "")
        return out
    except Exception:
        pass
    # ③ 兜底：只拿出口 IP
    for url in ("https://api.ip.sb/ip", "https://ifconfig.me/ip"):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
            with _OPENER.open(req, timeout=6) as r:
                out["ext_v4"] = r.read().decode().strip()
                break
        except Exception:
            continue
    return out


# ── STUN：判断 NAT 是否对称（对称 = P2P 打洞难，mesh 只能走中继）─────────────

def stun_mapped(host, port=3478, timeout=3):
    """STUN Binding Request → (public_ip, public_port) 或 None。"""
    MAGIC = 0x2112A442
    txid = os.urandom(12)
    msg = struct.pack(">HHI", 0x0001, 0, MAGIC) + txid
    try:
        addr = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(msg, addr)
        data, _ = s.recvfrom(2048)
        s.close()
    except Exception:
        return None
    if len(data) < 20 or data[8:20] != txid:
        return None
    msg_len = struct.unpack(">H", data[2:4])[0]
    i, end = 20, min(20 + msg_len, len(data))
    while i + 4 <= end:
        atype, alen = struct.unpack(">HH", data[i:i + 4])
        val = data[i + 4:i + 4 + alen]
        i += 4 + alen + ((4 - alen % 4) % 4)
        if atype in (0x0020, 0x0001) and len(val) >= 8 and val[1] == 0x01:  # XOR-MAPPED / MAPPED, IPv4
            xport = struct.unpack(">H", val[2:4])[0]
            xaddr = struct.unpack(">I", val[4:8])[0]
            if atype == 0x0020:
                xport ^= MAGIC >> 16
                xaddr ^= MAGIC
            return socket.inet_ntoa(struct.pack(">I", xaddr)), xport
    return None


def nat_is_symmetric():
    """从多台 STUN 服务器看映射是否一致；不一致=对称 NAT。返回 True/False/None(未知)。"""
    servers = [("stun.miwifi.com", 3478),        # 域名国内，墙内可达
               ("stun.cloudflare.com", 3478),
               ("stun.l.google.com", 19302),
               ("stun1.l.google.com", 19302)]
    seen = []
    for h, p in servers:
        m = stun_mapped(h, p)
        if m:
            seen.append(m)
        if len(seen) >= 2:
            break
    if len(seen) < 2:
        return None
    return seen[0] != seen[1]          # 映射的 IP:Port 因目标而变 → 对称


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    want_json = "--json" in sys.argv

    lan_v4 = local_v4()
    v6 = global_v6()
    v6_reach = tcp_reach("2400:3200::1", 53, socket.AF_INET6) if v6 else False
    intel = ip_intel()
    ext_v4 = intel["ext_v4"]
    cellular = bool(intel["mobile"]) or any(
        k in (intel["isp"] + intel["org"]).lower()
        for k in ("mobile", "cellular", "移动", "lte", "5g")
    )
    in_china = (intel["country"] or "").upper() == "CN"
    # GFW：墙内且连不上 google → 大概率墙内受限
    behind_gfw = in_china and not tcp_reach_http("https://www.google.com")
    sym = nat_is_symmetric()

    # 入站能力判断
    cgnat_v4 = is_cgnat_v4(ext_v4)
    public_v4_direct = bool(ext_v4) and not cgnat_v4 and not is_private_v4(ext_v4)
    # ext_v4 == 本机网卡 → 公网 IP 直接在机器上（可直连入站）
    on_host_v4 = bool(ext_v4) and ext_v4 == lan_v4

    # ── 分类 + 推荐 ──
    # 拨出式：默认 Tailscale（不需要 VPS、开源客户端、端到端加密）；闭源/不稳的只作明确备选，绝不默认。
    dialout_main = "Tailscale（不需要 VPS、开源客户端、端到端加密、免费设备无上限）"
    dialout_alt = ("图省事、且不介意闭源/第三方中转，可备选："
                   + ("蒲公英（国内中继，但闭源国产）/ ZeroTier" if in_china else "ZeroTier"))
    cn_note = ("（墙内提示：Tailscale 中继在海外，relay-bound 时延迟偏高，但够用）"
               if in_china else "")

    if cellular:
        net_class = "cellular"
        rec = dialout_main
        why = "蜂窝运营商上游封死入站（v4 CGNAT、v6 也拦），只能拨出式中继。" + cn_note + " 备选：" + dialout_alt
    elif v6 and v6_reach:
        net_class = "public-ipv6"
        rec = "IPv6 直连 + DDNS（最快、零中继、零第三方）"
        why = "有可用公网 IPv6。需确认 ① 光猫/路由放行入站 v6 ② 对端也有 v6；不行再回退 → " + dialout_main
    elif public_v4_direct or on_host_v4:
        net_class = "public-ipv4"
        rec = "端口映射 / 直接监听（公网 IPv4，最快、零依赖、零第三方）"
        why = "检测到公网 IPv4，可直接被连入。"
    else:
        net_class = "cgnat-fixed"
        rec = dialout_main
        why = "固网但 CGNAT、无可用公网入站，只能拨出式。" + cn_note + " 备选：" + dialout_alt

    sym_txt = {True: "是（对称 NAT，mesh 多走中继）", False: "否（可能 P2P 直连）", None: "未知"}[sym]

    # ── key=value（供 register/skill 解析）──
    kv = {
        "net_lan_v4": lan_v4,
        "net_ext_v4": ext_v4,
        "net_cgnat": "yes" if cgnat_v4 else "no",
        "net_v6_global": v6 or "none",
        "net_v6_reachable": "yes" if v6_reach else "no",
        "net_nat_symmetric": {True: "yes", False: "no", None: "unknown"}[sym],
        "net_cellular": "yes" if cellular else "no",
        "net_isp": intel["isp"],
        "net_country": intel["country"],
        "net_behind_gfw": "yes" if behind_gfw else "no",
        "net_class": net_class,
        "net_recommend": rec,
    }
    for k, val in kv.items():
        print(f"{k}={val}")

    # ── 人类可读 ──
    print()
    print("╔══════════════════ myaiweb 网络画像 ══════════════════")
    print(f"║  出口 IPv4   : {ext_v4 or '?'}  ({intel['isp'] or '?'})")
    print(f"║  是否 CGNAT  : {'是（无公网入站）' if cgnat_v4 else '否'}")
    print(f"║  公网 IPv6   : {v6 + ('  可达' if v6_reach else '  不可达') if v6 else '无'}")
    print(f"║  NAT 对称?   : {sym_txt}")
    print(f"║  蜂窝网络?   : {'是' if cellular else '否'}    地区: {intel['country'] or '?'}" + ("  (墙内受限)" if behind_gfw else ""))
    print("╠══════════════════════════════════════════════════════")
    print(f"║  网络类型    : {net_class}")
    print(f"║  推荐方案    : {rec}")
    print("╚══════════════════════════════════════════════════════")
    print(f"原因：{why}")
    print()
    print("脚本无法自动判断、需你确认的：")
    print("  · 所有机器是否基本在同一局域网？是 → 直接 LAN SSH，以上方案都不用。")

    if want_json:
        print(json.dumps(kv, ensure_ascii=False))


def tcp_reach_http(url, timeout=4):
    """绕过代理快速探一个 https 是否能连（用于 GFW 判断）。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with _OPENER.open(req, timeout=timeout):
            return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
