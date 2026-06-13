#!/usr/bin/env python3
"""
myainet: tailscale_proxy_bypass.py
Add Tailscale ranges to system proxy bypass domains.

This is intentionally idempotent: running it multiple times keeps existing
rules and only appends missing myainet/Tailscale bypass entries.
"""

import platform
import subprocess
import sys


REQUIRED_RULES = [
    "100.64.0.0/10",
    "100.*",
    "*.ts.net",
]

WINDOWS_RULES = [
    "100.*",
    "*.ts.net",
]


def run(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def list_macos_services() -> list[str]:
    result = run("networksetup", "-listallnetworkservices")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    services: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("An asterisk"):
            continue
        if line.startswith("*"):
            continue
        services.append(line)
    return services


def get_macos_bypass_domains(service: str) -> list[str]:
    result = run("networksetup", "-getproxybypassdomains", service)
    if result.returncode != 0:
        return []

    domains: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "aren't any bypass domains" in line:
            continue
        domains.append(line)
    return domains


def update_macos_service(service: str) -> tuple[bool, list[str]]:
    current = get_macos_bypass_domains(service)
    merged = current[:]
    lower_seen = {item.lower() for item in merged}

    changed = False
    for rule in REQUIRED_RULES:
        if rule.lower() not in lower_seen:
            merged.append(rule)
            lower_seen.add(rule.lower())
            changed = True

    if changed:
        result = run("networksetup", "-setproxybypassdomains", service, *merged)
        if result.returncode != 0:
            raise RuntimeError(
                f"{service}: {result.stderr.strip() or result.stdout.strip()}"
            )
    return changed, merged


def configure_macos() -> int:
    services = list_macos_services()
    if not services:
        print("myainet: 未发现可配置的网络服务。")
        return 0

    any_changed = False
    for service in services:
        try:
            changed, domains = update_macos_service(service)
            any_changed = any_changed or changed
            status = "updated" if changed else "ok"
            print(f"{status}: {service} -> {', '.join(domains)}")
        except Exception as exc:
            print(f"warn: {service}: {exc}", file=sys.stderr)

    if any_changed:
        print("myainet: 已加入 Tailscale 代理绕过规则。")
    else:
        print("myainet: Tailscale 代理绕过规则已存在。")
    return 0


def ps(script: str) -> subprocess.CompletedProcess[str]:
    return run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script)


def configure_windows() -> int:
    path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    get_result = ps(f"(Get-ItemProperty -Path '{path}' -Name ProxyOverride -ErrorAction SilentlyContinue).ProxyOverride")
    current_raw = get_result.stdout.strip() if get_result.returncode == 0 else ""
    current = [item.strip() for item in current_raw.split(";") if item.strip()]

    merged = current[:]
    lower_seen = {item.lower() for item in merged}
    changed = False
    for rule in WINDOWS_RULES:
        if rule.lower() not in lower_seen:
            merged.append(rule)
            lower_seen.add(rule.lower())
            changed = True

    if changed:
        value = ";".join(merged)
        set_result = ps(f"Set-ItemProperty -Path '{path}' -Name ProxyOverride -Value '{value}'")
        if set_result.returncode != 0:
            raise RuntimeError(set_result.stderr.strip() or set_result.stdout.strip())
        print(f"updated: Windows ProxyOverride -> {value}")
    else:
        print(f"ok: Windows ProxyOverride -> {';'.join(merged) or '(empty)'}")

    return 0


def main() -> int:
    system = platform.system()
    if system == "Darwin":
        return configure_macos()
    if system == "Windows":
        return configure_windows()

    print("myainet: 当前系统没有统一的系统代理绕过接口，请在代理软件中加入 100.64.0.0/10 DIRECT。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
