#!/usr/bin/env python3
"""
myainet: watch_job.py
登记一个「已经在跑的活儿」让建网机盯着 —— 主控把跑起来的脚本/进程交给 hub 监控。
hub 的巡检（patrol.py）会周期性检查它还活着没，把状态写进 注册中心 的 task:<name>，大屏可见。

边界：只【登记】已经在跑的进程（你自己怎么起的随意，最好后台 nohup 起，关了终端也不断）；
      hub 只负责【盯】，不负责启动。进程没了就标 stopped；拿不到退出码（要退出码得在启动时
      自己把 $? 写文件，那是后续的「带监控启动」助手，不在本脚本）。

用法（--registry-host 填主建网机 IP）：
  # 按进程名匹配（最省事，不用记 pid）
  python3 watch_job.py --registry-host <主IP> --node <节点名/IP> --name 夜间训练 --match "train.py"
  # 按 pid（精确）
  python3 watch_job.py --registry-host <主IP> --node mac-studio --name 渲染 --pid 4321
  # 看 / 撤
  python3 watch_job.py --registry-host <主IP> --list
  python3 watch_job.py --registry-host <主IP> --unwatch 夜间训练
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))
try:
    from registry_client import rset, rget, rkeys, rdel
except ImportError:
    print("❌ 找不到 registry_client.py，无法连接 注册中心", file=sys.stderr)
    sys.exit(1)


def register(host, port, node, name, check, log, desc, container=None):
    human = f"pid {check['value']}" if check["type"] == "pid" else f"匹配 {check['value']}"
    if container:
        human = f"容器 {container} 内 {human}"
    now = int(time.time())
    rec = {
        "id":          name,
        "node":        node,
        "description": desc or f"监控进程：{human}",
        "status":      "running",
        "started_at":  now,
        "last_seen":   now,
        "source":      "watch",              # task:* 三种来源（dispatch/watch/report）之一
        "watch":       True,                 # 标记：这是一条「盯进程」的活儿（patrol 据此挑出来查）
        "check":       check,                # {"type": "pid"/"match", "value": ...}
        "container":   container,            # 非空=盯【容器内】进程（patrol 会 docker exec 进去查）
        "log":         log or "",
    }
    ok = rset(host, port, f"task:{name}", json.dumps(rec, ensure_ascii=False))
    return ok, rec


def list_jobs(host, port):
    rows = []
    for key in rkeys(host, port, "task:*"):
        val = rget(host, port, key)
        if not val:
            continue
        try:
            t = json.loads(val)
        except Exception:
            continue
        if not t.get("watch"):
            continue
        rows.append(t)
    if not rows:
        print("（没有登记在册的监控活儿）")
        return
    print(f"{'名称':<16} {'节点':<20} {'状态':<8} {'最近一次看到':<20} 检查")
    print("─" * 84)
    for t in rows:
        chk = t.get("check", {})
        seen = time.strftime("%m-%d %H:%M:%S", time.localtime(t.get("last_seen", 0)))
        print(f"{t.get('id',''):<16} {t.get('node',''):<20} {t.get('status',''):<8} "
              f"{seen:<20} {chk.get('type','')}={chk.get('value','')}")


def main():
    p = argparse.ArgumentParser(description="myainet: 登记进程让建网机盯着")
    p.add_argument("--registry-host", required=True, help="主建网机 注册中心 地址")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--node", default=None, help="活儿在哪台节点上（节点名或 IP，要和注册卡对得上）")
    p.add_argument("--name", default=None, help="给这个活儿起个名（也是 task 键名，重名即更新）")
    p.add_argument("--match", default=None, help="按进程名匹配（pgrep -f 的模式），省事但可能糊")
    p.add_argument("--pid", type=int, default=None, help="按 pid 精确盯（重启后 pid 会变）")
    p.add_argument("--container", default=None, help="盯【某 docker 容器内】的进程（节点上跑着容器服务时用）——检查会 docker exec 进容器里查，宿主 OS 无关；pid 是容器内 pid，一般配 --match 用")
    p.add_argument("--log", default=None, help="（可选）日志文件路径，记下来方便回看")
    p.add_argument("--desc", default=None, help="（可选）人看的描述")
    p.add_argument("--list", action="store_true", help="列出所有登记在册的监控活儿")
    p.add_argument("--unwatch", default=None, help="撤销某个监控活儿（传名称）")
    args = p.parse_args()

    if args.list:
        list_jobs(args.registry_host, args.registry_port)
        return

    if args.unwatch:
        if rdel(args.registry_host, args.registry_port, f"task:{args.unwatch}"):
            print(f"✅ 已撤销监控：{args.unwatch}")
        else:
            print(f"⚠️ 撤销失败（连不上 注册中心？）：{args.unwatch}")
        return

    # 登记模式
    if not args.node or not args.name:
        p.error("登记需要 --node 和 --name")
    if (args.pid is None) == (args.match is None):
        p.error("请二选一：--pid（精确）或 --match（按进程名），不能都给也不能都不给")

    check = {"type": "pid", "value": args.pid} if args.pid is not None \
        else {"type": "match", "value": args.match}

    ok, rec = register(args.registry_host, args.registry_port,
                       args.node, args.name, check, args.log, args.desc, args.container)
    if ok:
        print(f"✅ 已登记监控：{rec['id']}  @ {rec['node']}  （{rec['description']}）")
        print(f"   建网机巡检（patrol.py）会周期性检查它还活着没，状态写进 task:{rec['id']}，大屏可见。")
        print(f"   撤销：python3 {Path(__file__).name} --registry-host {args.registry_host} --unwatch {rec['id']}")
    else:
        print("⚠️ 登记失败：连不上主建网机 注册中心。")


if __name__ == "__main__":
    main()
