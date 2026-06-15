#!/usr/bin/env python3
"""
myainet: sysinfo.py
跨平台系统信息采集 (macOS / Linux / Windows)
输出 key=value 格式，供 register_node.py 解析。

用法：python3 sysinfo.py
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LIN = sys.platform.startswith("linux")

# 强制 UTF-8 输出（与 register_node / dashboard / dispatch / patrol 一致）：
# 单独 `python sysinfo.py` 在中文 Windows（GBK 终端）直接跑时，打印中文/应用名也不崩；
# 被 register_node 当子进程调时本来就继承父进程的 PYTHONIOENCODING，这里只是让它自给自足。
# 注意：内部 run() 抓「原生系统工具」输出仍用 locale 解码（Win 原生工具吐 GBK，不能强转 UTF-8）。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def emit(key: str, val):
    print(f"{key}={val}")


def run(*cmd, timeout=5) -> str:
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def run_sh(cmd: str, timeout=5) -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def ps(cmd: str, timeout=8) -> str:
    """Windows PowerShell 命令"""
    return run("powershell", "-NoProfile", "-Command", cmd, timeout=timeout)


# ── 基础信息 ─────────────────────────────────────────────────────────────────

emit("hostname", socket.gethostname())
emit("user",     os.environ.get("USERNAME") or os.environ.get("USER") or "user")
emit("os",       f"{platform.system()} {platform.release()} {platform.machine()}")
emit("python_ver", platform.python_version())
emit("python",   sys.executable)   # 这台机器实际能用的 Python 解释器（自报=正在跑 sysinfo 的这个）；跨平台调脚本读它，别猜 python/python3

# ── 局域网 IP（UDP socket trick，三平台均有效）────────────────────────────────

lan_ip = "unknown"
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
except Exception:
    pass
emit("lan_ip", lan_ip)

# ── CPU ──────────────────────────────────────────────────────────────────────

cpu_model   = "unknown"
cpu_cores   = "?"
cpu_threads = "?"

if IS_MAC:
    cpu_model   = run("sysctl", "-n", "machdep.cpu.brand_string")
    cpu_cores   = run("sysctl", "-n", "hw.physicalcpu")
    cpu_threads = run("sysctl", "-n", "hw.logicalcpu")
elif IS_WIN:
    cpu_model   = ps("(Get-WmiObject Win32_Processor).Name")
    cpu_cores   = ps("(Get-WmiObject Win32_Processor).NumberOfCores")
    cpu_threads = ps("(Get-WmiObject Win32_Processor).NumberOfLogicalProcessors")
else:
    for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
        if "model name" in line:
            cpu_model = line.split(":", 1)[1].strip()
            break
    cpu_threads = run("nproc") or run_sh("grep -c ^processor /proc/cpuinfo")
    cpu_cores   = run_sh(
        "grep 'cpu cores' /proc/cpuinfo | head -1 | awk -F: '{print $2}'"
    ).strip() or cpu_threads

emit("cpu_model",   cpu_model.strip())
emit("cpu_cores",   cpu_cores.strip())
emit("cpu_threads", cpu_threads.strip())

# ── GPU ──────────────────────────────────────────────────────────────────────

gpu_model     = "none"
gpu_vram_gb   = "0"
gpu_framework = "none"
gpu_count     = "0"

# NVIDIA（三平台均可用 nvidia-smi）
nsmi = run("nvidia-smi",
           "--query-gpu=name,memory.total",
           "--format=csv,noheader,nounits",
           timeout=8)
if nsmi:
    lines = [l.strip() for l in nsmi.splitlines() if l.strip()]
    gpu_count = str(len(lines))
    if lines:
        parts = lines[0].split(",")
        gpu_model = parts[0].strip()
        try:
            gpu_vram_gb = str(round(float(parts[1].strip()) / 1024, 1))
        except Exception:
            pass
    gpu_framework = "cuda"

elif IS_MAC:
    sp = run("system_profiler", "SPDisplaysDataType", timeout=10)
    for line in sp.splitlines():
        ll = line.strip()
        if ("Chipset Model:" in ll or "Chip:" in ll) and gpu_model == "none":
            gpu_model = ll.split(":", 1)[1].strip()
        if "VRAM" in ll:
            raw = ll.split(":", 1)[1].strip()
            try:
                num = float("".join(c for c in raw if c.isdigit() or c == "."))
                gpu_vram_gb = str(int(num)) if "GB" in raw.upper() else str(round(num / 1024, 1))
            except Exception:
                pass
    # Apple Silicon：VRAM = 统一内存的一部分，用 RAM 估算
    if gpu_model and any(k in gpu_model for k in ("Apple", "M1", "M2", "M3", "M4")):
        gpu_framework = "metal"
        gpu_count = "1"
        if gpu_vram_gb == "0":
            # 没有独立 VRAM 字段时，暂报 0（用户可手动调）
            gpu_vram_gb = "0"

elif IS_WIN:
    # 排除 Microsoft 基本显示适配器（纯软件驱动）
    wgpu = ps(
        "(Get-WmiObject Win32_VideoController | "
        "Where-Object {$_.AdapterCompatibility -ne 'Microsoft'} | "
        "Select-Object -First 1).Name"
    )
    if wgpu.strip():
        gpu_model = wgpu.strip()
        gpu_count = "1"
        vram_b = ps(
            "(Get-WmiObject Win32_VideoController | "
            "Where-Object {$_.AdapterCompatibility -ne 'Microsoft'} | "
            "Select-Object -First 1).AdapterRAM"
        ).strip()
        try:
            gb = round(int(vram_b) / (1024 ** 3), 1)
            gpu_vram_gb = str(gb)
        except Exception:
            pass
        # AMD 卡用 OpenCL/ROCm（保守标注）
        if "AMD" in gpu_model or "Radeon" in gpu_model:
            gpu_framework = "opencl"
        elif "NVIDIA" in gpu_model or "GeForce" in gpu_model or "RTX" in gpu_model:
            gpu_framework = "cuda"

elif IS_LIN:
    rocm = run("rocm-smi", "--showproductname", timeout=6)
    if rocm and "GPU" in rocm:
        for line in rocm.splitlines():
            if "GPU" in line:
                gpu_model = line.strip()
                break
        gpu_framework = "rocm"
        gpu_count = "1"
    else:
        lspci = run("lspci", timeout=5)
        for line in lspci.splitlines():
            if "VGA" in line or "3D controller" in line:
                gpu_model = line.split(":", 2)[-1].strip()
                gpu_count = "1"
                break

emit("gpu_model",     gpu_model)
emit("gpu_vram_gb",   gpu_vram_gb)
emit("gpu_framework", gpu_framework)
emit("gpu_count",     gpu_count)

# ── RAM ──────────────────────────────────────────────────────────────────────

ram_gb = "?"
if IS_MAC:
    mem = run("sysctl", "-n", "hw.memsize")
    try:
        ram_gb = str(round(int(mem) / (1024 ** 3)))
    except Exception:
        pass
elif IS_WIN:
    mem = ps("(Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory")
    try:
        ram_gb = str(round(int(mem.strip()) / (1024 ** 3)))
    except Exception:
        pass
else:
    for line in Path("/proc/meminfo").read_text(errors="ignore").splitlines():
        if line.startswith("MemTotal"):
            try:
                ram_gb = str(round(int(line.split()[1]) / (1024 ** 2)))
            except Exception:
                pass
            break

emit("ram_gb", ram_gb)

# ── 存储 ─────────────────────────────────────────────────────────────────────

disk_total_gb = "?"
disk_avail_gb = "?"
disk_used_pct = "?"
disk_type     = "unknown"

try:
    root = "C:\\" if IS_WIN else "/"
    usage = shutil.disk_usage(root)
    disk_total_gb = str(round(usage.total / (1024 ** 3), 1))
    disk_avail_gb = str(round(usage.free  / (1024 ** 3), 1))
    disk_used_pct = str(round((1 - usage.free / usage.total) * 100))
except Exception:
    pass

# 磁盘类型
if IS_MAC:
    out = run_sh("diskutil info / | grep 'Solid State'")
    disk_type = "ssd" if "Yes" in out else "hdd"
elif IS_WIN:
    dt = ps(
        "(Get-PhysicalDisk | Sort-Object DeviceId | Select-Object -First 1).MediaType"
    ).strip().lower()
    disk_type = dt if dt in ("ssd", "hdd") else dt or "unknown"
else:
    # Linux：读取旋转标志
    rot = run_sh(
        "cat /sys/block/$(lsblk -dno pkname "
        "$(df / | tail -1 | awk '{print $1}') 2>/dev/null)"
        "/queue/rotational 2>/dev/null"
    )
    if rot == "0":
        disk_type = "ssd"
    elif rot == "1":
        disk_type = "hdd"

emit("disk_total_gb",   disk_total_gb)
emit("disk_avail_gb",   disk_avail_gb)
emit("disk_used_pct",   disk_used_pct)
emit("total_storage_gb", disk_total_gb)
emit("disk_type",       disk_type)


# ── 每块盘容量/空闲（供主控选盘：挑最空那块当工作区，多盘 Windows 尤其需要）──────────
def _disks():
    """枚举本机每块盘的容量+空闲。Win：本地固定盘(DriveType=3)；posix：df 真实挂载（滤伪 fs）。失败回 []。"""
    out = []
    if IS_WIN:
        # 纯单引号拼字符串，避免嵌套双引号被 PowerShell 包装层吃掉
        raw = ps("Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | "
                 "ForEach-Object { $_.DeviceID + '|' + $_.Size + '|' + $_.FreeSpace }")
        for ln in raw.splitlines():
            c = ln.strip().split("|")
            if len(c) == 3 and c[1].strip().isdigit():
                out.append({"mount": c[0], "total_gb": round(int(c[1]) / 1073741824, 1),
                            "avail_gb": round(int(c[2] or 0) / 1073741824, 1)})
    else:
        SKIP = ("tmpfs", "devfs", "overlay", "udev", "none", "map", "/dev/loop")
        seen = set()
        for ln in run("df", "-P", "-k").splitlines()[1:]:
            c = ln.split()
            if len(c) < 6 or any(c[0].startswith(s) for s in SKIP) or c[-1] in seen:
                continue
            try:
                tot, avail = int(c[1]) * 1024, int(c[3]) * 1024
            except ValueError:
                continue
            if tot < 1073741824:                  # 跳 <1G 的小挂载（boot/EFI 等）
                continue
            seen.add(c[-1])
            out.append({"mount": c[-1], "total_gb": round(tot / 1073741824, 1),
                        "avail_gb": round(avail / 1073741824, 1)})
    return out


try:
    _disks_list = _disks()
except Exception:
    _disks_list = []
import json as _dj   # _json 的正式定义在后面（models 处），此处提前要序列化，本地引一次
emit("disks", _dj.dumps(_disks_list, ensure_ascii=False))

# ── 能力探测：① AI agent（带版本）② 已安装工具/运行时（扁平）③ ollama 模型 ──

def _version(cmd: str) -> str:
    """取工具版本首行（失败则回 'yes'）。"""
    out = run(cmd, "--version", timeout=4)
    return out.splitlines()[0].strip()[:40] if out else "yes"

# ① AI agent —— myainet 的头等能力：这台机能跑哪个 agent 干活
AGENTS = ["claude", "opencode", "codex", "aider", "gemini"]
agents_found = [f"{a}:{_version(a)}" for a in AGENTS if shutil.which(a)]
emit("agents", ",".join(agents_found))

# ② CLI 工具/运行时 —— SSH 命令可直接控
CLI_TOOLS = ["ollama", "docker", "python3", "node", "npm",
             "git", "uv", "go", "cargo", "java", "nvcc", "n8n"]
cli = []
for t in CLI_TOOLS:
    found = shutil.which(t) or (shutil.which("python") if t == "python3" and IS_WIN else None)
    if found:
        cli.append(t)
emit("cli", ",".join(cli))

# ③ GUI 应用 —— 要靠 computer-use / 图形自动化控，SSH 命令控不了
gui = []
if IS_MAC:
    for d in ("/Applications", os.path.expanduser("~/Applications")):
        if Path(d).is_dir():
            gui += [p.stem for p in Path(d).glob("*.app")]
elif IS_WIN:
    out = ps("(Get-StartApps | Select-Object -ExpandProperty Name) -join ','")
    gui = [a.strip() for a in out.split(",") if a.strip()]
elif IS_LIN:
    import glob as _glob
    for pat in ("/usr/share/applications/*.desktop",
                os.path.expanduser("~/.local/share/applications/*.desktop")):
        gui += [Path(f).stem for f in _glob.glob(pat)]
emit("gui", ",".join(sorted(set(gui))))

# ── 本地大模型（运行时无关：ollama / LM Studio / HuggingFace 缓存）──────────────
# 不写死 ollama：本地大模型可能由任何运行时拉下来。按"声称大小"≥1GB 纳入扫描；
# 没下完 / 稀疏的残包也照样扫出来，但实占磁盘远小于声称 → 标 ok=False（不可用）。
# 每条 = {"name":..,"ok":bool}。
def _local_models(min_bytes=1024 ** 3, cap=40):
    import re, json
    out, seen = [], set()

    def add(name, ok):
        name = (name or "").strip().replace(",", " ").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append({"name": name, "ok": bool(ok)})

    def measure(paths):
        apparent = actual = 0
        for p in paths:
            try:
                st = p.stat()
                apparent += st.st_size
                actual += st.st_blocks * 512   # 实占磁盘：稀疏/没下完会远小于声称大小
            except Exception:
                pass
        return apparent, actual

    # ① Ollama —— ollama list（ollama 管的都是完整模型；SIZE 列顺手滤掉小不点）
    if shutil.which("ollama"):
        for ln in run("ollama", "list", timeout=6).splitlines()[1:]:
            cols = ln.split()
            if not cols:
                continue
            m = re.search(r"([\d.]+)\s*(TB|GB|MB|KB)", ln, re.I)
            if m:
                v = float(m.group(1)); u = m.group(2).upper()
                gb = v * 1024 if u == "TB" else v if u == "GB" else v / 1024 if u == "MB" else v / 1048576
                if gb * 1024 ** 3 < min_bytes:
                    continue
            add(cols[0], True)
    # ② LM Studio —— GGUF 文件（声称≥1GB 才纳入；稀疏/残包 → 不可用）
    for base in (Path.home() / ".lmstudio" / "models", Path.home() / ".cache" / "lm-studio" / "models"):
        if base.is_dir():
            for pat in ("*/*/*.gguf", "*/*.gguf", "*.gguf"):
                for f in base.glob(pat):
                    ap, ac = measure([f])
                    if ap >= min_bytes:
                        add(f.stem, ac >= ap * 0.9)
    # ③ HuggingFace hub 缓存（transformers / vLLM / MLX / TGI 共用同一份）；
    #    只留 LLM —— 读 config.json 的 architectures，排掉 TTS/视觉/扩散/embedding。
    hub = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"
    if hub.is_dir():
        for d in hub.glob("models--*"):
            arch_ok = False
            for cfg in d.glob("snapshots/*/config.json"):
                try:
                    c = json.loads(cfg.read_text())
                    archs = " ".join(c.get("architectures") or []) + " " + str(c.get("model_type", ""))
                    arch_ok = bool(re.search(r"ForCausalLM|LMHeadModel", archs, re.I))
                except Exception:
                    arch_ok = False
                break
            if not arch_ok:
                continue
            blobs = d / "blobs"
            if not blobs.is_dir():
                continue
            files = [b for b in blobs.iterdir() if b.is_file()]
            ap, ac = measure(files)
            if ap >= min_bytes:
                incomplete = any(b.name.endswith(".incomplete") for b in files)
                add(d.name[len("models--"):].replace("--", "/"), ac >= ap * 0.9 and not incomplete)
    return out[:cap]


import json as _json
try:
    _models = _local_models()
except Exception:
    _models = []
emit("models", _json.dumps(_models, ensure_ascii=False))


# ── 远程唤醒（WoL）：有线网卡 MAC + 是否已武装 magic packet ──────────────────────
# 只采"能唤醒"的事实进卡；主控据此对离线节点标「可唤醒」、并经建网机发 WoL。
# WoL 只能唤睡眠/休眠、不能唤已断电；无有线网卡 / 未武装 → None（标不可唤醒）。
def _wake():
    import re
    try:
        if IS_WIN:
            out = (ps("$a=Get-NetAdapter -Physical -ErrorAction SilentlyContinue | "
                      "Where-Object {$_.Status -eq 'Up' -and $_.PhysicalMediaType -notmatch 'Wireless|802.11' "
                      "-and $_.InterfaceDescription -notmatch 'Virtual|Bluetooth|Loopback'} | Select-Object -First 1; "
                      "if($a){$w=(Get-NetAdapterPowerManagement -Name $a.Name -ErrorAction SilentlyContinue).WakeOnMagicPacket; "
                      "\"$($a.MacAddress)|$w\"}") or "").strip()
            if "|" in out:
                mac, w = out.split("|", 1)
                mac = mac.strip().replace("-", ":").upper()
                if re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
                    return {"mac": mac, "armed": "enabled" in w.lower()}
        elif IS_LIN:
            m = re.search(r"dev (\S+)", run("ip", "route", "get", "8.8.8.8") or "")
            if m:
                ifc = m.group(1)
                p = Path(f"/sys/class/net/{ifc}/address")
                mac = (p.read_text().strip() if p.exists() else "").upper()
                if re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac):
                    armed = bool(re.search(r"Wake-on:\s*[a-z]*g", run("ethtool", ifc) or ""))
                    return {"mac": mac, "armed": armed, "iface": ifc}
        # macOS：本项目 Mac 多为 WiFi 笔记本，WoL 不适用，从简不采
    except Exception:
        pass
    return None

try:
    _wk = _wake()
except Exception:
    _wk = None
emit("wake", _json.dumps(_wk, ensure_ascii=False) if _wk else "")

# ── 原生远程工作区（机器自报标记 ~/.myainet/workspace.json，若这台设过）──────────────
# 工作区是这台机的一项资源：自报进卡，全网（assemble/控制台/dispatch）才 read-don't-guess
# 「这台有工作区 + 它的 OS 契约」。原生 = 就用本机 OS/python/GPU/盘，无容器。无标记则报空。
# agent 永不猜 OS：卡里带 os/shell/work_dir/python，操作一律走 dispatch（按 os 出命令）。
def _win_ssh_shell() -> str:
    """探 OpenSSH 真实默认登录 shell —— dispatch 据此出 cd 语法，绝不假设。
    读 HKLM\\SOFTWARE\\OpenSSH\\DefaultShell：含 bash→bash（Git-bash）/ powershell|pwsh→powershell /
    否则 cmd（键缺失=OpenSSH 出厂默认就是 cmd）。这一格是自报实情、不是写死，否则契约自己撒谎。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OpenSSH") as k:
            val = (winreg.QueryValueEx(k, "DefaultShell")[0] or "").lower()
    except Exception:
        return "cmd"
    if "bash" in val:
        return "bash"
    if "powershell" in val or "pwsh" in val:
        return "powershell"
    return "cmd"


def _workspace():
    import json
    marker = Path.home() / ".myainet" / "workspace.json"
    if not marker.exists():
        return None
    try:
        m = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return None
    work_dir = m.get("work_dir")
    if not work_dir:
        return None
    has_gpu = bool(gpu_model and gpu_model.lower() != "none")
    # ── OS 契约：agent / dispatch 据此出命令，绝不猜 ──
    ws = {
        "kind":     "native",
        "os":       platform.system(),                  # Windows / Darwin / Linux
        "shell":    _win_ssh_shell() if IS_WIN else "bash",  # SSH 登录 shell：探出来的真值（cmd/powershell/Git-bash），dispatch 据此出 cd 语法
        "work_dir": work_dir,                            # 那台的真实路径（D:\… 或 ~/…）
        "python":   sys.executable,                      # 确切解释器，别赌 python/python3
        "gpu":      has_gpu,
    }
    # ── 能直接蹭的本机资源（原生就在本机 → localhost，无任何转发/绕路）──
    access = []
    if has_gpu:
        access.append({"name": gpu_model, "via": "本机 GPU 直连（原生 CUDA/Metal，无虚拟层）"})
    SVC_PORTS = {"ollama": 11434, "n8n": 5678}
    for t in cli:
        if t in SVC_PORTS:
            access.append({"name": t, "via": f"本机 localhost:{SVC_PORTS[t]}（原生直跑）"})
    ws["host_access"] = access
    # ── 状态快照（轻量，给大屏）：work_dir 所在盘用量 + GPU 占用，全跨平台/原生命令 ──
    state = {}
    try:
        du = shutil.disk_usage(work_dir)                 # 跨平台标准库，免 df/wmic 各搞一套
        state["disk"] = f"{round(du.used / 1073741824, 1)}/{round(du.total / 1073741824, 1)}GB"
    except Exception:
        pass
    if has_gpu:
        g = run("nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits", timeout=6)   # Win/Linux 有；Mac 无→空，跳过
        if g:
            p = [x.strip() for x in g.splitlines()[0].split(",")]
            if len(p) >= 3:
                state["gpu"] = f"{p[0]}% · {p[1]}/{p[2]}MB"
    if state:
        ws["state"] = state
    return ws


try:
    _ws = _workspace()
except Exception:
    _ws = None
emit("workspace", _json.dumps(_ws, ensure_ascii=False) if _ws else "")

# ── Tailscale IP ──────────────────────────────────────────────────────────────

tailscale_ip = ""
try:
    ts_cmd = shutil.which("tailscale")
    # macOS 官方 App 的 CLI 包装器通常在这里，Homebrew CLI 可能连不上 App。
    if not ts_cmd and IS_MAC and Path("/usr/local/bin/tailscale").exists():
        ts_cmd = "/usr/local/bin/tailscale"
    # Windows 官方安装器装在这里、且常不在 PATH → which 找不到，必须兜底
    # （否则建网机卡里 tailscale_ip 永远空、主控显示「未配置」，哪怕 Tailscale 真已上线）。
    if not ts_cmd and IS_WIN and Path(r"C:\Program Files\Tailscale\tailscale.exe").exists():
        ts_cmd = r"C:\Program Files\Tailscale\tailscale.exe"
    if ts_cmd:
        ts = run(ts_cmd, "ip", "-4", timeout=4)
        for line in ts.splitlines():
            line = line.strip()
            if line.startswith("100.") and line.count(".") == 3:
                tailscale_ip = line
                break
except Exception:
    pass

emit("tailscale_ip", tailscale_ip)

# ── 是否常驻（无电池 = 台式机/服务器）───────────────────────────────────────

is_always_on = "yes"
try:
    if IS_MAC:
        batt = run("pmset", "-g", "batt")
        if "InternalBattery" in batt or "Battery" in batt:
            is_always_on = "no"
    elif IS_WIN:
        count = ps("@(Get-WmiObject -Class Win32_Battery).Count").strip()
        if count not in ("", "0"):
            is_always_on = "no"
    else:
        bat_path = Path("/sys/class/power_supply")
        if bat_path.exists():
            for p in bat_path.iterdir():
                if p.name.startswith("BAT"):
                    is_always_on = "no"
                    break
except Exception:
    pass

emit("is_always_on", is_always_on)

# ── 网络可达性 ────────────────────────────────────────────────────────────────

ENDPOINTS = [
    ("net_reach_anthropic",    "https://api.anthropic.com"),
    ("net_reach_openai",       "https://api.openai.com"),
    ("net_reach_huggingface",  "https://huggingface.co"),
    ("net_reach_ollama_lib",   "https://ollama.com/library"),
]

for key, url in ENDPOINTS:
    try:
        urllib.request.urlopen(url, timeout=4)
        emit(key, "yes")
    except Exception:
        emit(key, "no")

# Ping 延迟（到 8.8.8.8）
ping_ms = ""
try:
    if IS_WIN:
        out = run("ping", "-n", "1", "-w", "2000", "8.8.8.8", timeout=5)
    elif IS_MAC:
        out = run("ping", "-c", "1", "-t", "2", "8.8.8.8", timeout=5)
    else:
        out = run("ping", "-c", "1", "-W", "2", "8.8.8.8", timeout=5)
    for line in out.splitlines():
        for marker in ("time=", "Time=", "时间="):
            if marker in line:
                raw = line.split(marker)[1].split()[0].lower().rstrip("ms").strip()
                ping_ms = raw
                break
except Exception:
    pass

emit("net_ping_ms", ping_ms)
emit("sysinfo_version", "3.0")
