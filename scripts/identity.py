#!/usr/bin/env python3
"""
myainet: identity.py
机器级身份标记 —— 把"这台是谁"从【当前目录有没有 config.md】挪到【机器级文件】，
让 skill 在任何目录加载都认得自己（身份不再绑目录）。

标记：~/.myainet/identity.json
  { "role": "建网机|主控|次建网机|节点",
    "central": "<中央 注册中心 / 主建网机地址；建网机=自己，节点/主控/次填它>",
    "name": "<本机名>",
    "belongs_to": "<节点用：归哪台建网机>" }

判定规则（detect，标记优先、别被本机端口骗）：
  · 标记里有 role            → 用标记（次建网机/主控/节点全靠它；次也起 注册中心、同占 27182）
  · 没标记但本机 27182 通     → 当建网机（兜底：标记丢了但 注册中心 在）
  · 都没有                   → 新机器（让 skill 问角色）

能力与职责从身份推导，不另存权限位：
  建网机 = 控制全网 + 监听/写大屏(注册中心+patrol+dashboard)
  主控   = 控制全网（借建网机 注册中心 + 持本地镜像，抗掉线）
  节点   = 被控

用法：
  python3 identity.py                                          # 打印身份判定（facts，给 AI 读）
  python3 identity.py --set --role 节点 --central 192.168.1.10 [--name x] [--belongs-to y]
其它脚本：from identity import read_identity, write_identity, detect
"""
from __future__ import annotations  # X | None 注解兼容 3.7-3.9（macOS 自带 3.9）

import argparse
import json
import os
import socket
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

IDENTITY_DIR = Path.home() / ".myainet"
IDENTITY_PATH = IDENTITY_DIR / "identity.json"


def read_identity():
    """读机器级标记；没有 / 坏了返回 None。"""
    try:
        return json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


_LOCAL_CENTRAL = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


# ── 角色判定：中英都认（英文环境的机器上报 role="hub"/"control"/"node" 也不破匹配）──
def is_hub_like(role) -> bool:
    """建网机或次建网机（能当跳板 / 扛 infra）。中文「建网」/ 英文 hub / secondary。"""
    r = (role or "").lower()
    return "建网" in r or "hub" in r or "secondary" in r


def is_main_hub(role) -> bool:
    """【主】建网机 —— 唯一 central=自己(127.0.0.1) 合法的角色。排除次建网机（它 central 填主地址）。"""
    r = (role or "").lower()
    if "次建网" in r or "secondary" in r or "sub-hub" in r or "subhub" in r:
        return False
    return "建网" in r or "hub" in r


def is_control(role) -> bool:
    """主控。中文「主控」/ 英文 control / master。"""
    r = (role or "").lower()
    return "主控" in r or "control" in r or "master" in r


def write_identity(role=None, central=None, name=None, belongs_to=None):
    """写 / 更新标记。只动明确传入的字段（None = 不动、保留原值）。返回写后的 dict。"""
    data = read_identity() or {}
    if role is not None:
        data["role"] = role
    if central is not None:
        # central 卫士：只有建网机的 central=自己(127.0.0.1)才合法；主控/次/节点的 central 必须是【别的机器】
        # （建网机地址）。写成 localhost = 毒值——指向自己 → 裸加载 skill 查不到注册中心 → 静默空 →
        # 误判「注册表空了要恢复」（真实事故 2026-06-12）。毒值一律拒写：保留原有好值，绝不覆盖成自指。
        eff_role = data.get("role") or ""
        if not is_main_hub(eff_role) and str(central).strip().lower() in _LOCAL_CENTRAL:
            print(f"⚠️ 拒写毒值 central={central!r}（{eff_role or '非建网机'}的 central 不能指向自己）"
                  f"—— 应填建网机地址。保留原值 central={data.get('central')!r}", file=sys.stderr)
        else:
            data["central"] = central
    if name is not None:
        data["name"] = name
    if belongs_to is not None:
        data["belongs_to"] = belongs_to
    data.setdefault("name", socket.gethostname())
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    IDENTITY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return data


def _local_registry(timeout=2):
    try:
        with socket.create_connection(("127.0.0.1", 27182), timeout=timeout):
            return True
    except Exception:
        return False


def detect():
    """机器级身份判定。返回 dict：role / central / name / belongs_to / has_local_registry / source。"""
    marker = read_identity()
    has_registry = _local_registry()
    name = (marker or {}).get("name") or socket.gethostname()
    if marker and marker.get("role"):
        return {"role": marker["role"], "central": marker.get("central"),
                "name": name, "belongs_to": marker.get("belongs_to"),
                "has_local_registry": has_registry, "source": "标记"}
    if has_registry:
        return {"role": "建网机", "central": "127.0.0.1", "name": name,
                "belongs_to": None, "has_local_registry": True,
                "source": "本机注册中心(无标记)"}
    return {"role": None, "central": None, "name": name, "belongs_to": None,
            "has_local_registry": False, "source": "无"}


def main():
    p = argparse.ArgumentParser(description="myainet: 机器级身份标记（读 / 写 / 判定）")
    p.add_argument("--set", action="store_true", help="写标记（配合 --role 等）")
    p.add_argument("--role", default=None, help="建网机 / 主控 / 次建网机 / 节点")
    p.add_argument("--central", default=None, help="中央 注册中心 / 主建网机地址")
    p.add_argument("--name", default=None, help="本机名（默认主机名）")
    p.add_argument("--belongs-to", default=None, help="（节点用）归哪台建网机")
    args = p.parse_args()

    if args.set:
        if not args.role:
            print("❌ --set 需要 --role（建网机 / 主控 / 次建网机 / 节点）", file=sys.stderr)
            sys.exit(1)
        # 当场拦坏值：非建网机角色填 localhost 当 central = 注定指向自己、查不到注册中心。直接拒，别写进去再补救。
        if not is_main_hub(args.role) and args.central is not None \
                and str(args.central).strip().lower() in _LOCAL_CENTRAL:
            print(f"❌ {args.role} 的 --central 不能是 {args.central!r}（指向自己）—— 要填【建网机】的地址"
                  f"（同 LAN 填它 lan_ip，异地填它 Tailscale IP）。", file=sys.stderr)
            sys.exit(1)
        data = write_identity(args.role, args.central, args.name, args.belongs_to)
        print(f"✅ 身份已写 {IDENTITY_PATH}")
        for k in ("role", "central", "name", "belongs_to"):
            if data.get(k):
                print(f"   {k}={data[k]}")
        return

    d = detect()
    # facts-only：打印 key=value，AI 据此决定走哪条
    # （建网机→自检+控制台 / 主控→控制台 / 次建网机→注册中心+同步给主 / 节点→认得自己 / 新机器→问角色）
    print(f"role={d['role'] if d['role'] else '（未知-新机器）'}")
    print(f"central={d['central'] or ''}")
    print(f"name={d['name']}")
    print(f"belongs_to={d.get('belongs_to') or ''}")
    print(f"has_local_registry={'yes' if d['has_local_registry'] else 'no'}")
    print(f"source={d['source']}")


if __name__ == "__main__":
    main()
