#!/usr/bin/env python3
"""
myaiweb: registry_cache.py
主控的注册表本地镜像 —— 主控每次读全网时把原始节点卡存一份到本地。
作用：① 建网机掉线时主控仍知道每台机器（配合 dispatch 回退，能直驱够得到的）；
      ② 这份镜像就是【转移时的注册表备份】（老 hub 死了也能拿它喂新 hub）。

镜像：~/.myaiweb/registry-cache.json
  { "saved_at": <epoch>, "central": "<来源地址>",
    "cards": { "node:xxx": "<卡 JSON 字符串，原样>", ... } }

要点：读不到节点卡（建网机够不到 / 空注册表）就【不覆盖】，保住上次的好备份。

用法：
  python3 registry_cache.py --registry-host <中央> [--registry-port 27182]   # 存一份
其它脚本：from registry_cache import load_cards          # dispatch 回退用（解析好的卡 list）
          from registry_cache import load_card_strings  # 转移用（原样 rset 回新 hub）
"""
from __future__ import annotations  # X | None 注解兼容 3.7-3.9（macOS 自带 3.9）

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
    from registry_client import rmap
except ImportError:
    rmap = None

CACHE_DIR = Path.home() / ".myaiweb"
CACHE_PATH = CACHE_DIR / "registry-cache.json"


def save_mirror(host, port):
    """从建网机 注册中心 读所有 node:*，原样存本地。读不到则不覆盖。返回存了几张。"""
    if not rmap:
        return 0
    cards = rmap(host, port, "node:*")   # 一条连接拉全，省中继往返
    if not cards:
        return 0  # 建网机够不到 / 空注册表 → 别覆盖，保住上次的好镜像
    data = {"saved_at": int(time.time()), "central": str(host), "cards": cards}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return len(cards)


def load_raw():
    """读镜像原始结构（含 saved_at / central / cards）；没有返回 None。"""
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_cards():
    """解析好的卡 list（dispatch 回退解析节点用）。"""
    raw = load_raw()
    out = []
    for v in ((raw or {}).get("cards") or {}).values():
        try:
            out.append(json.loads(v))
        except Exception:
            continue
    return out


def load_card_strings():
    """{key: 原始JSON字符串}（转移时直接 rset 回新 hub，不丢字段）。"""
    raw = load_raw()
    return dict((raw or {}).get("cards") or {})


def main():
    p = argparse.ArgumentParser(description="myaiweb: 主控注册表本地镜像（抗建网机掉线 + 转移备份）")
    p.add_argument("--registry-host", required=True, help="中央 注册中心 地址")
    p.add_argument("--registry-port", type=int, default=27182)
    args = p.parse_args()
    n = save_mirror(args.registry_host, args.registry_port)
    if n:
        print(f"✅ 已镜像 {n} 张节点卡 → {CACHE_PATH}")
        print(f"   来源 {args.registry_host}（建网机掉线时主控靠它兜底；也是转移备份）")
    else:
        print(f"⚠️ 没从 {args.registry_host} 读到节点卡——镜像未覆盖，保留上次的好备份。")


if __name__ == "__main__":
    main()
