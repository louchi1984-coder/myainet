#!/usr/bin/env python3
"""
myainet: healthcheck.py
建网机自检 —— 看自己几个常驻服务还活着没。在建网机上本地跑（skill 认出建网机时自动跑）。
只查本机，不碰别的机器：注册中心 / Dashboard / Patrol / Tailscale，各报 ✅/❌/❓，挂的给启动命令。
（重启机器后大屏/巡检最容易没自启，所以再启动 skill 时顺手自检一下。）

用法（在建网机上）：python3 healthcheck.py
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

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


def port_open(host, port, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def proc_running(pattern):
    """本机有没有命令行匹配 pattern 的进程。查不出返回 None（不下结论）。"""
    try:
        if IS_WIN:
            ps = ("if (Get-CimInstance Win32_Process | Where-Object "
                  "{$_.CommandLine -like '*%s*'}){'YES'}else{'NO'}" % pattern)
            r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=12)
            out = r.stdout or ""
            return True if "YES" in out else (False if "NO" in out else None)
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return None


def tailscale_ok():
    ts = shutil.which("tailscale")
    if not ts:
        for c in ("/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"):
            if Path(c).exists():
                ts = c
                break
    if not ts:
        return None
    try:
        return subprocess.run([ts, "status"], capture_output=True, text=True, timeout=8).returncode == 0
    except Exception:
        return None


def mark(ok):
    return "✅" if ok else ("❌" if ok is False else "❓")


def main():
    print("🩺 建网机自检（本机服务）")

    vk = port_open("127.0.0.1", 27182)
    db = proc_running("dashboard.py")
    pt = proc_running("patrol.py")
    ts = tailscale_ok()

    # 启动命令提示按【本脚本真实所在目录 + 当前解释器】拼，不写死 ~/myainet 或 python3——
    # 这样无论 skill 装在哪、Python 叫 python/py/python3，复制粘贴都对（陌生人 clone 到任意目录也准）。
    _here = Path(__file__).resolve().parent
    _py = sys.executable or "python3"
    def _hint(script, *args):
        return f'nohup "{_py}" "{_here / script}" {" ".join(args)} > ~/{script.replace(".py","")}.log 2>&1 &'
    hint_reg = _hint("registry_server.py")
    hint_db = _hint("dashboard.py", "--registry-host", "127.0.0.1")
    hint_pt = _hint("patrol.py", "--registry-host", "127.0.0.1")

    print(f"  注册中心  {mark(vk)}" + ("" if vk else f"   ← 没通；起它：{hint_reg}"))
    print(f"  Dashboard {mark(db)}" + ("" if db is not False else f"   ← 没起；{hint_db}"))
    print(f"  Patrol    {mark(pt)}" + ("" if pt is not False else f"   ← 没起；{hint_pt}"))
    print(f"  Tailscale {mark(ts)}" + ("" if ts is not False else "   ← 没起；tailscale up（没装就先装）"))

    down = [n for n, ok in (("注册中心", vk), ("Dashboard", db), ("Patrol", pt)) if ok is False]
    print()
    if down:
        print(f"⚠️ 没起：{', '.join(down)} —— 按上面命令补起（重启机器后常见）。")
        sys.exit(1)
    print("✅ 核心服务都在。")


if __name__ == "__main__":
    main()
