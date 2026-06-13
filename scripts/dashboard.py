#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 注：本文件 HTML 模板里有超长 JS 单行 + 中文，缺 coding 声明时旧版 tokenizer 会按缓冲区
# 分块校验 UTF-8、在多字节字符边界处报「Non-UTF-8 code」。声明编码后走增量解码，跨边界安全。
"""
myaiweb: dashboard.py
在建网机上启动 myaiweb 网络状态仪表盘（HTTP 服务），iPad/浏览器均可访问。

用法：
  python3 dashboard.py --registry-host 127.0.0.1
  python3 dashboard.py --registry-host 192.168.1.10 --port 7700
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Windows GBK 终端会让 emoji 打印崩溃，强制 UTF-8；pythonw 后台启动还会无 stdout → 兜 devnull
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout is None:                      # pythonw 后台启动无 stdout → print 会崩
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── 注册中心读取（零依赖裸 socket RESP，见 registry_client）──

sys.path.insert(0, str(Path(__file__).parent))
try:
    from registry_client import rkeys as _vkeys, rget as _vget
except ImportError:
    _vkeys = _vget = None


def read_nodes(host: str, port: int) -> list[dict]:
    keys = _vkeys(host, port, "node:*") if _vkeys else []
    nodes = []
    for key in keys:
        val = _vget(host, port, key) if _vget else None
        if not val:
            continue
        try:
            nodes.append(json.loads(val))
        except Exception:
            continue
    return nodes


def read_tasks(host: str, port: int) -> list[dict]:
    keys = _vkeys(host, port, "task:*") if _vkeys else []
    tasks = []
    for key in keys:
        val = _vget(host, port, key) if _vget else None
        if not val:
            continue
        try:
            tasks.append(json.loads(val))
        except Exception:
            continue
    return tasks


# ── Agent Gateway ────────────────────────────────────────────────────────────

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text or "")


def _which_any(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _agent_command(agent: str, exe: str, workdir: Path) -> list[str]:
    """非交互调用。提示词一律走 stdin（codex/claude 的 "-"/无参形式）——
    不放 argv：Windows 下 .cmd 要经 cmd.exe /c 包装，多行/中文参数会被换行截断、引号转义也靠不住。"""
    if agent == "codex":
        # --skip-git-repo-check：Console 的工作目录（默认家目录）多半不是 git 仓库，不加必拒跑
        return [exe, "exec", "--skip-git-repo-check", "-"]
    if agent == "claude":
        return [exe, "-p"]
    return [exe, "run", "--dir", str(workdir)]   # opencode run 无参也读 stdin


CONSOLE_STATE = Path.home() / ".myaiweb" / "console.json"


def _host_agent() -> str | None:
    """skill 装在谁家，谁就是这台机器实测能跑的 agent（装下本 skill 的那位）。
    如 ~/.config/opencode/skills/... → opencode；~/.claude/skills/... → claude。"""
    p = str(Path(__file__).resolve()).lower().replace("\\", "/")
    for agent, marker in (("opencode", "/opencode/"), ("claude", "/.claude/"), ("codex", "/.codex/")):
        if marker in p:
            return agent
    return None


def _preferred_agent() -> str | None:
    """上次真答成功的 agent 优先（~/.myaiweb/console.json）；没记录就看 skill 装在谁家。"""
    try:
        return json.loads(CONSOLE_STATE.read_text(encoding="utf-8")).get("agent") or _host_agent()
    except Exception:
        return _host_agent()


def _remember_agent(agent: str) -> None:
    try:
        CONSOLE_STATE.parent.mkdir(parents=True, exist_ok=True)
        CONSOLE_STATE.write_text(json.dumps({"agent": agent}), encoding="utf-8")
    except Exception:
        pass


def local_agents() -> list[tuple[str, str]]:
    """建网机上装了的 agent。优先序：上次成功的 / skill 东家 → 其余按 codex → claude → opencode。
    谁真答过题谁排第一——别让每条消息都先烧一遍注定失败的。"""
    candidates = (
        ("codex", _which_any("codex.cmd", "codex")),
        ("claude", _which_any("claude.cmd", "claude")),
        ("opencode", _which_any("opencode.cmd", "opencode")),
    )
    found = [(a, e) for a, e in candidates if e]
    pref = _preferred_agent()
    found.sort(key=lambda t: 0 if t[0] == pref else 1)   # 稳定排序：优先者提前，其余保持原序
    return found


def _chat_context(message: str, history: list | None) -> str:
    """拼单轮提示词：身份 + 通道约束 + 最近几轮历史（agent 进程无状态，记忆只能靠这里带）。"""
    lines = [
        "你是 myaiweb 个人 AI 网络的建网机助手，通过大屏 Console 接收浏览器/手机发来的消息。",
        "这是单轮非交互调用：你无法追问、也等不到用户确认。直接给出答案或结果；",
        "涉及删除、重装、清理、改配置等破坏性操作时不要执行，回复说明该操作请用户在终端人工跑。",
        "请用中文简洁回答。",
        "",
    ]
    turns = [t for t in (history or []) if isinstance(t, dict) and t.get("text")][-8:]
    if turns:
        lines.append("最近的对话（供延续上下文）：")
        for t in turns:
            who = "用户" if t.get("role") == "user" else "你"
            lines.append(f"[{who}] {str(t['text'])[:1000]}")
        lines.append("")
    lines.append(f"用户本条消息：\n{message}")
    return "\n".join(lines)


def run_agent_chat(message: str, history: list | None = None,
                   cwd: str | None = None, timeout: int = 300) -> dict:
    """把 Console 消息桥接到建网机本地可用 agent；失败自动换下一个（codex→claude→opencode）。"""
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "消息不能为空"}
    if len(message) > 8000:
        return {"ok": False, "error": "消息太长，请控制在 8000 字以内"}

    agents = local_agents()
    if not agents:
        return {"ok": False, "error": "找不到 codex / claude / opencode，请先在建网机安装并登录一个 agent"}
    workdir = Path(cwd).expanduser() if cwd else Path.home()
    if not workdir.exists() or not workdir.is_dir():
        return {"ok": False, "error": f"工作目录不存在：{workdir}"}

    context = _chat_context(message, history)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # CREATE_NO_WINDOW：别让建网机桌面每条消息闪一个黑窗
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

    started = time.time()
    deadline = started + timeout                 # 总预算：降级不叠加超时（否则最坏 N×timeout，浏览器早断了）
    failures = []
    for agent, agent_exe in agents:
        left = max(15, int(deadline - time.time()))   # 后备 agent 至少给 15s 报快错的机会
        try:
            cmd = _agent_command(agent, agent_exe, workdir)
            if sys.platform == "win32":
                cmd = ["cmd.exe", "/d", "/c", subprocess.list2cmdline(cmd)]
            result = subprocess.run(
                cmd, input=context,
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=left, env=env, cwd=str(workdir), creationflags=creationflags
            )
            stdout = strip_ansi(result.stdout).strip()
            stderr = strip_ansi(result.stderr).strip()
            if result.returncode == 0:
                _remember_agent(agent)           # 记住真答成功的，下次它排第一
                return {
                    "ok": True,
                    "agent": agent,
                    "cwd": str(workdir),
                    "duration_ms": int((time.time() - started) * 1000),
                    "output": stdout,
                    "error": "",
                    "returncode": 0,
                }
            failures.append(f"{agent}: {(stderr or stdout or '退出码 ' + str(result.returncode))[-300:]}")  # 取尾部：致命错误通常在最后，开头多是警告
        except subprocess.TimeoutExpired:
            failures.append(f"{agent}: 响应超时（>{left}s）")
        except Exception as e:
            failures.append(f"{agent}: {e}")
    return {
        "ok": False,
        "cwd": str(workdir),
        "duration_ms": int((time.time() - started) * 1000),
        "error": "所有 agent 都失败了：\n" + "\n".join(failures),
    }


# ── Ping ──────────────────────────────────────────────────────────────────────

def ping_node(ip: str, timeout: float = 1.5) -> float | None:
    """Ping 节点，返回延迟（ms）；不可达则返回 None。兼容 macOS / Linux / Windows。"""
    if not ip or ip in ("unknown", ""):
        return None
    try:
        if sys.platform == "win32":
            # Windows: ping -n 1 -w <ms>
            cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
        elif sys.platform == "darwin":
            cmd = ["ping", "-c", "1", "-t", str(max(1, int(timeout))), ip]
        else:
            cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout + 2)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                # macOS/Linux : "time=X.X ms"
                # Windows EN  : "time=Xms"  或  "time<1ms"（自身 IP 超快时）
                # Windows CN  : "时间=Xms"  或  "时间<1ms"
                for marker in ("time=", "time<", "Time=", "Time<", "时间=", "时间<"):
                    if marker in line:
                        raw = line.split(marker)[1].split()[0]
                        raw = raw.lower().rstrip("ms").strip()
                        # "time<1ms" → raw="1"，直接用 1ms 表示 <1ms
                        if not raw or raw == "":
                            return 0.5
                        try:
                            return round(float(raw), 1)
                        except ValueError:
                            continue
            # returncode=0 说明节点在线，只是解析不到具体延迟，返回 0 而非 None
            return 0.0
    except Exception:
        pass
    return None


def tcp_ping(ip: str, port: int = 22, timeout: float = 1.5) -> float | None:
    """ICMP 被节点防火墙拦时的兜底探活（Win 默认拦 ping 但 SSH 通）：TCP 摸 22 口，连上=在线。"""
    if not ip or ip in ("unknown", ""):
        return None
    try:
        t0 = time.time()
        socket.create_connection((ip, port), timeout=timeout).close()
        return round((time.time() - t0) * 1000, 1)
    except OSError:
        return None


# ── 状态聚合 ──────────────────────────────────────────────────────────────────

def get_status(registry_host: str, registry_port: int) -> dict:
    """读取所有节点 + 任务，并发 ping，返回完整状态 dict。"""
    nodes = read_nodes(registry_host, registry_port)
    tasks = read_tasks(registry_host, registry_port)

    # 并发 ping（本机节点直接标在线，跳过 ping）
    local_hostname = socket.gethostname().lower()
    # UDP socket trick：与 sysinfo.py 完全相同，拿到真实出站 IP
    _my_ip = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as _s:
            _s.connect(("8.8.8.8", 80))
            _my_ip = _s.getsockname()[0]
    except Exception:
        pass

    ping_results: dict[str, float | None] = {}
    lock = threading.Lock()

    def do_ping(hostname: str, ip: str, ts_ip: str = ""):
        # hostname 或 IP 匹配本机 → Dashboard 还在跑，必然在线
        if hostname.lower() == local_hostname or (ip and ip == _my_ip):
            with lock:
                ping_results[hostname] = 0.0
            return
        ms = ping_node(ip)
        if ms is None and ts_ip:          # LAN ping 不通 → 试 Tailscale（跨 LAN / 异地也算在线）
            ms = ping_node(ts_ip)
        if ms is None:                    # ICMP 全拦（Win 节点防火墙默认）→ TCP 摸 SSH 口兜底
            ms = tcp_ping(ip)
        if ms is None and ts_ip:
            ms = tcp_ping(ts_ip)
        with lock:
            ping_results[hostname] = ms

    threads = []
    for node in nodes:
        net = node.get("network", {})
        ip = net.get("lan_ip", "")
        ts_ip = net.get("tailscale_ip", "")
        t = threading.Thread(target=do_ping, args=(node.get("hostname", ""), ip, ts_ip), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=5)

    # ── 兜底：本机节点强制标为在线（不依赖 ping 线程结果）──────────────────
    # 原因：UDP 探测 IP 可能与 注册中心 存储的 lan_ip 不匹配（多网卡/VPN 场景），
    # 或线程因任何原因未能在 timeout 内写入 ping_results。
    # 只要 hostname 或任一本机 IP 匹配，就直接设 0ms。
    try:
        all_my_ips = set()
        # UDP trick
        if _my_ip:
            all_my_ips.add(_my_ip)
        # 枚举本机所有 IPv4 地址
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            all_my_ips.add(info[4][0])
    except Exception:
        all_my_ips = {_my_ip} if _my_ip else set()

    for node in nodes:
        hn = node.get("hostname", "")
        ip = node.get("network", {}).get("lan_ip", "")
        if hn.lower() == local_hostname or ip in all_my_ips:
            ping_results[hn] = 0.0

    # ── 读取次建网机推送上来的状态（status:*，见 patrol.py）────────────────────
    # 主建网机 ping 不到别的 LAN 的私网 IP，那些节点的在线状态由各 LAN 的 hub
    # 本地探完 push 上来。这里读进来，给主 ping 不到的节点兜底。
    now_ts = int(time.time())
    PUSH_FRESH_SEC = 150       # 推送状态超过这个秒数算过期，不再当在线
    pushed_status = {}
    for skey in (_vkeys(registry_host, registry_port, "status:*") if _vkeys else []):
        sval = _vget(registry_host, registry_port, skey) if _vget else None
        if not sval:
            continue
        try:
            st = json.loads(sval)
            pushed_status[st.get("hostname", "")] = st
        except Exception:
            continue

    # 组装节点列表
    node_list = []
    latencies = []
    for node in nodes:
        hostname = node.get("hostname", "?")
        ip = node.get("network", {}).get("lan_ip", "")
        services = node.get("services", [])
        ms = ping_results.get(hostname)
        online = ms is not None
        last_seen = now_ts if online else 0
        seen_by = "本机巡检" if online else ""
        # 主 ping 不到（多半是别的 LAN 的私网 IP）→ 用次建网机推上来的新鲜状态兜底
        if not online:
            st = pushed_status.get(hostname)
            if st and st.get("online") and (now_ts - int(st.get("last_seen", 0))) <= PUSH_FRESH_SEC:
                online = True
                ms = st.get("latency_ms")
                last_seen = int(st.get("last_seen", 0))
                seen_by = st.get("hub", "次建网机")
        # 外地节点（经次同步上来、主自己够不到又没新鲜推送）：标「已注册（远程）」，不误判离线
        synced_from = node.get("synced_from", "")
        registered_remote = bool(synced_from) and not online
        if registered_remote:
            seen_by = f"已注册（远程·未探活）· 经 {synced_from}"
        if online and ms is not None:
            latencies.append(ms)
        node_list.append({
            "hostname":         hostname,
            "is_local":         hostname.lower() == local_hostname or ip in all_my_ips,
            "primary_role":     node.get("role") or node.get("primary_role", ""),
            "belongs_to":       node.get("belongs_to", ""),
            "is_infra_candidate": node.get("is_infra_candidate", False),
            "ip":               ip,
            "tailscale_ip":     node.get("network", {}).get("tailscale_ip", ""),
            "online":           online,
            "latency_ms":       ms,
            "last_seen":        last_seen,
            "seen_by":          seen_by,
            "synced_from":      synced_from,
            "registered_remote": registered_remote,
            "scores":           node.get("scores", {}),
            "hardware":         node.get("hardware", {}),
            "agents":           node.get("agents", {}),
            "models":           node.get("models", []),
            "cli_tools":        node.get("cli", node.get("cli_tools", node.get("tools", []))),
            "link":             node.get("link", {}),
            "accept":           node.get("accept", []),
            "services":         services,
            "workspace":        node.get("workspace"),   # 原生工作区契约（无=null，卡上不显示）
            "registered_at":    node.get("registered_at", 0),
        })

    # 排序：真建网机（含次）最前，候选其次，其余按名——001 永远是 hub，候选不抢位
    def _hublike(role):
        r = (role or "").lower()
        return "建网" in r or "hub" in r          # 中英都认（英文环境 role="hub" 也排前）
    node_list.sort(key=lambda n: (0 if _hublike(n["primary_role"])
                                  else (1 if n["is_infra_candidate"] else 2), n["hostname"]))

    online_count  = sum(1 for n in node_list if n["online"])
    remote_count  = sum(1 for n in node_list if n.get("registered_remote"))
    avg_latency   = round(sum(latencies) / len(latencies), 1) if latencies else 0
    active_tasks  = sum(1 for t in tasks if t.get("status") in ("running", "pending"))
    local_role    = next((n["primary_role"] for n in node_list if n["hostname"].lower() == local_hostname), "")

    return {
        "summary": {
            "total_nodes":   len(node_list),
            "online_nodes":  online_count,
            "remote_nodes":  remote_count,
            "offline_nodes": len(node_list) - online_count - remote_count,
            "active_tasks":  active_tasks,
            "total_tasks":   len(tasks),
            "avg_latency_ms": avg_latency,
        },
        "nodes":        node_list,
        "tasks":        tasks,
        "generated_at": int(time.time()),
        "local_role":   local_role,
    }


# ── HTML 模板 ─────────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MYAIWEB Control Surface</title>
<style>
  :root{
    --bg:#020303;--ink:#e9f0ef;--muted:#7c8582;--dim:#3e4744;--line:#1f2926;
    --panel:#070909;--green:#66f5ad;--red:#ff5161;--amber:#e6c46d;--paper:#dce6e3;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{min-height:100vh;background:
    linear-gradient(rgba(102,245,173,.04) 1px,transparent 1px),
    linear-gradient(90deg,rgba(102,245,173,.04) 1px,transparent 1px),var(--bg);
    background-size:32px 32px;color:var(--ink);font-family:"Avenir Next Condensed","Arial Narrow","Helvetica Neue",sans-serif;letter-spacing:0;overflow-x:hidden}
  button,textarea{font:inherit}
  .page{height:100vh;padding:14px clamp(14px,1.6vw,28px) 16px;display:flex;flex-direction:column;gap:13px;overflow:hidden}
  .hero{display:flex;align-items:center;gap:30px;border-bottom:1px solid var(--line);padding-bottom:12px;flex:none}
  .title{font-family:Impact,"Arial Black","Avenir Next Condensed",sans-serif;font-size:clamp(38px,4.6vw,62px);font-weight:900;line-height:.84;letter-spacing:-.02em;color:var(--paper);white-space:nowrap;text-transform:uppercase}
  .title sup{font-size:.18em;vertical-align:top;margin-left:6px;font-family:"SF Mono",Consolas,monospace;letter-spacing:0}
  .meta{flex:1;min-width:0;display:flex;flex-direction:column;gap:9px;font:700 11px/1.3 "SF Mono",Consolas,monospace;text-transform:uppercase;color:var(--ink)}
  .meta p{color:var(--muted);letter-spacing:.16em;font-size:10px}.meta .muted{color:var(--muted)}.meta-grid{display:flex;flex-wrap:wrap;gap:7px 26px;align-items:center}.meta-grid>span{white-space:nowrap}
  .stamp{text-align:right;color:var(--ink);flex:none;font:700 11px/1.3 "SF Mono",Consolas,monospace;text-transform:uppercase}.status-line{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin-top:6px}.status-dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 14px rgba(102,245,173,.8)}.status-dot.off{background:var(--red);box-shadow:none}
  .layout{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,400px);gap:16px;align-items:stretch}.stage{display:flex;flex-direction:column;min-height:0}.right{display:flex;flex-direction:column;gap:13px;min-height:0}
  .right .panel:first-of-type{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}.right .panel:first-of-type .activity{flex:1;min-height:0;overflow:auto}.right .panel:last-of-type{flex:none}.right .panel:last-of-type .log{max-height:230px}
  .section-head{display:flex;align-items:end;justify-content:space-between;gap:14px;margin-bottom:10px}.section-title{font:900 20px/1 "Avenir Next Condensed","Arial Narrow",sans-serif;text-transform:uppercase}.section-note{font:700 11px/1.2 "SF Mono",Consolas,monospace;color:var(--muted);text-transform:uppercase}
  .topology{border:1px solid var(--line);background:
    radial-gradient(circle at 50% 50%,rgba(102,245,173,.06),transparent 34%),
    repeating-radial-gradient(circle at 50% 50%,transparent 0 86px,rgba(102,245,173,.045) 86px 87px),
    linear-gradient(90deg,transparent calc(50% - .5px),rgba(102,245,173,.05) calc(50% - .5px),rgba(102,245,173,.05) calc(50% + .5px),transparent calc(50% + .5px)),
    linear-gradient(transparent calc(50% - .5px),rgba(102,245,173,.05) calc(50% - .5px),rgba(102,245,173,.05) calc(50% + .5px),transparent calc(50% + .5px)),
    rgba(5,7,7,.86);flex:1;min-height:320px;position:relative;overflow:hidden}.topo-legend{position:absolute;left:12px;top:10px;z-index:3;display:flex;gap:14px;flex-wrap:wrap;font:600 10px/1 "SF Mono",Consolas,monospace;color:#9aa39f;pointer-events:none}.topo-legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px;vertical-align:-1px}.topology-network{position:absolute;inset:0;outline:none;touch-action:none;cursor:grab}.topology-network:active{cursor:grabbing}.topo-empty{position:absolute;inset:0;display:grid;place-items:center;color:var(--muted);font:700 12px "SF Mono",Consolas,monospace;text-transform:uppercase;pointer-events:none}.topo-pop{position:absolute;right:14px;top:14px;width:min(310px,calc(100% - 24px));border:1px solid var(--line);background:rgba(2,3,3,.94);padding:12px 13px;display:none;z-index:4;box-shadow:0 18px 60px rgba(0,0,0,.42)}.topo-pop.open{display:block}.pop-title{font:900 16px/1 "Avenir Next Condensed","Arial Narrow",sans-serif;text-transform:uppercase;color:var(--paper)}.pop-sub{font:800 10px/1.35 "SF Mono",Consolas,monospace;color:var(--muted);text-transform:uppercase;margin-top:4px}.pop-grid{display:grid;grid-template-columns:auto 1fr;gap:6px 10px;margin-top:12px;font:800 10px/1.25 "SF Mono",Consolas,monospace;text-transform:uppercase}.pop-grid span:nth-child(odd){color:var(--muted)}.pop-agents{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}.pop-agent{border:1px solid #2d3935;color:var(--paper);padding:4px 7px;font:800 10px "SF Mono",Consolas,monospace;text-transform:uppercase}.pop-agent.on{border-color:rgba(102,245,173,.5);color:var(--green)}.pop-models{margin-top:7px;display:flex;flex-direction:column;gap:6px;max-height:190px;overflow:auto}.pop-ws{margin-top:7px;font:700 11px/1.5 "SF Mono",Consolas,monospace;color:var(--green);text-transform:none;word-break:break-all}.pop-model{display:flex;align-items:center;gap:8px;font:700 11px/1.25 "SF Mono",Consolas,monospace;color:var(--paper);word-break:break-all}.md-dot{width:7px;height:7px;border-radius:50%;flex:none}.md-dot.on{background:#4fc98a}.md-dot.off{background:#ff5161}
  .metrics{flex:none;display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--line);border:1px solid var(--line)}.metric{background:#050707;min-height:92px;padding:12px 14px;min-width:0}.metric-label{font:800 10px "SF Mono",Consolas,monospace;color:var(--muted);text-transform:uppercase}.metric-value{font:900 29px/1 "Avenir Next Condensed","Arial Narrow",sans-serif;margin-top:12px;color:var(--paper);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.metric-value small{font-size:11px;color:var(--muted);margin-left:4px}
  .panel{border:1px solid var(--line);background:rgba(5,7,7,.9)}.panel-head{height:42px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 13px;font:900 15px/1 "Avenir Next Condensed","Arial Narrow",sans-serif;text-transform:uppercase}.activity{display:flex;flex-direction:column}.activity-row{display:grid;grid-template-columns:72px 1fr 52px;gap:9px;padding:11px 13px;border-bottom:1px solid var(--line);font:700 11px/1.2 "SF Mono",Consolas,monospace;text-transform:uppercase}.activity-row:last-child{border-bottom:0}.activity-row.failed{color:#ffd3d7;background:rgba(255,81,97,.08)}.tag{color:var(--green)}.failed .tag{color:var(--red)}.watch .tag{color:var(--amber)}.progress{height:5px;background:#1a2220;margin-top:6px}.progress i{display:block;height:100%;background:var(--green)}
  .command{padding:13px}.cmd-input{width:100%;min-height:86px;resize:vertical;border:1px solid var(--line);background:#010202;color:var(--paper);padding:12px 13px;font:700 12px/1.45 "SF Mono",Consolas,monospace;outline:none}.cmd-input:focus{border-color:#55615d}.cmd-actions{display:flex;justify-content:space-between;align-items:center;margin-top:10px;gap:10px}.cmd-hint{font:700 10px "SF Mono",Consolas,monospace;color:var(--muted);text-transform:uppercase}.send{border:1px solid var(--paper);background:var(--paper);color:#020303;padding:8px 16px;font:900 11px "SF Mono",Consolas,monospace;text-transform:uppercase;cursor:pointer}.send:disabled{opacity:.45;cursor:default}.log{max-height:340px;overflow:auto;padding:4px 13px 13px;display:flex;flex-direction:column;gap:9px;border-bottom:1px solid var(--line)}.msg{padding:8px 0 0 10px;border-left:2px solid var(--line);font:700 11px/1.5 "SF Mono",Consolas,monospace;white-space:pre-wrap;color:#cbd4d1}.msg-meta{color:var(--muted);margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}.msg.user{border-left-color:var(--green);color:var(--green)}.msg.user .msg-meta{color:var(--green);opacity:.7}.msg.agent{border-left-color:#3c4440;color:var(--paper)}.msg.sys{border-left-color:transparent;color:var(--muted);padding-left:0}.msg.err{border-left-color:var(--red);color:#ffc8ce}.msg.pending{animation:pmsg 1.1s ease-in-out infinite}@keyframes pmsg{50%{opacity:.35}}
  /* ── 动效：纯 CSS/SVG 零依赖；底部 prefers-reduced-motion 一刀全关 ── */
  body{animation:gridmove 90s linear infinite}
  @keyframes gridmove{to{background-position:32px 32px,32px 32px}}
  .title{background:linear-gradient(105deg,var(--paper) 42%,#fff 50%,var(--paper) 58%);background-size:260% 100%;-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent;animation:shine 9s ease-in-out infinite}
  .title sup{-webkit-text-fill-color:var(--paper)}
  @keyframes shine{0%,55%{background-position:120% 0}90%,100%{background-position:-40% 0}}
  .status-dot:not(.off){animation:dotpulse 2.4s ease-in-out infinite}
  @keyframes dotpulse{50%{box-shadow:0 0 22px rgba(102,245,173,1)}}
  #last-refresh.blip{animation:blip .9s ease-out}
  @keyframes blip{0%{color:var(--green)}100%{color:inherit}}
  .topology::after{content:"";position:absolute;inset:-60%;pointer-events:none;z-index:1;background:conic-gradient(from 0deg at 50% 50%,rgba(102,245,173,.12) 0deg,rgba(102,245,173,.04) 26deg,transparent 58deg);animation:radar 16s linear infinite}
  @keyframes radar{to{transform:rotate(1turn)}}
  .tn>circle{transition:filter .18s}
  .tn:hover>circle{filter:brightness(1.3) drop-shadow(0 0 6px rgba(220,230,227,.35))}
  .ping{fill:none;stroke-width:1.6;pointer-events:none;transform-box:fill-box;transform-origin:center;animation:ping 3.4s ease-out infinite}
  .ping-hub{animation-duration:4.4s;stroke-width:2}
  @keyframes ping{0%{transform:scale(1);opacity:.55}75%,100%{transform:scale(2.5);opacity:0}}
  .eflow{stroke-dasharray:5 11;opacity:.32;animation:eflow 5.5s linear infinite}
  @keyframes eflow{to{stroke-dashoffset:-176}}
  .satg>*{transform-box:fill-box;transform-origin:center;animation:satin .28s cubic-bezier(.2,.9,.3,1.4) backwards}
  @keyframes satin{from{opacity:0;transform:scale(.2)}to{opacity:1;transform:scale(1)}}
  .seln{animation:selglow 2.2s ease-in-out infinite}
  @keyframes selglow{0%,100%{filter:drop-shadow(0 0 4px rgba(255,255,255,.3))}50%{filter:drop-shadow(0 0 11px rgba(255,255,255,.65))}}
  .topo-pop.open{animation:popin .2s ease-out}
  @keyframes popin{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:translateY(0)}}
  .activity-row.run .tag{animation:tagpulse 1.5s ease-in-out infinite}
  @keyframes tagpulse{50%{opacity:.45}}
  .progress i{background:repeating-linear-gradient(45deg,var(--green) 0 6px,rgba(102,245,173,.55) 6px 12px);background-size:17px 17px;animation:stripes 1.1s linear infinite;transition:width .6s ease}
  .failed .progress i{background:var(--red);animation:none}
  @keyframes stripes{to{background-position:17px 0}}
  .metric-value.mflash{animation:mflash .8s ease-out}
  @keyframes mflash{0%{color:var(--green)}}
  /* 开机入场编排：区块错峰升入（一次性） */
  .hero{animation:risein .55s ease-out backwards}
  .metrics{animation:risein .55s ease-out .1s backwards}
  .stage{animation:risein .55s ease-out .18s backwards}
  .right>.panel:nth-of-type(1){animation:risein .55s ease-out .26s backwards}
  .right>.panel:nth-of-type(2){animation:risein .55s ease-out .34s backwards}
  @keyframes risein{from{opacity:0;transform:translateY(16px)}}
  .ekg{vertical-align:-3px;margin-left:7px}
  .ekg path{stroke-dasharray:90 60;animation:ekg 2.6s linear infinite}
  @keyframes ekg{to{stroke-dashoffset:-150}}
  .activity-row{animation:rowin .35s ease-out backwards}
  @keyframes rowin{from{opacity:0;transform:translateX(-9px)}}
  .cmd-input.busy{border-color:#3aa869;animation:busyb 1.6s ease-in-out infinite}
  @keyframes busyb{50%{border-color:#66f5ad}}
  .panel,.metrics{transition:border-color .3s}
  .panel:hover,.metrics:hover{border-color:#34413c}
  /* ── 质感打磨：HUD 角标/比例条/纹理/反馈（拓扑图形本体不动，只动页面 chrome）── */
  ::selection{background:rgba(102,245,173,.25);color:#fff}
  ::-webkit-scrollbar{width:8px;height:8px}
  ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:#222b28}
  ::-webkit-scrollbar-thumb:hover{background:#33403b}
  body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:9;background:radial-gradient(ellipse 120% 90% at 50% 0%,transparent 62%,rgba(0,0,0,.4) 100%)}
  #clock{font-size:17px;letter-spacing:.04em}
  #clock,#datestamp,.metric-value,.activity-row>div:last-child{font-variant-numeric:tabular-nums}
  .hero{position:relative}
  .hero::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;background:linear-gradient(90deg,var(--red),transparent 32%)}
  .section-title{display:flex;align-items:center;gap:8px}
  .section-title::before{content:"";width:8px;height:8px;background:var(--red)}
  .panel-head>span:first-child{display:inline-flex;align-items:center;gap:8px}
  .panel-head>span:first-child::before{content:"";width:8px;height:8px;background:var(--red)}
  .metric{position:relative;transition:background .25s}
  .metric:hover{background:#08100c}
  .metric::before{content:"";position:absolute;top:6px;left:6px;width:9px;height:9px;border-top:1px solid #2c3733;border-left:1px solid #2c3733}
  .metric::after{content:"";position:absolute;bottom:6px;right:6px;width:9px;height:9px;border-bottom:1px solid #2c3733;border-right:1px solid #2c3733}
  .metric-label{letter-spacing:.1em}
  .metric-value{margin-top:14px}
  .mbar{height:3px;background:#16201c;margin-top:10px;overflow:hidden}
  .mbar i{display:block;height:100%;background:var(--green);box-shadow:0 0 8px rgba(102,245,173,.5)}
  .mbar.warn i{background:var(--amber);box-shadow:0 0 8px rgba(230,196,109,.45)}
  .topology{box-shadow:inset 0 0 0 1px rgba(255,255,255,.02),inset 0 0 60px rgba(0,0,0,.5)}
  .panel,.metrics{box-shadow:inset 0 0 0 1px rgba(255,255,255,.02)}
  .activity-row{transition:background .2s}
  .activity-row:hover{background:rgba(255,255,255,.025)}
  .activity-row .tag{letter-spacing:.06em}
  .cmd-input{transition:border-color .2s,box-shadow .2s}
  .cmd-input:focus{border-color:#3aa869;box-shadow:0 0 0 1px rgba(58,168,105,.35),0 0 18px rgba(102,245,173,.08)}
  .send{transition:box-shadow .15s}
  .send:hover:not(:disabled){box-shadow:0 0 14px rgba(220,230,227,.35)}
  .meta-grid .muted,.metric-label{letter-spacing:.09em}
  /* ── 氛围光：环境光晕/毛玻璃/霓虹微光 ── */
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;background:radial-gradient(640px 420px at 10% -6%,rgba(255,81,97,.06),transparent 60%),radial-gradient(960px 640px at 104% 42%,rgba(102,245,173,.05),transparent 62%)}
  .page{position:relative;z-index:1}
  .panel{background:rgba(7,12,10,.55);backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px)}
  .panel-head{position:relative}
  .panel-head::after{content:"";position:absolute;left:0;bottom:-1px;width:42%;height:1px;background:linear-gradient(90deg,rgba(102,245,173,.7),transparent)}
  .metric-value{text-shadow:0 0 16px rgba(233,240,239,.2)}
  .tag{text-shadow:0 0 9px currentColor}
  .topo-legend span{background:rgba(2,3,3,.55);border:1px solid #1f2926;padding:5px 8px;backdrop-filter:blur(4px)}
  #lang-btn{background:transparent;border:1px solid var(--line);color:var(--muted);font:800 9px "SF Mono",Consolas,monospace;padding:2px 6px;cursor:pointer;letter-spacing:.05em;transition:color .15s,border-color .15s}
  #lang-btn:hover{color:var(--green);border-color:#34413c}
  @media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
  @media(max-width:1180px){.page{height:auto;overflow:visible}.layout{grid-template-columns:1fr}.hero{flex-wrap:wrap;gap:12px 24px}.metrics{grid-template-columns:repeat(3,1fr)}.topology{min-height:420px}.right .panel:first-of-type .activity{max-height:300px}}
  @media(max-width:720px){.page{padding:12px}.hero{flex-direction:column;align-items:flex-start;gap:10px}.stamp{text-align:left}.status-line{justify-content:flex-start}.meta p{display:none}.metrics{grid-template-columns:repeat(2,1fr)}.activity-row{grid-template-columns:60px 1fr}.activity-row>div:last-child{grid-column:2}.title{font-size:42px}.topology{min-height:340px}.cmd-actions{align-items:stretch;flex-direction:column}.send{width:100%}}
</style>
</head>
<body>
<div class="page">
  <header class="hero">
    <div class="title">MYAIWEB<sup>®</sup></div>
    <div class="meta">
      <p>PERSONAL AI NETWORK CONTROL SURFACE</p>
      <div class="meta-grid">
        <span><span class="muted">HUB:</span> <span id="location-name">--</span></span>
        <span><span class="muted">SCOPE:</span> <span id="mode-id">--</span></span>
        <span><span class="muted">ENGINE:</span> myaiweb 2.0</span>
        <span><span class="muted">STATUS:</span> <span id="vk-status"><span class="status-dot off"></span> WAITING</span></span>
      </div>
    </div>
    <div class="stamp">
      <div id="datestamp">M--.D--.Y--</div>
      <div id="clock">--:--:--</div>
      <div class="status-line"><button id="lang-btn" onclick="setLang(LANG==='zh'?'en':'zh')" title="中 / EN">EN</button><span id="hub-dot" class="status-dot off"></span><span id="last-refresh">NO SIGNAL</span></div>
    </div>
  </header>

  <section class="metrics" aria-label="network aggregate metrics">
    <div class="metric"><div class="metric-label">Machines</div><div id="m-machines" class="metric-value">--</div></div>
    <div class="metric"><div class="metric-label">GPU / VRAM</div><div id="m-gpu" class="metric-value">--</div></div>
    <div class="metric"><div class="metric-label">Agents</div><div id="m-agents" class="metric-value">--</div></div>
    <div class="metric"><div class="metric-label" id="m-models-l">本地大模型</div><div id="m-models" class="metric-value">--</div></div>
    <div class="metric"><div class="metric-label" id="m-tasks-l">Tasks 活跃</div><div id="m-tasks" class="metric-value">--</div></div>
    <div class="metric"><div class="metric-label">Storage</div><div id="m-storage" class="metric-value">--</div></div>
  </section>

  <main class="layout">
    <section class="stage">
      <div class="section-head"><div class="section-title">Network Topology</div><div id="topology-note" class="section-note">waiting for registry</div></div>
      <div class="topology">
        <div class="topo-legend"><span><i style="background:#ff5161"></i><b id="leg-hub">建网机</b></span><span><i style="background:#edf1ee"></i><b id="leg-control">主控</b></span><span><i style="background:#8f9996"></i><b id="leg-node">节点</b></span><span><i style="background:#4fc98a"></i><b id="leg-agent">agent</b></span></div>
        <div id="topology-network" class="topology-network"></div>
        <div id="topology-empty" class="topo-empty">No machines registered yet</div>
        <div id="topology-pop" class="topo-pop"></div>
      </div>
    </section>

    <aside class="right">
      <section class="panel">
        <div class="panel-head"><span>Activity</span><span id="done-note" class="section-note"></span></div>
        <div id="activity" class="activity"><div class="activity-row"><span class="tag">IDLE</span><div>No active tasks</div><div>--</div></div></div>
      </section>

      <section class="panel">
        <div class="panel-head"><span>Command</span><span id="agent-name" class="section-note">adaptive agent</span></div>
        <div id="agent-log" class="log"><div class="msg sys"><div class="msg-meta">myaiweb</div><span id="cmd-ready">Command surface ready. 直接提问，或下任务给建网机 agent。</span></div></div>
        <div class="command">
          <textarea id="agent-message" class="cmd-input" placeholder="> 问网络状态 / 派任务 / 自然语言都行"></textarea>
          <div class="cmd-actions"><div id="cmd-hint" class="cmd-hint">Enter 发送 · Shift+Enter 换行 · hub agent: codex / claude / opencode</div><button id="agent-send" class="send" onclick="sendAgentMessage()">Send</button></div>
        </div>
      </section>
    </aside>
  </main>
</div>

<script>
// ── i18n：大屏中英切换（零依赖；默认跟浏览器语言，可手动切、记 localStorage）──
const I18N={
 zh:{m_models:'本地大模型',m_tasks:'Tasks 活跃',leg_hub:'建网机',leg_control:'主控',leg_node:'节点',leg_agent:'agent',
  cmd_ready:'Command surface ready. 直接提问，或下任务给建网机 agent。',cmd_ph:'> 问网络状态 / 派任务 / 自然语言都行',
  cmd_hint:'Enter 发送 · Shift+Enter 换行 · hub agent: codex / claude / opencode',cmd_running:'Running',cmd_send:'Send',
  online:'在线',offline:'离线',reg_remote:'已注册（远程·未探活）',via:'经',local_llm:'本地大模型',unavailable:'不可用',
  no_llm:'无本地大模型',workspace:'工作区',no_active:'No active tasks',done_hidden:n=>`${n} 已完成/过期(已隐藏)`,
  loc:'本机',across:n=>`跨 ${n} 台`,m_total:'总',bad:n=>`${n} 不可用`,topo_hint:'拖动排布 · 点节点看详情',topo_wait:'等待注册中心',
  hub_signal:'HUB SIGNAL',waiting:'WAITING',no_signal:'NO SIGNAL',proc:'正在建网机上跑…（agent 冷启动通常 10–60s）',
  pending:'processing on hub...'},
 en:{m_models:'Local LLMs',m_tasks:'Tasks',leg_hub:'Hub',leg_control:'Control',leg_node:'Node',leg_agent:'agent',
  cmd_ready:'Command surface ready. Ask anything, or dispatch a task to the hub agent.',cmd_ph:'> ask status / dispatch a task / natural language',
  cmd_hint:'Enter to send · Shift+Enter newline · hub agent: codex / claude / opencode',cmd_running:'Running',cmd_send:'Send',
  online:'online',offline:'offline',reg_remote:'registered (remote·unprobed)',via:'via',local_llm:'Local LLMs',unavailable:'unavailable',
  no_llm:'no local LLM',workspace:'workspace',no_active:'No active tasks',done_hidden:n=>`${n} done/stale (hidden)`,
  loc:'local',across:n=>`across ${n}`,m_total:'total',bad:n=>`${n} unavailable`,topo_hint:'drag to arrange · click a node → details',topo_wait:'waiting for registry',
  hub_signal:'HUB SIGNAL',waiting:'WAITING',no_signal:'NO SIGNAL',proc:'running on hub… (agent cold start ~10–60s)',
  pending:'processing on hub...'}};
let LANG=localStorage.getItem('myaiweb-lang')||((navigator.language||'en').toLowerCase().startsWith('zh')?'zh':'en');
function t(k,a){const v=(I18N[LANG]||I18N.en)[k];return typeof v==='function'?v(a):(v??k);}
function applyStaticI18n(){document.documentElement.lang=LANG;
 const S=(id,txt)=>{const e=document.getElementById(id);if(e)e.textContent=txt;};
 S('m-models-l',t('m_models'));S('m-tasks-l',t('m_tasks'));
 S('leg-hub',t('leg_hub'));S('leg-control',t('leg_control'));S('leg-node',t('leg_node'));S('leg-agent',t('leg_agent'));
 const ph=document.getElementById('agent-message');if(ph)ph.placeholder=t('cmd_ph');
 S('cmd-hint',t('cmd_hint'));S('lang-btn',LANG==='zh'?'EN':'中');S('cmd-ready',t('cmd_ready'));
 const rl=document.getElementById('last-refresh');if(rl&&/NO SIGNAL|无信号/.test(rl.textContent))rl.textContent=t('no_signal');}
function setLang(l){LANG=l;localStorage.setItem('myaiweb-lang',l);window._mSig=window._actSig='';lastTopoSig='';applyStaticI18n();if(window._lastData)render(window._lastData);}
function escapeHTML(s){return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtTime(ts){if(!ts)return'--:--:--';return new Date(ts*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});}
function tick(){var d=new Date();document.getElementById('clock').textContent=d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});var ds=document.getElementById('datestamp');if(ds)ds.textContent='M'+String(d.getMonth()+1).padStart(2,'0')+'.D'+String(d.getDate()).padStart(2,'0')+'.Y'+String(d.getFullYear()).slice(2);}setInterval(tick,1000);tick();
function num(v){const m=String(v||'').match(/[\d.]+/);return m?parseFloat(m[0]):0;}
function unit(v,u){return v?`${Math.round(v*10)/10}${u}`:'--';}
function gb(v){if(!v)return'--';return v>=1024?`${Math.round(v/102.4)/10}T`:`${Math.round(v)}G`;}
function asList(v){if(Array.isArray(v))return v.map(String);if(v&&typeof v==='object')return Object.keys(v).filter(k=>v[k]);if(typeof v==='string')return v.split(/[,\s]+/).filter(Boolean);return[];}
function hw(n,k){return (n.hardware&&n.hardware[k])||'';}
function mdl(n){var a=Array.isArray(n.models)?n.models:[];return a.map(function(m){return (m&&typeof m==='object')?{name:m.name||'',ok:m.ok!==false}:{name:String(m),ok:true};}).filter(function(m){return m.name;});}
function storageGB(n){var ds=hw(n,'disks');if(Array.isArray(ds)&&ds.length){var to=0,av=0;ds.forEach(function(d){to+=parseFloat(d.total_gb)||0;av+=parseFloat(d.avail_gb)||0;});if(to)return {total:to,used:Math.max(0,to-av)};}var s=hw(n,'storage')||'';var m=s.match(/([\d.]+)\s*\/\s*([\d.]+)/);if(m){var av2=parseFloat(m[1])||0,to2=parseFloat(m[2])||0;return {total:to2,used:Math.max(0,to2-av2)};}return {total:toGB(hw(n,'disk_total')||hw(n,'disk')),used:toGB(hw(n,'disk_used'))};}
function isControl(n){return /主控|control/i.test((n.primary_role||'')+' '+(n.hostname||''));}
function isHub(n){return /建网|hub|engine/i.test(n.primary_role||'');}   // 只认角色：候选(is_infra_candidate)≠现任，曾让新节点抢走 C 位
function roleName(s){return String(s||'').replace(/^[^一-龥A-Za-z0-9]+/,'').replace(' 节点','').trim()||'NODE';}
// EN 模式把角色数据词译成英文（图例已是 Hub/Control/Node，弹窗角色徽章也跟上，保持一致）
const ROLE_EN=[['次建网机','Secondary Hub'],['建网机','Hub'],['主控','Control'],['未分配','Unassigned'],['GPU 节点','GPU Node'],['GPU','GPU'],['服务节点','Service Node'],['服务','Service'],['存储节点','Storage Node'],['存储','Storage'],['推理','Inference'],['训练','Training'],['节点','Node']];
function roleDisp(s){let r=roleName(s);if(LANG==='en'){for(const[zh,en]of ROLE_EN)r=r.split(zh).join(en);}return r;}
function specLine(n){const cpu=hw(n,'cpu')||'CPU';const ram=hw(n,'ram')||hw(n,'memory')||(hw(n,'ram_gb')?hw(n,'ram_gb')+'GB':'')||'RAM';const gpu=hw(n,'gpu')||'NO GPU';const vram=hw(n,'vram')||'';
// 硬盘走 disks 求和（和 Storage 指标一致）——别退到 storage 摘要串，那串只含 C: 盘，多盘机会漏掉 D:/E:
const ds=hw(n,'disks');let disk;if(Array.isArray(ds)&&ds.length){const sg=storageGB(n);disk=`${gb(sg.used)}/${gb(sg.total)}${ds.length>1?` (${ds.length}盘)`:''}`;}else{disk=hw(n,'disk')||hw(n,'disk_total')||hw(n,'storage')||'DISK';}
return `${cpu} · ${ram} · ${gpu}${vram?'/'+vram:''} · ${disk}`;}
function wsLine(n){const w=n.workspace;if(!w)return'';const st=w.state||{};const bits=['✓ '+(w.work_dir||'?')];if(w.shell)bits.push(w.shell);if(st.disk)bits.push('盘 '+st.disk);if(st.gpu)bits.push('GPU '+st.gpu);return bits.join(' · ');}
function shortName(s){return String(s||'').replace(/\.local$/i,'');}
// ── 拓扑：手写 SVG，零依赖（曾用 unpkg 的 cytoscape——CDN 够不到时整块卡 LOADING，违背零依赖铁律）──
let topoSel=null;let lastTopologyNodes=[];let lastTopoSig='';
let topoPos=new Map();          // hostname → 拖过的坐标（30s 刷新不回弹）
let topoPan={x:0,y:0};          // 画布平移量（viewBox 单位）
let topoDrag=null;
window.addEventListener('resize',()=>{lastTopoSig='';if(lastTopologyNodes.length)renderTopology(lastTopologyNodes);});
function topoAgents(n){return [...asList(n.agents).filter(x=>!/:no$/i.test(x)),...asList(n.cli_tools).filter(x=>/claude|codex|opencode/i.test(x)),...asList(n.services).filter(x=>/ollama|ssh|minio|n8n/i.test(x))].map(x=>String(x).replace(/:yes$/i,'')).filter((x,i,a)=>a.indexOf(x)===i).slice(0,6);}
function topoColor(n){return isHub(n)?'#ff5161':isControl(n)?'#edf1ee':'#8f9996';}
function showModelPop(n){const pop=document.getElementById('topology-pop');if(!pop)return;const ms=mdl(n);const ok=ms.filter(m=>m.ok).length;pop.innerHTML=`<div class="pop-title">${escapeHTML(n.hostname||'node')}</div><div class="pop-sub">${escapeHTML(roleDisp(n.primary_role))} · ${escapeHTML(specLine(n))}</div><div class="pop-sub" style="margin-top:7px">${n.online?`<span style="color:#66f5ad">● ${t('online')}</span>`:(n.registered_remote?`<span style="color:#e0a44d">● ${t('reg_remote')}${n.synced_from?'· '+t('via')+' '+escapeHTML(n.synced_from):''}</span>`:`<span style="color:#ff5161">● ${t('offline')}</span>`)}</div><div class="pop-sub" style="margin-top:11px;color:#cfd8d4">${t('local_llm')}${ms.length?` (${ok}/${ms.length})`:''}</div><div class="pop-models">${ms.length?ms.map(m=>`<div class="pop-model"><span class="md-dot ${m.ok?'on':'off'}"></span><span>${escapeHTML(m.name)}</span>${m.ok?'':`<span style="color:#ff5161;margin-left:auto">${t('unavailable')}</span>`}</div>`).join(''):`<div class="pop-model" style="color:#6b7470">${t('no_llm')}</div>`}</div>${n.workspace?`<div class="pop-sub" style="margin-top:11px;color:#cfd8d4">${t('workspace')}</div><div class="pop-ws">${escapeHTML(wsLine(n))}</div>`:''}`;pop.classList.add('open');}
function edgeD(ax,ay,bx,by){return `M ${ax} ${ay} L ${bx} ${by}`;}   // 直线（试过贝塞尔弧，拐弯不好看，撤）
let topoSatOut=null;   // 选中节点的卫星扇出方向：点开时按「连线最大空当」定一次，拖动期间锁定不变
function _satOut(i,nodes,parent,pos){const a=[];if(parent[i]>=0&&pos[parent[i]])a.push(Math.atan2(pos[parent[i]].y-pos[i].y,pos[parent[i]].x-pos[i].x));nodes.forEach((m,j)=>{if(parent[j]===i&&pos[j])a.push(Math.atan2(pos[j].y-pos[i].y,pos[j].x-pos[i].x));});if(!a.length)return -Math.PI/2;if(a.length===1)return a[0]+Math.PI;a.sort((x,y)=>x-y);let best=-Math.PI/2,bg=-1;for(let k=0;k<a.length;k++){const nx=k+1<a.length?a[k+1]:a[0]+2*Math.PI;const g=nx-a[k];if(g>bg){bg=g;best=a[k]+g/2;}}return best;}
function renderTopology(nodes){lastTopologyNodes=nodes;const box=document.getElementById('topology-network');const empty=document.getElementById('topology-empty');const note=document.getElementById('topology-note');const pop=document.getElementById('topology-pop');empty.style.display=nodes.length?'none':'grid';note.textContent=nodes.length?t('topo_hint'):t('topo_wait');if(!nodes.length){box.innerHTML='';pop.classList.remove('open');lastTopoSig='';return;}
if(topoSel===null)topoSatOut=null;   // 收起详情即清方向缓存，下次点开按彼时位置重新找空当
const sig=JSON.stringify([nodes.map(n=>[n.hostname,n.online,n.registered_remote]),topoSel]);if(sig===lastTopoSig)return;lastTopoSig=sig;
const W=box.clientWidth||800,H=box.clientHeight||400,cx=W/2,cy=H/2;
// 布局：hub 居中、其余放射状（起角 30°，避免两台机时直上直下）；挂在别的节点下的沿父节点外向扇出
const hubIdx=Math.max(0,nodes.findIndex(isHub));
const parent=nodes.map((n,i)=>{if(i===hubIdx)return -1;const bt=String(n.belongs_to||'').toLowerCase();if(bt){const j=nodes.findIndex(m=>String(m.hostname||'').toLowerCase()===bt);if(j>=0&&j!==i)return j;}return hubIdx;});
const pos=new Array(nodes.length);pos[hubIdx]={x:cx,y:cy};
const ring=nodes.map((n,i)=>i).filter(i=>parent[i]===hubIdx);
const R=Math.max(130,Math.min(W,H)/2-95);
ring.forEach((i,k)=>{const ang=Math.PI/6+2*Math.PI*k/ring.length;pos[i]={x:cx+Math.cos(ang)*R,y:cy+Math.sin(ang)*R};});
let guard=0;while(pos.some(p=>!p)&&guard++<5){nodes.forEach((n,i)=>{if(pos[i]||!pos[parent[i]])return;const pi=parent[i];const sibs=nodes.map((m,j)=>j).filter(j=>parent[j]===pi);const k=sibs.indexOf(i);const out=Math.atan2(pos[pi].y-cy,pos[pi].x-cx)||Math.PI/6;const ang=out+(sibs.length>1?(k/(sibs.length-1)-.5)*Math.PI/2.6:0);pos[i]={x:pos[pi].x+Math.cos(ang)*115,y:pos[pi].y+Math.sin(ang)*115};});}
nodes.forEach((n,i)=>{if(!pos[i])pos[i]={x:cx+R,y:cy};const ov=topoPos.get(n.hostname);if(ov)pos[i]=ov;});
let edges='';nodes.forEach((n,i)=>{if(i===hubIdx)return;const s=parent[i];const d=edgeD(pos[s].x,pos[s].y,pos[i].x,pos[i].y);edges+=`<path class="edge" data-a="${s}" data-b="${i}" d="${d}" fill="none" stroke="#3c4440" stroke-width="1.1" opacity=".8"/>`;if(n.online&&nodes[s].online)edges+=`<path class="edge eflow" data-a="${s}" data-b="${i}" d="${d}" fill="none" stroke="#66f5ad" stroke-width="1.1"/>`;});
let sat='',nd='';
nodes.forEach((n,i)=>{const r=isHub(n)?28:isControl(n)?22:17;const c=topoColor(n);const on=n.online;const rr=!on&&n.registered_remote;const sel=topoSel===i;
if(sel){const ags=topoAgents(n);const cnt=ags.length;
if(!topoSatOut||topoSatOut.host!==n.hostname)topoSatOut={host:n.hostname,ang:_satOut(i,nodes,parent,pos)};
const out=topoSatOut.ang;
const spread=cnt>1?Math.min(Math.PI*.75,Math.PI*.22*(cnt-1)):0;let s='';
ags.forEach((a,k)=>{const ang=out+(cnt>1?(k/(cnt-1)-.5)*spread:0);const ax=pos[i].x+Math.cos(ang)*(r+56),ay=pos[i].y+Math.sin(ang)*(r+56);const lx=ax+Math.cos(ang)*17,ly=ay+Math.sin(ang)*17+3;const anchor=Math.cos(ang)>.35?'start':(Math.cos(ang)<-.35?'end':'middle');
const dl=` style="animation-delay:${k*55}ms"`;s+=`<path${dl} d="${edgeD(pos[i].x,pos[i].y,ax,ay)}" fill="none" stroke="#3aa869" stroke-width="1" stroke-dasharray="3 3" opacity=".85"/><circle${dl} cx="${ax}" cy="${ay}" r="10" fill="#3aa869" stroke="#7fe6a9" stroke-width="1.2"/><text${dl} x="${lx}" y="${ly}" text-anchor="${anchor}" fill="#9fe8c3" font-size="9" font-weight="700" font-family="SF Mono,Consolas,monospace">${escapeHTML(String(a).slice(0,12))}</text>`;});
sat+=`<g class="satg" data-i="${i}">${s}</g>`;}
nd+=`<g class="tn" data-i="${i}" data-x="${pos[i].x}" data-y="${pos[i].y}" style="cursor:pointer">${isHub(n)?`<circle cx="${pos[i].x}" cy="${pos[i].y}" r="${r+7}" fill="none" stroke="rgba(255,81,97,.35)" stroke-width="1.2"/>`:''}${on?`<circle class="ping${isHub(n)?' ping-hub':''}" cx="${pos[i].x}" cy="${pos[i].y}" r="${r}" stroke="${c}" style="animation-delay:${((i*.7)%2.8).toFixed(1)}s"/>`:''}<circle${sel?' class="seln"':''} cx="${pos[i].x}" cy="${pos[i].y}" r="${r}" fill="${on?c:'#0a0c0b'}"${on?'':` stroke="${rr?'#e0a44d':c}" stroke-width="2.6"${rr?' stroke-dasharray="5 4"':''}`}/><text x="${pos[i].x}" y="${pos[i].y+r+16}" text-anchor="middle" font-size="10" font-weight="700" font-family="SF Mono,Consolas,monospace"><tspan fill="#7c8582">${String(i+1).padStart(3,'0')}</tspan><tspan fill="${on?'#dce6e3':'#9aa39f'}"> ${escapeHTML((shortName(n.hostname)||'node').slice(0,18))}</tspan></text></g>`;});
box.innerHTML=`<svg width="100%" height="100%" viewBox="${-topoPan.x} ${-topoPan.y} ${W} ${H}" xmlns="http://www.w3.org/2000/svg">${edges}${sat}${nd}</svg>`;
// ── 拖拽：节点=改坐标（记进 topoPos），空白=平移画布；位移 <4px 当点击（弹/收详情）──
const svg=box.querySelector('svg');const k=()=>W/(box.clientWidth||W);
box.onpointerdown=e=>{const g=e.target.closest('.tn');topoDrag={i:g?+g.dataset.i:null,x0:e.clientX,y0:e.clientY,px:topoPan.x,py:topoPan.y,nx:g?+g.dataset.x:0,ny:g?+g.dataset.y:0,moved:false};try{box.setPointerCapture(e.pointerId);}catch(_){}e.preventDefault();};
box.onpointermove=e=>{const d=topoDrag;if(!d)return;const s=k();const dx=(e.clientX-d.x0)*s,dy=(e.clientY-d.y0)*s;if(Math.abs(dx)+Math.abs(dy)>4)d.moved=true;if(!d.moved)return;
if(d.i!=null){const i=d.i,nx=d.nx+dx,ny=d.ny+dy;topoPos.set(nodes[i].hostname,{x:nx,y:ny});const tr=`translate(${nx-d.nx},${ny-d.ny})`;const g=box.querySelector(`.tn[data-i="${i}"]`);if(g)g.setAttribute('transform',tr);const sg=box.querySelector(`.satg[data-i="${i}"]`);if(sg)sg.setAttribute('transform',tr);box.querySelectorAll(`path.edge[data-a="${i}"],path.edge[data-b="${i}"]`).forEach(p=>{const a=+p.dataset.a,b=+p.dataset.b;const ga=box.querySelector(`.tn[data-i="${a}"]`),gb=box.querySelector(`.tn[data-i="${b}"]`);if(!ga||!gb)return;const ax=a===i?nx:+ga.dataset.x,ay=a===i?ny:+ga.dataset.y,bx=b===i?nx:+gb.dataset.x,by=b===i?ny:+gb.dataset.y;p.setAttribute('d',edgeD(ax,ay,bx,by));});}
else{topoPan={x:d.px+dx,y:d.py+dy};if(svg)svg.setAttribute('viewBox',`${-topoPan.x} ${-topoPan.y} ${W} ${H}`);}};
box.onpointerup=e=>{const d=topoDrag;topoDrag=null;if(!d)return;
if(!d.moved){if(d.i!=null){if(topoSel===d.i){topoSel=null;pop.classList.remove('open');}else{topoSel=d.i;showModelPop(nodes[d.i]);}}else if(topoSel!==null){topoSel=null;pop.classList.remove('open');}lastTopoSig='';renderTopology(lastTopologyNodes);}
else if(d.i!=null){lastTopoSig='';renderTopology(lastTopologyNodes);}};   // 拖完重排：transform 烘进坐标、连线/卫星归位
box.onpointercancel=()=>{topoDrag=null;};}
function toGB(v){v=String(v||'');var m=v.match(/([\d.]+)\s*(tb|gb|mb|t|g|m)?/i);if(!m)return 0;var x=parseFloat(m[1])||0;var u=(m[2]||'g').toLowerCase();if(u.charAt(0)==='t')x*=1024;else if(u.charAt(0)==='m')x/=1024;return x;}
function renderMetrics(nodes,tasks){const _s=JSON.stringify([nodes.map(n=>[n.hostname,n.online,n.is_local,n.models,asList(n.agents).length,hw(n,'storage'),hw(n,'disk_total'),hw(n,'disk_used'),hw(n,'gpu')]),(tasks||[]).map(t=>t.status)]);if(_s===window._mSig)return;window._mSig=_s;const online=nodes.filter(n=>n.online).length;const modelsAll=nodes.reduce((a,n)=>a.concat(mdl(n)),[]);const modelTotal=modelsAll.length;const modelBad=modelsAll.filter(m=>!m.ok).length;const agentsOf=n=>asList(n.agents).length+asList(n.cli_tools).filter(x=>/claude|codex|opencode/i.test(x)).length;const agentTotal=nodes.reduce((a,n)=>a+agentsOf(n),0);const agentMachines=nodes.filter(n=>agentsOf(n)>0).length;const st=nodes.reduce((a,n)=>{var s=storageGB(n);return {total:a.total+s.total,used:a.used+s.used};},{total:0,used:0});const gpus=nodes.map(n=>String(hw(n,'gpu')||'')).filter(g=>g&&!/^(none|no\s|无|-)/i.test(g));const vram=gpus.reduce((a,g)=>{const m=g.match(/([\d.]+)\s*GB?/i);return a+(m?parseFloat(m[1]):0);},0)+nodes.reduce((a,n)=>a+(hw(n,'gpu')?0:num(hw(n,'vram'))),0);const tAct=(tasks||[]).filter(t=>['running','pending'].includes(String(t.status||'').toLowerCase())).length;const _rm=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;const setM=(id,h)=>{const el=document.getElementById(id);if(el.innerHTML===h)return;el.classList.remove('mflash');void el.offsetWidth;el.classList.add('mflash');const m=h.match(/^([\d.]+)/);if(!m||_rm){el.innerHTML=h;return;}const target=parseFloat(m[1]),rest=h.slice(m[1].length),prev=parseFloat((el.textContent||'').trim())||0,dec=(m[1].split('.')[1]||'').length,t0=performance.now(),D=650;const step=t=>{const p=Math.min(1,(t-t0)/D),e=1-Math.pow(1-p,3);el.innerHTML=(prev+(target-prev)*e).toFixed(dec)+rest;if(p<1)requestAnimationFrame(step);};requestAnimationFrame(step);};const bar=(num,den,warn)=>`<div class="mbar${warn?' warn':''}"><i style="width:${den?Math.max(2,Math.round(num/den*100)):0}%"></i></div>`;setM('m-machines',`${online}<small>/${nodes.length}</small>${bar(online,nodes.length)}`);setM('m-gpu',gpus.length?`${gpus.length}<small>${vram?Math.round(vram)+'G VRAM':''}</small>`:'--');setM('m-models',modelTotal?`${modelTotal}${modelBad?`<small><span style="color:#ff5161">${t('bad',modelBad)}</span></small>`:''}${bar(modelTotal-modelBad,modelTotal,modelBad>0)}`:'--');setM('m-agents',agentTotal?`${agentTotal}<small>${t('across',agentMachines)}</small>`:'--');setM('m-tasks',`${tAct}<small>/ ${(tasks||[]).length} ${t('m_total')}</small>${bar(tAct,(tasks||[]).length)}`);setM('m-storage',st.total?`${gb(st.used)}<small>/ ${gb(st.total)}</small>${bar(st.used,st.total,st.used/st.total>.85)}`:'--');}
function renderActivity(tasks){const _s=JSON.stringify((tasks||[]).map(t=>[t.node||t.target,t.description||t.desc,t.status,t.progress]));if(_s===window._actSig)return;window._actSig=_s;const box=document.getElementById('activity');const _now=Date.now()/1000;const _stale=t=>{const st=(t.status||'').toLowerCase();return (st==='failed'||st==='note')&&t.started_at&&(_now-t.started_at>86400);};const visible=(tasks||[]).filter(t=>(t.status||'pending')!=='done'&&!_stale(t));const hidden=(tasks||[]).length-visible.length;document.getElementById('done-note').textContent=hidden?t('done_hidden',hidden):'';if(!visible.length){box.innerHTML=`<div class="activity-row"><span class="tag">IDLE</span><div>${t('no_active')}</div><div>--</div></div>`;return;}box.innerHTML=visible.map((t,ix)=>{const st=(t.status||'pending').toLowerCase();const watch=t.watch===true||t.watch==='true';const cls=st==='failed'?'failed':watch?'watch':st==='running'?'run':'';const tag=watch?'WATCH':st==='running'?'RUNNING':st==='failed'?'FAILED':st==='note'?'NOTE':'QUEUED';const prog=Math.max(0,Math.min(100,parseInt(t.progress)||0));const right=watch&&t.last_seen?fmtTime(t.last_seen):st==='note'?'--':`${prog}%`;const bar=st==='note'?'':`<div class="progress"><i style="width:${prog}%"></i></div>`;return `<div class="activity-row ${cls}" style="animation-delay:${ix*60}ms"><span class="tag">${tag}</span><div>${escapeHTML(t.node||t.target||'node')} · ${escapeHTML(t.description||t.desc||'task')}${bar}</div><div>${right}</div></div>`;}).join('');}
function render(data){window._lastData=data;const nodes=data.nodes||[];const hub=nodes.find(isHub);const loc=document.getElementById('location-name');if(loc)loc.textContent=(hub?shortName(hub.hostname):'—').toString().toUpperCase();const md=document.getElementById('mode-id');if(md){const lans=1+new Set(nodes.map(n=>n.synced_from).filter(Boolean)).size;md.textContent=nodes.length?`${lans} LAN · ${nodes.length} MACHINES`:'--';}document.getElementById('vk-status').innerHTML='<span class="status-dot"></span> HUB SIGNAL<svg class="ekg" viewBox="0 0 60 14" width="60" height="14"><path d="M0 8 H13 L17 3 L22 12 L26 8 H37 L41 5 L45 11 L49 8 H60" fill="none" stroke="#66f5ad" stroke-width="1.4"/></svg>';document.getElementById('hub-dot').classList.remove('off');const _lr=document.getElementById('last-refresh');_lr.textContent='REFRESH '+fmtTime(data.generated_at);_lr.classList.remove('blip');void _lr.offsetWidth;_lr.classList.add('blip');renderTopology(nodes);renderMetrics(nodes,data.tasks||[]);renderActivity(data.tasks||[]);}
function renderError(msg){document.getElementById('vk-status').innerHTML='<span class="status-dot off"></span> '+escapeHTML(msg);document.getElementById('hub-dot').classList.add('off');}
function addAgentMsg(kind,title,body){const log=document.getElementById('agent-log');const div=document.createElement('div');div.className='msg '+kind;div.innerHTML=`<div class="msg-meta">${escapeHTML(title)}</div>${escapeHTML(body)}`;log.appendChild(div);log.scrollTop=log.scrollHeight;return div;}
function typeMsg(title,body){const log=document.getElementById('agent-log');const div=document.createElement('div');div.className='msg agent';const meta=document.createElement('div');meta.className='msg-meta';meta.textContent=title;const span=document.createElement('span');div.appendChild(meta);div.appendChild(span);log.appendChild(div);
if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches){span.textContent=body;log.scrollTop=log.scrollHeight;return;}
let i=0;const n=Math.max(2,Math.ceil(body.length/140));const tick=()=>{i=Math.min(body.length,i+n);span.textContent=body.slice(0,i);log.scrollTop=log.scrollHeight;if(i<body.length)requestAnimationFrame(tick);};tick();}
const chatHist=[];
async function sendAgentMessage(){const input=document.getElementById('agent-message');const btn=document.getElementById('agent-send');const message=input.value.trim();if(!message||btn.disabled)return;addAgentMsg('user','❯ you',message);input.value='';btn.disabled=true;btn.textContent=t('cmd_running');input.classList.add('busy');const pending=addAgentMsg('agent pending','hub',t('proc'));try{const res=await fetch('/api/agent/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,history:chatHist.slice(-8)})});const data=await res.json();pending.remove();if(!res.ok||!data.ok){addAgentMsg('err','agent error',data.error||`HTTP ${res.status}`);}else{document.getElementById('agent-name').textContent=data.agent||'agent';typeMsg(`${data.agent||'agent'} · ${Math.round((data.duration_ms||0)/1000)}s`,data.output||'(no output)');chatHist.push({role:'user',text:message},{role:'agent',text:(data.output||'').slice(0,2000)});if(chatHist.length>16)chatHist.splice(0,chatHist.length-16);}}catch(e){pending.remove();addAgentMsg('err','console error',e.message);}finally{btn.disabled=false;btn.textContent=t('cmd_send');input.classList.remove('busy');input.focus();}}
document.addEventListener('DOMContentLoaded',()=>{const ta=document.getElementById('agent-message');if(ta)ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendAgentMessage();}});});
let refreshing=false;async function doRefresh(){if(refreshing)return;refreshing=true;try{const res=await fetch('/api/status');if(!res.ok)throw new Error(`HTTP ${res.status}`);render(await res.json());}catch(e){renderError('NO SIGNAL');}finally{refreshing=false;}}
applyStaticI18n();doRefresh();setInterval(doRefresh,30000);
</script>
</body>
</html>
"""


# ── HTTP Server ───────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    """极简 HTTP handler，只服务 / 和 /api/status。"""

    def log_message(self, fmt, *args):  # 静默请求日志
        pass

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/api/status"):
            self.send_response(200)
            self.send_header("Connection", "close")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.close_connection = True
        elif path == "/favicon.ico":
            self.send_response(204)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
        else:
            self.send_response(404)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", HTML_TEMPLATE.encode())
        elif path == "/api/status":
            try:
                data = get_status(self.server.registry_host, self.server.registry_port)
                body = json.dumps(data, ensure_ascii=False).encode()
                self._send(200, "application/json; charset=utf-8", body,
                           extra_headers={"Access-Control-Allow-Origin": "*"})
            except Exception as e:
                err = json.dumps({"error": str(e)}).encode()
                self._send(500, "application/json", err)
        elif path == "/favicon.ico":
            self._send(204, "image/x-icon", b"")
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        if self.path == "/api/agent/chat":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(min(length, 1024 * 1024))
                payload = json.loads(raw.decode("utf-8")) if raw else {}
                data = run_agent_chat(
                    payload.get("message", ""),
                    history=payload.get("history") or None,
                    cwd=payload.get("cwd") or None,
                    timeout=int(payload.get("timeout", 300) or 300),
                )
                status = 200 if data.get("ok") else 500
                body = json.dumps(data, ensure_ascii=False).encode()
                self._send(status, "application/json; charset=utf-8", body,
                           extra_headers={"Access-Control-Allow-Origin": "*"})
            except Exception as e:
                err = json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False).encode()
                self._send(500, "application/json; charset=utf-8", err)
        else:
            self._send(404, "text/plain", b"Not found")

    def do_OPTIONS(self):
        self._send(204, "text/plain", b"",
                   extra_headers={
                       "Access-Control-Allow-Origin": "*",
                       "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                       "Access-Control-Allow-Headers": "Content-Type",
                   })

    def _send(self, code: int, ctype: str, body: bytes, extra_headers: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, addr, handler, registry_host: str, registry_port: int):
        super().__init__(addr, handler)
        self.registry_host = registry_host
        self.registry_port = registry_port


# ── Port detection ────────────────────────────────────────────────────────────

def find_free_port(start: int = 7700, end: int = 7800) -> int:
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"找不到可用端口（{start}–{end}）")


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3
            )
            return str(pid) in r.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_dashboard_lock(port: int) -> Path:
    lock_path = Path(tempfile.gettempdir()) / f"myaiweb-dashboard-{port}.pid"
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            atexit.register(lambda: lock_path.exists() and lock_path.unlink())
            return lock_path
        except FileExistsError:
            try:
                old_pid = int(lock_path.read_text(encoding="utf-8").strip())
            except Exception:
                old_pid = -1
            if not _pid_running(old_pid):
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass
            raise RuntimeError(
                f"Dashboard 已在端口 {port} 运行（pid {old_pid}）。"
                "请先停止旧进程，避免多个 Dashboard 同时返回不同状态。"
            )


def local_ips() -> list[str]:
    """获取本机所有局域网 IP 地址。兼容 macOS / Linux / Windows。"""
    ips = []
    try:
        if sys.platform == "win32":
            # PowerShell 过滤掉 loopback 和 APIPA
            r = subprocess.run(
                ["powershell", "-Command",
                 "(Get-NetIPAddress -AddressFamily IPv4 | "
                 "Where-Object {$_.IPAddress -notlike '127.*' -and "
                 "$_.IPAddress -notlike '169.254.*'}).IPAddress"],
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=6
            )
            ips = [i.strip() for i in r.stdout.strip().splitlines() if i.strip()]
        elif sys.platform == "darwin":
            for iface in subprocess.run(
                ["ipconfig", "getiflist"], capture_output=True, text=True, timeout=3
            ).stdout.split():
                try:
                    ip = subprocess.run(
                        ["ipconfig", "getifaddr", iface],
                        capture_output=True, text=True, timeout=2
                    ).stdout.strip()
                    if ip and not ip.startswith("127"):
                        ips.append(ip)
                except Exception:
                    pass
        else:
            r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3)
            ips = [i for i in r.stdout.split() if not i.startswith("127") and not i.startswith("169.254")]
    except Exception:
        pass
    if not ips:
        try:
            # 通用 fallback：建立一个 UDP socket 来探测出站 IP
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ips = [s.getsockname()[0]]
        except Exception:
            pass
    return ips or ["<your-ip>"]


# ── 自动注册本机 ──────────────────────────────────────────────────────────────

def _do_register(registry_host: str, registry_port: int) -> bool:
    """运行 register_node.py，注册或刷新本机档案到 注册中心。返回是否成功。"""
    script = Path(__file__).parent / "register_node.py"
    if not script.exists():
        print("⚠️  找不到 register_node.py，跳过自动注册")
        return False
    # 带上本机的拓扑角色/归属再注册——否则不传 --role 会把已设好的角色（建网机/主控）刷成空、身份降回节点
    cmd = [sys.executable, str(script), "--registry-host", registry_host, "--registry-port", str(registry_port)]
    try:
        from identity import read_identity
        ident = read_identity() or {}
        if ident.get("role"):
            cmd += ["--role", ident["role"]]
        if ident.get("belongs_to"):
            cmd += ["--belongs-to", ident["belongs_to"]]
    except Exception:
        pass
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
            env=env
        )
        if result.returncode == 0:
            return True
        print(f"⚠️  注册失败: {result.stderr[:200] or result.stdout[:200]}")
        return False
    except Exception as e:
        print(f"⚠️  注册异常: {e}")
        return False


def _auto_register_loop(registry_host: str, registry_port: int, interval: int = 1800):
    """
    后台线程：启动时注册本机，之后每 interval 秒（默认 30 分钟）刷新一次档案。
    node:* 默认长期保存；在线/可控状态由 Dashboard 每次刷新时检测。
    """
    hostname = socket.gethostname()

    # 首次：总是注册或刷新（确保 Dashboard 启动后本机立刻出现在线）
    print(f"📡 注册本机 [{hostname}] 到 注册中心...")
    ok = _do_register(registry_host, registry_port)
    print(f"{'✅ 注册完成' if ok else '❌ 注册失败，请手动运行 register_node.py'}")

    # 定时刷新硬件/网络档案
    while True:
        time.sleep(interval)
        _do_register(registry_host, registry_port)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="myaiweb Dashboard Server")
    parser.add_argument("--registry-host", default="127.0.0.1", help="注册中心 地址（默认 127.0.0.1）")
    parser.add_argument("--registry-port", type=int, default=27182)
    parser.add_argument("--port", type=int, default=0,
                        help="HTTP 监听端口（默认 0 = 自动选择 7700-7799）")
    parser.add_argument("--bind", default="0.0.0.0",
                        help="绑定地址（默认 0.0.0.0，局域网可访问）")
    args = parser.parse_args()

    port = args.port if args.port > 0 else find_free_port()
    acquire_dashboard_lock(port)
    server = DashboardServer((args.bind, port), DashboardHandler,
                             args.registry_host, args.registry_port)

    ips = local_ips()

    print()
    print("╔══════════════════════════════════════════╗")
    print("║       myaiweb Dashboard Server           ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  注册中心  : {args.registry_host}:{args.registry_port:<27}║")
    print(f"║  Port    : {port:<30}║")
    print("╠══════════════════════════════════════════╣")
    print("║  访问地址：                              ║")
    print(f"║    http://localhost:{port:<21}║")
    for ip in ips[:3]:
        url = f"http://{ip}:{port}"
        print(f"║    {url:<38}║")
    print("╠══════════════════════════════════════════╣")
    print("║  iPad / 浏览器打开以上任意地址即可       ║")
    print("║  Ctrl+C 停止服务                         ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # 后台自动注册本机 + 定时刷新档案（daemon=True 随主进程退出）
    reg_thread = threading.Thread(
        target=_auto_register_loop,
        args=(args.registry_host, args.registry_port),
        daemon=True
    )
    reg_thread.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Dashboard 已停止")
        server.server_close()


if __name__ == "__main__":
    main()
