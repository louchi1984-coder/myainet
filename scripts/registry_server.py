#!/usr/bin/env python3
"""
myaiweb: registry_server.py
零依赖注册中心 —— 把 sqlite + ThreadingTCPServer（都是标准库）用 RESP 胶水连起来，
顶替 Valkey/Redis。建网机上跑这一个 python 进程即可：三系统原生、不用 Docker/WSL。

只实现 myaiweb 真正用到的 5 条命令：SET(+EX) / GET / MGET / KEYS / DEL（外加 PING 等握手善意）。
客户端 registry_client.py 说的就是 RESP，一行不用改 —— RESP 只是线缆格式，不是 Valkey 本身。

存储落 sqlite(WAL)，进程崩了/重启不丢；过期惰性滤 + 定时清扫。
重活全甩给标准库（sqlite 管存储、ThreadingTCPServer 管连接），自己只担中间那层 RESP 胶水。

用法：python3 registry_server.py [--host 0.0.0.0] [--port 27182] [--db ~/.myaiweb/registry.db]
自测：另开终端 python3 registry_client.py 127.0.0.1
"""
from __future__ import annotations

import argparse
import os
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
import time
from fnmatch import fnmatchcase
from pathlib import Path

# Windows 后台启动鲁棒性（否则 print emoji/中文 会让 server 起不来）：
#   ① pythonw 启动时 sys.stdout/stderr = None → print 崩 → 先兜成 devnull；
#   ② GBK 控制台 → emoji/中文 print 崩 → reconfigure 成 utf-8。
# 自处理后，启动命令一句 `Start-Process pythonw ...registry_server.py` 就稳，外面不用再设编码。
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


# ── sqlite 存储（全局单连接 + 锁：这点写入量足够，且最稳，无跨线程 sqlite 隐患）──────
class Store:
    def __init__(self, db_path: str):
        p = Path(db_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(p), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")        # 崩了/重启不丢
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS kv("
            "key TEXT PRIMARY KEY, value BLOB, expires_at REAL)"
        )
        self.db.commit()
        self.lock = threading.Lock()                       # 只在 DB 调用期间持有，不含 socket I/O
        self.key_event = threading.Event()                 # 收到控制方公钥(pubkey:*)写入时置位 → 触发装门
        self.sync_event = threading.Event()                # 次建网机：本地 node/pubkey/status 写入时置位 → 触发往主同步

    def set(self, key: str, value: bytes, ttl: int | None):
        exp = time.time() + ttl if ttl else None
        with self.lock:
            self.db.execute(
                "INSERT INTO kv(key,value,expires_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
                (key, value, exp),
            )
            self.db.commit()

    def get(self, key: str):
        now = time.time()
        with self.lock:
            row = self.db.execute(
                "SELECT value FROM kv WHERE key=? AND (expires_at IS NULL OR expires_at>?)",
                (key, now),
            ).fetchone()
        return row[0] if row else None

    def mget(self, keys: list) -> list:
        if not keys:
            return []
        now = time.time()
        ph = ",".join("?" * len(keys))
        with self.lock:
            found = dict(self.db.execute(
                f"SELECT key,value FROM kv WHERE (expires_at IS NULL OR expires_at>?) AND key IN ({ph})",
                (now, *keys),
            ).fetchall())
        return [found.get(k) for k in keys]                # 缺/过期 → None（回 nil），保持顺序

    def keys(self, pattern: str) -> list:
        now = time.time()
        with self.lock:
            allk = [r[0] for r in self.db.execute(
                "SELECT key FROM kv WHERE expires_at IS NULL OR expires_at>?", (now,)
            ).fetchall()]
        return [k for k in allk if fnmatchcase(k, pattern)]  # 大小写敏感，跨平台一致

    def delete(self, keys: list) -> int:
        if not keys:
            return 0
        ph = ",".join("?" * len(keys))
        with self.lock:
            cur = self.db.execute(f"DELETE FROM kv WHERE key IN ({ph})", tuple(keys))
            self.db.commit()
            return cur.rowcount

    def sweep(self):
        with self.lock:
            self.db.execute(
                "DELETE FROM kv WHERE expires_at IS NOT NULL AND expires_at<=?", (time.time(),)
            )
            self.db.commit()


# ── RESP 编解码（与 registry_client 同一套规则，方向相反）──────────────────────────
def _read_request(rfile):
    """读一条客户端命令 → list[bytes]；EOF 返回 None。支持 RESP 数组 + 行内命令。"""
    line = rfile.readline()
    if not line:
        return None                       # EOF
    line = line.rstrip(b"\r\n")
    if not line:
        return []                         # 空行，忽略
    if line[:1] == b"*":                  # RESP 数组（我们 client / redis-cli 都发这个）
        n = int(line[1:])
        args = []
        for _ in range(n):
            hdr = rfile.readline().rstrip(b"\r\n")   # $len
            ln = int(hdr[1:])
            data = rfile.read(ln)
            rfile.read(2)                            # 吞掉结尾 \r\n
            args.append(data)
        return args
    return line.split()                   # 行内命令（如健康检查直接敲 PING）


def _bulk(b) -> bytes:
    if b is None:
        return b"$-1\r\n"
    if isinstance(b, str):
        b = b.encode("utf-8")
    return b"$%d\r\n%s\r\n" % (len(b), b)


def _array(items) -> bytes:
    return b"*%d\r\n" % len(items) + b"".join(_bulk(i) for i in items)


# ── 命令分发（只认 myaiweb 用到的那几条，其余善意搪塞或 -ERR）─────────────────────
def dispatch(store: Store, args: list) -> bytes:
    cmd = args[0].decode("utf-8", "replace").upper()

    if cmd == "SET":
        key, value = args[1].decode("utf-8", "replace"), args[2]
        ttl = int(args[4]) if len(args) >= 5 and args[3].upper() == b"EX" else None
        store.set(key, value, ttl)
        if key.startswith("pubkey:"):                 # 新控制方公钥 → 触发"装进本机门"
            store.key_event.set()
        if key.split(":", 1)[0] in ("node", "pubkey", "status"):
            store.sync_event.set()                     # 次建网机：触发往主同步（主上没这线程，置位无害）
        return b"+OK\r\n"

    if cmd == "GET":
        return _bulk(store.get(args[1].decode("utf-8", "replace")))

    if cmd == "MGET":
        return _array(store.mget([a.decode("utf-8", "replace") for a in args[1:]]))

    if cmd == "KEYS":
        ks = store.keys(args[1].decode("utf-8", "replace"))
        return _array([k.encode("utf-8") for k in ks])

    if cmd == "DEL":
        return b":%d\r\n" % store.delete([a.decode("utf-8", "replace") for a in args[1:]])

    if cmd == "PING":
        return _bulk(args[1]) if len(args) > 1 else b"+PONG\r\n"

    # 给手动 redis-cli 调试留点善意（我们自己的 client 根本不发这些）
    if cmd in ("SELECT", "CLIENT"):
        return b"+OK\r\n"
    if cmd in ("COMMAND", "CONFIG"):
        return b"*0\r\n"

    return b"-ERR unknown command '%s'\r\n" % cmd.encode("utf-8", "replace")


# ── 网络层（一连接一线程；锁不含 socket I/O → 慢/死客户端拖不垮别人）──────────────
class _Handler(socketserver.StreamRequestHandler):
    timeout = 60          # 单连接超时，死连不长占线程

    def handle(self):
        store = self.server.store
        try:
            while True:
                try:
                    args = _read_request(self.rfile)
                except Exception:
                    break                 # 协议错位 → 关连接（避免 desync 滚雪球）
                if args is None:
                    break                 # EOF
                if not args:
                    continue              # 空行
                try:
                    reply = dispatch(store, args)
                except Exception as e:
                    reply = b"-ERR %s\r\n" % str(e).encode("utf-8", "replace")
                self.wfile.write(reply)
                self.wfile.flush()
        except (OSError, socket.timeout):
            pass                          # 客户端断开/超时，正常收尾


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, store):
        super().__init__(addr, _Handler)
        self.store = store


def _sweeper(store, every=60):
    while True:
        time.sleep(every)
        try:
            store.sweep()
        except Exception:
            pass


def _key_installer(port, event):
    """注册中心收到控制方公钥 → 把它装进本机门（免密：建网机自己这扇门，靠最可靠的注册中心来装，不依赖 patrol 起没起）。
    启动先装一遍库里已有的，之后每次有 pubkey 写入再装（事件驱动、不轮询）。装门复用 keysync.install：幂等、Win 管理员自动写对文件 + 设 ACL。"""
    import io
    from contextlib import redirect_stdout
    while True:
        try:
            from keysync import install
            with redirect_stdout(io.StringIO()):       # keysync 内部 print 吞掉，不刷注册中心日志
                install("127.0.0.1", port, dry=False)
        except Exception:
            pass
        event.wait()        # 等下一把公钥写入
        event.clear()
        time.sleep(0.5)     # 小去抖：一串写入合一次装


def _ts_bin():
    """找 tailscale 可执行（PATH → mac/win 兜底路径）。"""
    import shutil
    ts = shutil.which("tailscale")
    if ts:
        return ts
    for c in ("/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
              r"C:\Program Files\Tailscale\tailscale.exe"):
        if Path(c).exists():
            return c
    return None


def _my_tailscale_ip(ts):
    try:
        r = subprocess.run([ts, "ip", "-4"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:            # tailscale 没起/没登录 → 别拿空 stdout 当「没 IP」往下走
            return None
        for line in (r.stdout or "").splitlines():
            if line.strip().startswith("100."):
                return line.strip()
    except Exception:
        pass
    return None


def _find_main_on_tailnet(port):
    """次找主：列 tailnet peer（tailscale status），挨个探谁的 <port> 是注册中心（PING→PONG）。
    返回主的 Tailscale IP，没找到 None。多机才真能验；单主网够用，多个次时建议显式给 --main-host。"""
    import re
    ts = _ts_bin()
    if not ts:
        return None
    try:
        r = subprocess.run([ts, "status"], capture_output=True, text=True, timeout=8)
        if r.returncode != 0:            # status 失败（没登录/服务挂）→ 别拿空输出当「tailnet 没 peer」
            return None
        out = r.stdout or ""
    except Exception:
        return None
    me = _my_tailscale_ip(ts)
    for ip in re.findall(r"100\.\d+\.\d+\.\d+", out):
        if ip == me:
            continue
        try:
            with socket.create_connection((ip, port), timeout=1.5) as s:
                s.sendall(b"*1\r\n$4\r\nPING\r\n")
                if b"PONG" in s.recv(64):
                    return ip                  # 这台在 port 上应 PING → 就是注册中心（主）
        except OSError:
            continue
    return None


def _sync_to_main(store, main_host, main_port):
    """次建网机：把本地注册中心同步给主 —— 整批推 node:*/pubkey:*/status:* 上去 + 拉主的控制方公钥下来给本地装。
    事件驱动（本地一有写入就推）+ 低频心跳（60s，捞主那边的下行变化）。零依赖，用 registry_client。
    main_host 可为具体 Tailscale 地址，或 'auto'（列 tailnet peer 自动探）。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from registry_client import rset, rget, rkeys
    while True:
        host = _find_main_on_tailnet(main_port) if main_host == "auto" else main_host
        if host:
            try:
                # 上行：本地 node/pubkey/status 整批推给主
                import json
                my_name = socket.gethostname()
                for pat in ("node:*", "pubkey:*", "status:*"):
                    for k in store.keys(pat):
                        v = store.get(k)
                        if v is None:
                            continue
                        if pat == "node:*":
                            # 给外地节点卡盖来源戳：主自己 ping 不到它们（不在主 LAN、不上 Tailscale），
                            # 大屏据此显示「已注册（远程）」而非误判离线。解析失败就原样推、不拦同步。
                            try:
                                card = json.loads(v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v)
                                card["synced_from"] = my_name
                                v = json.dumps(card, ensure_ascii=False).encode("utf-8")
                            except Exception:
                                pass
                        rset(host, main_port, k, v)        # v 是 bytes，registry_client 直接收
                # 下行：主的控制方公钥拉回本地（本地节点据此装主控钥匙；次自己的门也补上）
                pulled = False
                for k in rkeys(host, main_port, "pubkey:*"):
                    v = rget(host, main_port, k)
                    if v:
                        store.set(k, v.encode("utf-8") if isinstance(v, str) else v, None)
                        pulled = True
                if pulled:
                    store.key_event.set()
            except Exception:
                pass
        store.sync_event.wait(timeout=60)     # 本地一有写入立刻醒；否则 60s 心跳捞主的下行
        store.sync_event.clear()
        time.sleep(0.5)                       # 小去抖：一串写入合一次推


def main():
    ap = argparse.ArgumentParser(description="myaiweb 零依赖注册中心（顶替 Valkey，无需 Docker）")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址（默认全网卡，LAN+Tailscale 都够得到）")
    ap.add_argument("--port", type=int, default=27182)
    ap.add_argument("--db", default="~/.myaiweb/registry.db")
    ap.add_argument("--main-host", default=None,
                    help="次建网机用：把本地注册中心同步给主（填主的 Tailscale 地址，或 'auto' 列 tailnet 自动探）。不填=自己是主")
    ap.add_argument("--main-port", type=int, default=None, help="主注册中心端口（默认同 --port）")
    args = ap.parse_args()

    store = Store(args.db)
    threading.Thread(target=_sweeper, args=(store,), daemon=True).start()
    # 局域网自动发现：起个 UDP 应答器，新机器广播「建网机在哪」就回本机 LAN IP（discover.py）。
    # 附加功能：导入/起线程失败都不拖累注册中心主职。
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from discover import serve_discovery
        threading.Thread(target=serve_discovery, args=(args.port,), daemon=True).start()
    except Exception:
        pass
    srv = _Server((args.host, args.port), store)
    # 注册中心收到控制方公钥 → 自己装进本机门（免密，不靠 patrol）。附加功能，失败不拖累主职。
    try:
        threading.Thread(target=_key_installer, args=(args.port, store.key_event), daemon=True).start()
    except Exception:
        pass
    # 次建网机：把本地注册中心同步给主（整批推上去 + 拉控制方公钥下来）。不填 --main-host = 自己是主、不同步。
    if args.main_host:
        mport = args.main_port or args.port
        try:
            threading.Thread(target=_sync_to_main, args=(store, args.main_host, mport), daemon=True).start()
            print(f"   🔗 次建网机：同步本地注册中心 → 主 {args.main_host}:{mport}")
        except Exception:
            pass
    print(f"📒 myaiweb 注册中心已起：{args.host}:{args.port}  db={Path(args.db).expanduser()}")
    print("   说 RESP（SET/GET/MGET/KEYS/DEL），registry_client.py 直连，零 Valkey/Docker。Ctrl-C 退出。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n收到中断，关闭。")
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    main()
