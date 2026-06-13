#!/usr/bin/env python3
"""
myaiweb: setup_workspace.py
把本机设成「原生工作区」—— 无容器、无 Docker：就用本机的 OS / python / GPU + 一块盘上的工作目录。
主控可远程对节点跑它（经 SSH），全是 operate 层、没有"装运行时"那道墙。

做三件事：
  ① 定 work_dir（--dir；默认 ~/myaiweb-ws。选哪块盘，由主控读卡里 storage 决定后传进来）
  ② 建目录
  ③ 写自报标记 ~/.myaiweb/workspace.json（{kind:native, work_dir}）—— 机器据此自报进卡
  ④ 可选触发 register_node 自报，让全网立刻看到这台多了工作区

之后：进入 = ssh 这台 + cd work_dir；派活一律走 dispatch --workspace（按节点 os 自动 cd，agent 不手写 OS 命令）。

用法：
  python3 setup_workspace.py                                       # ~/myaiweb-ws，仅本机设好
  python3 setup_workspace.py --dir D:\\myaiweb-ws                  # 指定盘
  python3 setup_workspace.py --dir /data/ws --registry-host <建网机IP>   # 设好并自报进卡
"""
from __future__ import annotations

import argparse
import json
import os
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

MARKER = Path.home() / ".myaiweb" / "workspace.json"


def main():
    p = argparse.ArgumentParser(description="把本机设成原生工作区（无 Docker）")
    p.add_argument("--dir", default=str(Path.home() / "myaiweb-ws"),
                   help="工作目录（默认 ~/myaiweb-ws；选盘由主控读卡后传进来）")
    p.add_argument("--registry-host", default="", help="设好后触发 register_node 自报到这个注册中心")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--node-name", default="", help="自报时的节点名（传给 register_node）")
    args = p.parse_args()

    work_dir = Path(args.dir).expanduser()
    print(f"🧰 设原生工作区 → {work_dir}")

    # ① + ② 建目录
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"❌ 建目录失败：{e}（盘符 / 权限对吗？）", file=sys.stderr)
        sys.exit(1)
    print(f"   ✅ 目录就绪：{work_dir}")

    # ③ 写自报标记（机器据此自报进卡；sysinfo._workspace 读它）
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({"kind": "native", "work_dir": str(work_dir)},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   ✅ 标记已写：{MARKER}")

    # ④ 可选触发自报（标记已在前，register 重扫就会带上 workspace 字段）
    if args.registry_host:
        reg = Path(__file__).parent / "register_node.py"
        cmd = [sys.executable, str(reg), "--registry-host", args.registry_host,
               "--registry-port", str(args.registry_port)]
        if args.node_name:
            cmd += ["--node-name", args.node_name]
        print(f"   自报进卡 → {args.registry_host}:{args.registry_port} …")
        if subprocess.run(cmd).returncode != 0:
            print("   ⚠️ 自报没成（工作区已设好，稍后重跑 register_node 即可补上）")

    # 进入 / 用法提示
    print("\n📒 工作区已就绪（原生，无 Docker）。")
    print(f"   进入：ssh 这台 → cd {work_dir}")
    print("   派活：走 dispatch --workspace（按节点 os 自动 cd，agent 不手写 OS 命令）。")


if __name__ == "__main__":
    main()
