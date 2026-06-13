#!/usr/bin/env python3
"""
myaiweb: enable_ssh.py
跨平台【自动开启 SSH 服务】(macOS / Linux / Windows)，幂等。
节点入伙 / 建网机搭建时调用，免去手动去系统设置里开「远程登录」。

需要管理员权限（开 SSH 服务本身就要 root/admin）：
  · macOS / Linux：直接跑即可，脚本内部用 sudo 调用（会提示输一次密码）；
                   或你已经 `sudo python3 enable_ssh.py` 也行。
  · Windows：必须在【管理员身份】的 PowerShell / 终端里跑。

用法：
  python3 enable_ssh.py          # 自动开启并验证（幂等：已开则跳过）
  python3 enable_ssh.py --check  # 只检查，不改动任何东西
"""
import os
import shutil
import socket
import subprocess
import sys

# Windows GBK 终端会让 emoji/中文崩溃，强制 UTF-8
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LIN = sys.platform.startswith("linux")


def port22_listening() -> bool:
    """本机 22 端口是否已在监听（= SSH 服务已起）。幂等检测用。"""
    for fam, addr in ((socket.AF_INET, ("127.0.0.1", 22)),
                      (socket.AF_INET6, ("::1", 22))):
        try:
            with socket.socket(fam, socket.SOCK_STREAM) as s:
                s.settimeout(1.5)
                if s.connect_ex(addr) == 0:
                    return True
        except Exception:
            pass
    return False


def run(cmd):
    print(f"   $ {' '.join(cmd)}")
    return subprocess.run(cmd, text=True)


def sudo_prefix():
    """非 root 时加 sudo；已是 root 则不加。Windows 不用 sudo。"""
    if IS_WIN:
        return []
    try:
        if os.geteuid() == 0:
            return []
    except AttributeError:
        pass
    return ["sudo"] if shutil.which("sudo") else []


def is_admin_windows() -> bool:
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def enable_macos():
    run(sudo_prefix() + ["systemsetup", "-setremotelogin", "on"])


def enable_linux():
    s = sudo_prefix()
    # 1) 装 openssh-server（识别常见包管理器）
    if shutil.which("apt-get") or shutil.which("apt"):
        apt = "apt-get" if shutil.which("apt-get") else "apt"
        run(s + [apt, "install", "-y", "openssh-server"])
    elif shutil.which("dnf"):
        run(s + ["dnf", "install", "-y", "openssh-server"])
    elif shutil.which("yum"):
        run(s + ["yum", "install", "-y", "openssh-server"])
    elif shutil.which("pacman"):
        run(s + ["pacman", "-S", "--noconfirm", "openssh"])
    elif shutil.which("zypper"):
        run(s + ["zypper", "install", "-y", "openssh"])
    else:
        print("   ⚠️ 未识别的包管理器，请手动安装 openssh-server")
    # 2) 起服务（有的发行版叫 ssh，有的叫 sshd）
    svc = "ssh"
    r = subprocess.run(["systemctl", "list-unit-files", "sshd.service"],
                       capture_output=True, text=True)
    if "sshd.service" in r.stdout:
        svc = "sshd"
    run(s + ["systemctl", "enable", "--now", svc])


def enable_windows():
    if not is_admin_windows():
        print("   ❌ 需要【管理员】权限。请右键 PowerShell →「以管理员身份运行」后重跑本脚本。")
        return False
    ps = ["powershell", "-NoProfile", "-Command"]
    run(ps + ["Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0"])
    run(ps + ["Set-Service -Name sshd -StartupType Automatic; Start-Service sshd"])
    run(ps + [
        "if (-not (Get-NetFirewallRule -Name 'sshd' -ErrorAction SilentlyContinue)) { "
        "New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True "
        "-Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 }"
    ])
    return True


def main():
    check_only = "--check" in sys.argv
    print("🔌 myaiweb: 检查 SSH 服务...")

    if port22_listening():
        print("✅ SSH 服务已开启（22 端口在监听），无需操作。")
        return 0
    if check_only:
        print("⛔ SSH 服务未开启。去掉 --check 即可自动开启（需管理员）。")
        return 1

    print("⚙️  SSH 未开启，正在自动开启（可能要求输入一次管理员密码）...")
    try:
        if IS_MAC:
            enable_macos()
        elif IS_LIN:
            enable_linux()
        elif IS_WIN:
            if not enable_windows():
                return 1
        else:
            print("   ⚠️ 未知系统，请手动开启 SSH 服务。")
            return 1
    except Exception as e:
        print(f"   ❌ 开启过程出错：{e}")
        return 1

    if port22_listening():
        print("✅ SSH 服务已开启并在监听 22 端口。")
        return 0
    print("⚠️  命令已执行，但 22 端口暂未监听——稍等片刻、或重启服务/机器后再试一次。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
