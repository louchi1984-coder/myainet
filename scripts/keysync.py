#!/usr/bin/env python3
"""
myainet: keysync.py
三角色 SSH 钥匙自动化 —— 把原本手动的 `ssh-copy-id` 干掉。
全程【本机操作自己的 authorized_keys + 读写 注册中心】，不往别人那儿推钥匙、不用任何密码。

谁 SSH 进谁 → 决定谁门上装谁的公钥：
  主控 → 建网机、节点      建网机 → 节点      （没人进主控，push 模型）
所以：
  --role master(主控) : 生成钥匙对(没有才生成) + 发布自己公钥        —— 只发、不收
  --role hub(建网机)  : 生成 + 发布 + 把所有控制方公钥装进自己门     —— 发 + 收主控的
  --role node(节点)   : 把所有控制方公钥(主控+建网机)装进自己门      —— 只收

公钥放 注册中心 的 `pubkey:<hostname>`（只有会主动 SSH 的机器=主控/建网机才发布；节点不发）。
装钥匙幂等（按 key 指纹去重），且只追加、绝不动你自己原有的钥匙。

用法（通常不用手动跑 —— `register_node` 注册时 + patrol 每轮都会自动调它；以下仅手动/排查用）：
  python3 keysync.py --role master|hub|node     # 不给 --registry-host 则局域网自动发现建网机
  --dry-run 只看会做什么、不动手
"""
from __future__ import annotations  # 让 X | None 等注解兼容 Python 3.7-3.9（macOS 自带 3.9）

import argparse
import os
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

sys.path.insert(0, str(Path(__file__).parent))
try:
    from registry_client import rset, rget, rkeys
except ImportError:
    print("❌ 找不到 registry_client.py，无法连接 注册中心", file=sys.stderr)
    sys.exit(1)

IS_WIN = sys.platform == "win32"
SSHDIR = Path.home() / ".ssh"


def _is_win_admin() -> bool:
    """本机是不是 Windows 管理员账号 —— 决定 OpenSSH 实际读哪个 authorized_keys。"""
    if not IS_WIN:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _authorized_keys_path() -> Path:
    """OpenSSH 真正读的 authorized_keys：
       Windows 管理员账号 → C:\\ProgramData\\ssh\\administrators_authorized_keys
         （家目录那张它根本不看 —— 这是 Win 免密最常踩、最隐蔽的坑：钥匙装了门却不认）；
       其余（posix / Win 普通账号）→ ~/.ssh/authorized_keys。"""
    if _is_win_admin():
        pd = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(pd) / "ssh" / "administrators_authorized_keys"
    return SSHDIR / "authorized_keys"


def _lock_win_admin_acl(path: Path):
    """administrators_authorized_keys 必须只许 SYSTEM + Administrators，否则 OpenSSH StrictModes 拒读。
    用内置 SID（S-1-5-18 / S-1-5-32-544）而非名字 —— 中文 Windows 上组名本地化，按名字会对不上。"""
    for args in (["/inheritance:r"], ["/grant", "*S-1-5-18:F"], ["/grant", "*S-1-5-32-544:F"]):
        try:
            subprocess.run(["icacls", str(path), *args], capture_output=True, text=True, timeout=15)
        except Exception:
            pass


def ensure_keypair():
    """有现成钥匙就用（不覆盖你 GitHub 那把），没有才生成 ed25519。返回 (公钥行, 是否新生成)。"""
    for name in ("id_ed25519", "id_ecdsa", "id_rsa"):
        pub = SSHDIR / (name + ".pub")
        if pub.exists():
            return pub.read_text(encoding="utf-8", errors="replace").strip(), False
    SSHDIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = SSHDIR / "id_ed25519"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key),
                    "-C", f"myainet-{socket.gethostname()}"],
                   capture_output=True, text=True, timeout=30)
    return (SSHDIR / "id_ed25519.pub").read_text(encoding="utf-8", errors="replace").strip(), True


def publish(host, port, publine, dry):
    hn = socket.gethostname()
    if dry:
        print(f"   [dry-run] 发布 pubkey:{hn}")
        return
    ok = rset(host, port, f"pubkey:{hn}", publine)
    print(f"   {'✅ 已发布' if ok else '⚠️ 发布失败'} 公钥 pubkey:{hn}")


def install(host, port, dry):
    """把 注册中心 里所有控制方公钥（pubkey:*，跳过自己）追加进本机 authorized_keys，幂等。
    返回本次新装的把数（patrol 每轮调它自动补钥匙，靠返回值决定要不要出声）。"""
    controllers = {}
    for k in rkeys(host, port, "pubkey:*"):
        v = rget(host, port, k)
        if v:
            controllers[k.split(":", 1)[1]] = v.strip()
    if not controllers:
        print("   ℹ️ 注册中心 里还没有控制方公钥（pubkey:*）——等主控/建网机先发布，再重跑本步。")
        return 0

    auth = _authorized_keys_path()                # Win 管理员→administrators_authorized_keys，否则 ~/.ssh/authorized_keys
    try:
        existing = auth.read_text(encoding="utf-8", errors="replace") if auth.exists() else ""
    except Exception:
        existing = ""
    have = {ln.split()[1] for ln in existing.splitlines() if len(ln.split()) >= 2}

    myhost = socket.gethostname().lower()
    to_add = []
    for src, publine in controllers.items():
        if src.lower() == myhost:
            continue                              # 自己的不用装
        parts = publine.split()
        if len(parts) < 2:
            continue
        keytype, blob = parts[0], parts[1]
        if blob in have:
            continue                              # 已在，幂等跳过
        to_add.append(f"{keytype} {blob} myainet:{src}")   # 打 myainet 标记，便于将来撤

    if not to_add:
        n = len([s for s in controllers if s.lower() != myhost])
        print(f"   ✅ authorized_keys 已是最新（{n} 把控制方公钥都在）")
        return 0
    if dry:
        print(f"   [dry-run] 将往 {auth} 追加 {len(to_add)} 把：{[l.split()[-1] for l in to_add]}")
        return len(to_add)

    win_admin = IS_WIN and _is_win_admin()
    try:
        auth.parent.mkdir(parents=True, exist_ok=True)
        with auth.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            for l in to_add:
                f.write(l + "\n")
    except PermissionError:
        print(f"   ⚠️ 没权限写 {auth}（需要管理员）。建网机 setup 提权时会成功；"
              f"普通权限下提权后重跑、或交给 setup_hub。")
        return 0
    except Exception as e:
        print(f"   ⚠️ 写 authorized_keys 失败：{e}")
        return 0

    if win_admin:
        _lock_win_admin_acl(auth)                 # 锁 ACL，否则 OpenSSH 拒读这张表 → 免密照样不生效
    elif not IS_WIN:
        try:
            os.chmod(auth, 0o600)
            os.chmod(SSHDIR, 0o700)
        except Exception:
            pass
    tail = "（Win 管理员账号 → administrators_authorized_keys，已自动设 ACL）" if win_admin else ""
    print(f"   ✅ 装入 {len(to_add)} 把控制方公钥 → {auth}{tail}")
    return len(to_add)


def main():
    p = argparse.ArgumentParser(description="myainet: 三角色 SSH 钥匙自动化")
    p.add_argument("--registry-host", default=None, help="建网机 注册中心 地址；不给则局域网广播自动发现（hub 自己用 127.0.0.1）")
    p.add_argument("--registry-port", type=int, default=27182)
    p.add_argument("--role", required=True, choices=["master", "hub", "node"],
                   help="master=主控 / hub=建网机 / node=节点")
    p.add_argument("--dry-run", action="store_true", help="只看会做什么，不动手")
    args = p.parse_args()
    dry = args.dry_run

    # 不给地址 → hub 用本机、其余局域网广播自动发现（跟 register_node 一致，不输 IP 也能配钥匙）
    if not args.registry_host:
        if args.role == "hub":
            args.registry_host = "127.0.0.1"
        else:
            try:
                from discover import discover_hub
                found = discover_hub()
            except Exception:
                found = None
            if found:
                args.registry_host, args.registry_port = found[0], found[1] or args.registry_port
                print(f"🔍 自动发现建网机：{args.registry_host}")
            else:
                print("❌ 没找到建网机（同局域网且在运行？跨网手填 --registry-host）", file=sys.stderr)
                sys.exit(2)

    print(f"🔑 myainet 钥匙同步  role={args.role}" + ("  （dry-run，不动手）" if dry else ""))

    if args.role in ("master", "hub"):
        publine, gen = ensure_keypair()
        tail = publine.split()[-1] if publine.split() else ""
        print(f"   {'🆕 生成了新钥匙对' if gen else '♻️ 用已有钥匙'}：{tail}")
        publish(args.registry_host, args.registry_port, publine, dry)

    if args.role in ("hub", "node"):
        install(args.registry_host, args.registry_port, dry)

    if args.role == "master":
        print("   ℹ️ 主控只发布、不收（push 模型里没人 SSH 进主控）。")

    print("完成。" + ("（以上为 dry-run 预览）" if dry else ""))


if __name__ == "__main__":
    main()
