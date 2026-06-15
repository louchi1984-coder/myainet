#!/usr/bin/env python3
"""
myainet: setup_workspace.py
把本机设成「原生工作区」—— 无容器、无 Docker：就用本机的 OS / python / GPU + 一块盘上的工作目录。
主控可远程对节点跑它（经 SSH），全是 operate 层、没有"装运行时"那道墙。

做三件事：
  ① 定 work_dir（--dir；默认 ~/myainet-ws。选哪块盘，由主控读卡里 storage 决定后传进来）
  ② 建目录
  ③ 写自报标记 ~/.myainet/workspace.json（{kind:native, work_dir}）—— 机器据此自报进卡
  ④ 可选触发 register_node 自报，让全网立刻看到这台多了工作区

之后：进入 = ssh 这台 + cd work_dir；派活一律走 dispatch --workspace（按节点 os 自动 cd，agent 不手写 OS 命令）。

用法：
  python3 setup_workspace.py                                       # ~/myainet-ws，仅本机设好
  python3 setup_workspace.py --dir D:\\myainet-ws                  # 指定盘
  python3 setup_workspace.py --dir /data/ws --registry-host <建网机IP>   # 设好并自报进卡

  # 【主控本地】给某远程节点的工作区建"本地把手"——把 CLAUDE.md/AGENTS.md（指向远端）
  # 写进【当前目录】（= 你在 Desktop 选定的工作区文件夹；要落别处用 --at）。本地不占地方。
  cd <你选定的本地工作区文件夹> && python3 ~/.../setup_workspace.py --handle <远程节点>
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

MARKER = Path.home() / ".myainet" / "workspace.json"


def make_local_handle(node: str, host: str, port: int, at: str = "") -> None:
    """在【主控本地】生成"远程工作区把手"——空壳目录 + CLAUDE.md/AGENTS.md。
    Desktop 的 Claude/codex 只能选本地文件夹当工作区；这个壳让你选它、agent 一打开就读 md，
    知道真正的工作区在远程节点上，去那边干（dispatch --workspace / ssh）。本地不放文件、不占地方。"""
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from registry_client import rget
    except ImportError:
        print("❌ 找不到 registry_client.py", file=sys.stderr); sys.exit(1)

    if not host:                                  # 没给就从本机身份的 central 读（跟 dispatch 一个规矩）
        try:
            host = json.loads((Path.home() / ".myainet" / "identity.json").read_text(encoding="utf-8")).get("central", "")
        except Exception:
            host = ""
    if not host:
        print("❌ 没有注册中心地址：给 --registry-host，或先把本机配成主控（identity 里有 central）", file=sys.stderr); sys.exit(1)

    try:
        card = json.loads(rget(host, port, f"node:{node}") or "{}")
    except Exception as e:
        print(f"❌ 读注册中心失败（{host}:{port}）：{e}", file=sys.stderr); sys.exit(1)
    if not isinstance(card, dict) or not card.get("hostname"):
        print(f"❌ 注册中心查不到节点 {node}（先确认它已注册）", file=sys.stderr); sys.exit(1)

    ws = card.get("workspace") or {}
    work_dir = ws.get("work_dir")
    if not work_dir:
        print(f"❌ 节点 {node} 还没设工作区——先在它上面设：", file=sys.stderr)
        print(f'   dispatch.py --node {node} "<它的python> <它的scripts>/setup_workspace.py --dir <盘:\\路径> --registry-host {host} --node-name {node}"', file=sys.stderr)
        sys.exit(1)

    hw = card.get("hardware") or {}
    net = card.get("network") or {}
    os_   = ws.get("os") or hw.get("os") or "?"
    shell = ws.get("shell") or "?"
    py    = ws.get("python") or card.get("python") or "python3"
    gpu   = hw.get("gpu") or ("有 GPU 直连" if ws.get("gpu") else "无")
    ssh_cmd = net.get("ssh_tailscale") or net.get("ssh") or f"ssh {node}"
    local_py = sys.executable or "python3"
    dispatch = Path(__file__).resolve().parent / "dispatch.py"

    # 默认落【当前目录】——agent 在用户选定的工作区文件夹里跑，md 就直接进那个文件夹（不再另造 ~/myainet-ws-<节点>）
    handle = Path(at).expanduser() if at else Path.cwd()
    handle.mkdir(parents=True, exist_ok=True)

    md = f"""# myainet 远程工作区·把手（本地空壳，别在这放文件）

**你打开的是一个"把手"目录，不是真正的工作区。** 真正的工作区在远程节点上；
本地这里只有这份说明，**不要在本地建 / 下载文件**——所有产物都落在远端那块盘。

## 远程工作区
- 节点：`{node}`
- 工作目录(work_dir)：`{work_dir}`
- 系统：`{os_}`　shell：`{shell}`　解释器：`{py}`　GPU：`{gpu}`
- SSH：`{ssh_cmd}`

## 怎么干活（在远端，不在本地）
- 派命令（推荐——自动 cd 到 work_dir、用远端解释器、记账上大屏）：
  `{local_py} {dispatch} --node {node} --workspace "<在远端跑的命令>"`
- 交互式进去：`{ssh_cmd}`，再 `cd "{work_dir}"`
- 看这台实时状态 / 已装能力 / 经验便签：读它的卡 `node:{node}`（myainet 注册中心）或开大屏。

## 规矩
- 跨 OS 的路径 / 解释器**读上面的契约、别猜**（Windows 反斜杠、解释器可能是 `python` 不是 `python3`）。
- 别把数据 / 模型往本地放——本地是把手，远端才是盘。

## 本项目经验（往这记、别删旧的）
<在这个项目里用这台机器/工作区干活攒的经验都写这儿：哪个配置/参数最优、踩过的坑、验过走不通的方向。
下次在这个文件夹开会话会自动读到，不必从零再试。
（机器级的客观事实——比如"这台装了 X 能力"——写它的注册卡 `notes`，不写这。这里只放*本项目*的经验。）>
"""
    for name in ("CLAUDE.md", "AGENTS.md"):
        (handle / name).write_text(md, encoding="utf-8")
    print(f"🔗 远程工作区把手已建：{handle}")
    print(f"   指向：{node}:{work_dir}（{os_}/{shell}）")
    print("   用法：Desktop 的 Claude/codex 选这个文件夹当工作区——打开即读 CLAUDE.md/AGENTS.md，去远端干。")


def main():
    p = argparse.ArgumentParser(description="把本机设成原生工作区（无 Docker）")
    p.add_argument("--dir", default=str(Path.home() / "myainet-ws"),
                   help="工作目录（默认 ~/myainet-ws；选盘由主控读卡后传进来）")
    p.add_argument("--registry-host", default="", help="设好后触发 register_node 自报到这个注册中心")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--node-name", default="", help="自报时的节点名（传给 register_node）")
    p.add_argument("--handle", default="", metavar="节点名",
                   help="【主控本地】给某远程节点的工作区建一个本地把手（空壳+CLAUDE.md/AGENTS.md 指向远端）——给 Desktop 的 Claude/codex 当本地工作区用，本地不占地方")
    p.add_argument("--at", default="", help="把手目录放哪（配合 --handle；默认 ~/myainet-ws-<节点>）")
    args = p.parse_args()

    if args.handle:                               # 主控侧：建本地把手，不做节点侧那套
        make_local_handle(args.handle, args.registry_host, args.registry_port, args.at)
        return

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
