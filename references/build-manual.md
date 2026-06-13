# 建网机：手动等价命令（排查参考）

> 正常**不用**看这个 —— 建网就跑 `setup_hub.py`（见 SKILL「建网路径 / 一键搭建」）。
> 下面是 setup_hub 内部各步的手动等价命令，**仅当它某步失败、要单独排查时**照着查。

### Step 1 确认机器条件

建网机要求：
- 能 24 小时常驻在线（服务器、NUC、旧电脑、树莓派等）
- 有固定局域网 IP（或能设置静态 IP）
- 推荐 RAM ≥ 4GB，存储 ≥ 20GB

询问用户：这台机器的操作系统是 macOS、Linux 还是 Windows？

### Step 2 起注册中心（`registry_server.py` — 零依赖，免装、无 Docker）

注册中心就是 skill 自带的一个 Python 脚本（sqlite + 标准库 RESP server），**三系统完全一样、什么都不用装**——
不装 Valkey/Redis、不碰 Docker/WSL。它监听 27182、说 RESP，`registry_client.py` 直连。起它就行：

**macOS / Linux**：
```bash
nohup python3 ~/myainet/scripts/registry_server.py > ~/registry.log 2>&1 &
```

**Windows**（后台、无窗口）：
```powershell
Start-Process pythonw -ArgumentList "C:\myainet\scripts\registry_server.py" -WindowStyle Hidden
```

验证（零依赖自测，**不需要 redis-cli**）：
```bash
python3 ~/myainet/scripts/registry_client.py 127.0.0.1   # 打印「✅ 裸 socket RESP 通了」即成
```

> **开机自启**：注册中心 + 大屏 + 巡检是建网机的 3 个常驻进程，用同一套机制随机器起 ——
> Linux 写 systemd 单元（`Restart=always`）、macOS 放 launchd plist、Windows 用「任务计划程序」开机触发。
> 漏起了也不慌：`healthcheck.py` 会指出哪个没通、给补起命令。
> 卡落在 `~/.myainet/registry.db`（sqlite，WAL），**进程崩了 / 重启不丢**。

安装完毕后获取建网机的局域网 IP：
```bash
# macOS
ipconfig getifaddr en0 || ipconfig getifaddr en1
# Linux
hostname -I | awk '{print $1}'
# Windows（PowerShell）
(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.*'}).IPAddress
```

### Step 3 安装 Tailscale（远程接入）

让主控在局域网外也能连进来。

> **Tailscale 装法看系统、不看角色** —— 建网机 / 主控 / 节点都用下面这套（按 OS 选），终态都是「全模式 + 系统服务 + 开机自起」。**绝不用 userspace 模式**（不建网卡、靠 SOCKS、没服务托管 → 漫游/重启就死，主控笔记本尤其会踩）。"轻"和"稳"在 Tailscale 这里对立，要稳就得全模式。

**macOS**（命令行装全模式、装成 launchd 系统服务，不用 App；想要菜单栏 GUI 可另装 App）：
```bash
brew install tailscale
sudo tailscaled install-system-daemon   # 装成开机自起的系统服务（全模式：有网卡 → tailnet 上能被直接 SSH 到）
sudo tailscale up                         # 打印登录 URL，展示给用户在浏览器授权一次
```

**Linux**：
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

**Windows**：
下载安装包：https://tailscale.com/download/windows
安装后系统托盘会出现 Tailscale 图标，点击 → Log in，在浏览器完成账号授权。
安装时勾选"Run at login"确保开机自启。

运行后会弹出一个链接，在浏览器中打开完成 Tailscale 账号授权（免费账号，注册一次即可）。

授权完成后获取 Tailscale IP：
```bash
# macOS / Linux
tailscale ip -4
# Windows（PowerShell）
tailscale ip -4
# 或从托盘图标右键 → This device 查看
```

#### 默认修正代理绕过规则

凡是安装 Tailscale 的机器，都必须把 Tailscale 网段加入系统代理绕过规则，避免 Chrome/Safari/Edge 把 `100.x` tailnet 地址送进 HTTP/SOCKS 代理导致 `502 Bad Gateway`。

安装或登录 Tailscale 后运行：

```bash
python3 scripts/tailscale_proxy_bypass.py
```

该脚本会保留现有绕过规则，并幂等追加。macOS 追加：

```text
100.64.0.0/10
100.*
*.ts.net
```

Windows 追加：

```text
100.*
*.ts.net
```

如果用户使用 Clash/Surge/Quantumult X 等代理软件，并且软件会覆盖系统代理设置，还需要在代理软件自己的规则里加入 `100.64.0.0/10 DIRECT`。

### Step 4 开启 SSH 服务

**一键（推荐，三平台通用、幂等、已开则跳过）**：
```bash
python3 scripts/enable_ssh.py   # 自动识别系统并开启 SSH 服务；需管理员（会提示输一次密码）
```

**SSH 开好后配钥匙**（建网机生成/发布自己公钥 + 装主控公钥进自己门，供主控免密跳板进来）：
```bash
python3 scripts/keysync.py --registry-host 127.0.0.1 --role hub
```

以下为脚本内部做的手动等价命令（供排查）：

**macOS**：系统设置 → 通用 → 共享 → 开启「远程登录」
或命令行：`sudo systemsetup -setremotelogin on`

**Linux**：
```bash
sudo apt install -y openssh-server
sudo systemctl enable --now ssh
```

**Windows**（PowerShell，以管理员身份运行）：
```powershell
# 安装内置 OpenSSH Server
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0

# 启动服务并设为自启
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

# 开放防火墙（如未自动添加）
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22

# 验证
Get-Service sshd  # Status 应为 Running
```

Windows 默认 SSH 登录用 Windows 账户名和密码，免密配置同 Linux（`ssh-copy-id` 或手动写 `~/.ssh/authorized_keys`）。

### Step 5 输出建网机信息卡

```
╔══════════════════════════════════════════════════════╗
║          myainet 建网机信息                          ║
╠══════════════════════════════════════════════════════╣
║  局域网 IP    : <LAN_IP>                             ║
║  Tailscale IP : <TS_IP>                              ║
║  注册中心       : <LAN_IP>:27182  ✅                    ║
║  SSH          : ssh <user>@<LAN_IP>                  ║
╠══════════════════════════════════════════════════════╣
║  节点注册命令（在每台节点上运行）：                  ║
║  python3 register_node.py \                          ║
║    --registry-host <LAN_IP>                            ║
╠══════════════════════════════════════════════════════╣
║  主控连接命令：                                      ║
║  ssh <user>@<TS_IP>   （Tailscale 远程）             ║
║  ssh <user>@<LAN_IP>  （局域网内直连）               ║
╚══════════════════════════════════════════════════════╝
```

**建网机自身也需要注册**（注册中心在本机，连 127.0.0.1 即可）：

```bash
python3 ~/myainet/scripts/register_node.py --registry-host 127.0.0.1
```

注册成功后 Dashboard 里就能看到建网机自身的节点卡片。

告诉用户：建网机已就绪。接下来在每台节点上运行此 skill，选择「节点」路径完成注册。

### Step 6 启动 Dashboard（可选）

在建网机上启动可视化仪表盘，任意浏览器（含 iPad）均可访问：

```bash
python3 ~/myainet/scripts/dashboard.py --registry-host 127.0.0.1
```

启动后自动选取可用端口（7700–7799），并输出访问地址，例如：

```
╔══════════════════════════════════════════╗
║       myainet Dashboard Server           ║
╠══════════════════════════════════════════╣
║  注册中心  : 127.0.0.1:27182               ║
║  Port    : 7700                          ║
╠══════════════════════════════════════════╣
║  访问地址：                              ║
║    http://localhost:7700                 ║
║    http://192.168.1.10:7700             ║
╚══════════════════════════════════════════╝
```

iPad 或任何局域网设备直接打开 `http://<建网机局域网IP>:<端口>` 即可。
仪表盘每 30 秒自动刷新，展示：在线节点数、活跃任务、能力矩阵、任务进度。

**后台持久运行**（不随终端关闭）：

macOS / Linux：
```bash
nohup python3 ~/myainet/scripts/dashboard.py --registry-host 127.0.0.1 > ~/dashboard.log 2>&1 &
echo "Dashboard PID: $!"
```

Windows（PowerShell）：
```powershell
Start-Process pythonw -ArgumentList "C:\myainet\scripts\dashboard.py --registry-host 127.0.0.1" -WindowStyle Hidden
# 或用 Task Scheduler 设置开机自启
```

---
