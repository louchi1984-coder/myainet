#!/usr/bin/env python3
"""
myainet: report.py
agent 汇报（监控模式②）—— 让 agent 主动往看板写一条【带判断】的话。
机器写"还活着没"（patrol 写事实），agent 写"发生了啥、好不好、要不要管"（这个，写判断）。

注：你【委托】给 agent 跑的活（dispatch --node X "claude -p '...'"），它的输出已经自动进了
task:*.output——那已经是它的汇报。本脚本是给"没被派、agent 自己主动冒一句"用的
（比如跑完顺手说一句、或发现"B 机磁盘快满了"）。

用法（在 agent 所在机器上，--registry-host 填主建网机；话整体加引号）：
  python3 report.py --registry-host <主IP> "训练完了，准确率 92%"
  python3 report.py --registry-host <主IP> --node nas-box --warn "磁盘只剩 5%，建议清缓存"
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import json
import os
import socket
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
    from registry_client import rset, rget
except ImportError:
    print("❌ 找不到 registry_client.py，无法连接 注册中心", file=sys.stderr)
    sys.exit(1)


def main():
    p = argparse.ArgumentParser(description="myainet: agent 汇报（往看板写一条 note）")
    p.add_argument("--registry-host", required=True, help="主建网机 注册中心 地址")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--node", default=None, help="跟哪台节点有关（可选，默认本机）")
    p.add_argument("--warn", action="store_true", help="标为警告（话前面加 ⚠️）")
    p.add_argument("--card", action="store_true",
                   help="durable：把这条钉到那台节点的卡上（notes）——装了非-LLM 能力 / 实测最优配置 / 踩过的坑写这，别的任务读卡即知，不重复试错。（不加=只写流水 task:*，大屏看得见但不挂卡）")
    p.add_argument("message", help="要汇报的话（整体加引号）")
    args = p.parse_args()

    by = socket.gethostname()
    node = args.node or by
    now = int(time.time())
    msg = ("⚠️ " if args.warn else "") + args.message
    tid = f"report-{now}"

    rec = {
        "id":          tid,
        "node":        node,
        "description": msg,
        "status":      "note",      # 大屏给灰底；不计入"进行中"
        "by":          by,
        "started_at":  now,
        "source":      "report",    # task:* 三种来源（dispatch/watch/report）之一
    }
    ok = rset(args.registry_host, args.registry_port, f"task:{tid}", json.dumps(rec, ensure_ascii=False))
    if ok:
        print(f"📝 已汇报：{msg}   （node={node}，task:{tid}，大屏任务栏可见）")
    else:
        print("⚠️ 写入失败：连不上主建网机 注册中心。", file=sys.stderr)

    # --card：durable 钉到那台节点的卡（notes）。读出卡 → 追加 → 写回（保留 register 重建时会带上的，见 register_node.write_to_registry）
    if args.card:
        nkey = f"node:{node}"
        try:
            ncard = json.loads(rget(args.registry_host, args.registry_port, nkey) or "{}")
        except Exception:
            ncard = {}
        if isinstance(ncard, dict) and ncard.get("hostname"):
            notes = ncard.get("notes") or []
            notes.append({"text": args.message, "warn": bool(args.warn), "ts": now, "by": by})
            ncard["notes"] = notes[-50:]   # 留近 50 条，别无限涨
            if rset(args.registry_host, args.registry_port, nkey, json.dumps(ncard, ensure_ascii=False)):
                print(f"📌 已钉到 {node} 的卡（notes，别的任务读卡即知）")
                ok = True
            else:
                print("⚠️ 钉卡失败：写不回注册中心。", file=sys.stderr)
        else:
            print(f"⚠️ 找不到 {node} 的卡（先确认它已注册），便签没钉上。", file=sys.stderr)

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
