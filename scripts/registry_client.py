#!/usr/bin/env python3
"""
myainet: registry_client.py
零依赖注册中心客户端：裸 socket 说 RESP，连 registry_server.py（或任何 RESP 服务）。
只要标准库 —— 全新机器 / 便携版 Python 都能读写注册中心。

瞬断自动重连一次（中继 DERP 偶有抖动），仍失败则该函数返回 False/None，不抛、不堵死。
（注：RESP 只是线缆格式，不是 Valkey/Redis —— 连的就是我们自己的 registry_server.py。）

⚠️ 读类接口（rget/rkeys/rmap）失败时返回空 —— 「连不上」和「真的没这个键」在返回值上**不可区分**。
   常驻进程（dashboard/patrol）靠这个优雅降级、没问题；但 agent 跑「看网络状态」时，
   空结果会被误读成「注册表空了，要恢复」（真实事故，2026-06-12）。
   所以**判断网络状态前先 `reachable(host, port)`** —— 它把「够不着」和「空」分清楚。

自测：python3 registry_client.py <host> [port]
"""
from __future__ import annotations

import socket
import time


def reachable(host, port, timeout=3) -> bool:
    """注册中心连得上吗（只握手，不发命令）。读到空结果前先问它：
    True+空 = 真没数据；False = 够不着（地址/端口错，或建网机没在跑）—— 别把后者当成「注册表空」。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# ── RESP 编解码（纯标准库）────────────────────────────────────────────────────

def _encode(args) -> bytes:
    out = [f"*{len(args)}\r\n".encode()]
    for a in args:
        b = a if isinstance(a, bytes) else str(a).encode("utf-8")
        out.append(f"${len(b)}\r\n".encode())   # 注意：长度按字节算（中文 UTF-8 多字节）
        out.append(b)
        out.append(b"\r\n")
    return b"".join(out)


def _read_reply(f):
    line = f.readline().rstrip(b"\r\n")
    if not line:
        return None
    t, rest = line[:1], line[1:]
    if t == b"+":                       # 简单字符串
        return rest.decode("utf-8", "replace")
    if t == b"-":                       # 错误
        raise RuntimeError(rest.decode("utf-8", "replace"))
    if t == b":":                       # 整数
        return int(rest)
    if t == b"$":                       # bulk 字符串
        n = int(rest)
        if n < 0:
            return None
        return f.read(n + 2)[:-2].decode("utf-8", "replace")
    if t == b"*":                       # 数组
        n = int(rest)
        return None if n < 0 else [_read_reply(f) for _ in range(n)]
    return None


def _raw(host, port, *args, timeout=5, _retry=True):
    """裸 socket 发一条命令、读一个回复。瞬断重连一次；仍失败则抛。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as s:
            s.sendall(_encode(args))
            with s.makefile("rb") as f:
                return _read_reply(f)
    except (OSError, ConnectionError):
        if _retry:                       # 抖一下重连一次，对付中继瞬断
            time.sleep(0.2)
            return _raw(host, port, *args, timeout=timeout, _retry=False)
        raise


# ── 对外接口（失败返回 False/None，不堵死）────────────────────────────────────────

def rset(host, port, key, value, ttl=None) -> bool:
    args = ["SET", key, value] + (["EX", str(ttl)] if ttl else [])
    try:
        return _raw(host, port, *args) == "OK"
    except Exception:
        return False


def rget(host, port, key):
    try:
        return _raw(host, port, "GET", key)
    except Exception:
        return None


def rkeys(host, port, pattern) -> list:
    try:
        return _raw(host, port, "KEYS", pattern) or []
    except Exception:
        return []


def rmap(host, port, pattern, timeout=8) -> dict:
    """一条连接里 KEYS + MGET，把匹配 pattern 的所有键值一次拉回 {key: value}。
    省往返：N 个键从「(N+1) 次新连接」压成「1 条连接 2 条命令」——中继(DERP ~170ms)下尤其值。
    瞬断重连一次；快路径仍失败则退回逐个 rkeys+rget，绝不堵死。"""
    for attempt in (1, 2):
        try:
            with socket.create_connection((host, int(port)), timeout=timeout) as s:
                with s.makefile("rb") as f:
                    s.sendall(_encode(["KEYS", pattern]))
                    keys = _read_reply(f) or []
                    if not keys:
                        return {}
                    s.sendall(_encode(["MGET", *keys]))
                    vals = _read_reply(f) or []
                    return {k: v for k, v in zip(keys, vals) if v is not None}
        except Exception:
            if attempt == 1:
                time.sleep(0.2)
                continue
    # 兜底：退回逐个（仍走裸 socket，零依赖）
    out = {}
    for k in rkeys(host, port, pattern):
        v = rget(host, port, k)
        if v is not None:
            out[k] = v
    return out


def rdel(host, port, key) -> bool:
    try:
        _raw(host, port, "DEL", key)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    import os, sys
    # Windows：pythonw 无 stdout / GBK 控制台打 emoji 会崩 → 先兜，免得自测炸（line 135 的 ✅）
    os.environ.setdefault("PYTHONUTF8", "1")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    h = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    p = int(sys.argv[2]) if len(sys.argv) > 2 else 27182
    print(f"测试注册中心 {h}:{p}（裸 socket RESP）...")
    if not reachable(h, p):       # 先分清「够不着」vs「空」——直接发命令会被静默吞成空，看不出是连不上
        print(f"❌ 连不上 {h}:{p} —— 不是「注册表空」，是够不着。检查：地址/端口对不对、建网机注册中心在不在跑。")
        sys.exit(2)
    ok = rset(h, p, "myainet:selftest", "hello-中文-myainet")
    got = rget(h, p, "myainet:selftest")
    rdel(h, p, "myainet:selftest")
    print(f"  SET ok={ok}   GET={got!r}")
    print("✅ 裸 socket RESP 通了，零依赖可用" if ok and got == "hello-中文-myainet" else "❌ 没通")
