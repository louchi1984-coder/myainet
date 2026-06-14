---
name: myainet
description: >
  myainet 个人 AI 网络搭建工具。当用户想了解某台机器在 AI 时代能承担什么任务、
  给机器打节点名片、或者把多台机器组成 myainet 个人 AI 网络时，必须使用此 skill。
  触发场景：「这台机器能跑本地大模型吗」「帮我分析这台服务器能干啥」
  「给我的机器生成节点名片」「这台机器适合装 Claude Code / OpenCode 吗」
  「我想让多台设备协同工作，帮我组网」「扫描所有节点，生成网络配置」。
  支持三条路径：建网路径（搭建注册中心+远程接入）、主控路径（查看全网并生成调度配置）、节点路径（扫描并注册这台机器）。
---

# myainet — 个人 AI 网络搭建工具

> **语言铁律：本文档是中文，但【面向用户的交互】要用用户的语言。** 问角色 / 确认操作 / 报状态 / 给建议时，跟着用户的语言走——看用户用什么语言跟你说话，拿不准就按系统 locale（`echo $LANG` / Windows `Get-Culture`）。脚本输出是中文不要紧（你读得懂、转述时用用户的语言）；**别因为文档是中文就默认用中文跟英文用户对话**。

把多台机器组成 myainet 个人 AI 网络——各司其职，任意 AI 工具加载网络配置后即可统一调度：

```
任意 AI 工具（加载 myainet 网络配置后）按任务匹配 4 类能力节点：
  ├─→ 推理 / 生图 / 训练（吃显存）       →  🖥️ 本地算力(GPU) 节点（按 VRAM 分档）
  ├─→ 编程 / 云端推理（装 agent+API）    →  ☁️ 云端 AI 节点（不吃本地硬件）
  ├─→ 脚本 / 定时 / 测试 / 轻服务        →  ⚙️ 通用·自动化 节点
  └─→ 存模型 / 数据                      →  💾 存储 节点（盘大）
```

## 网络架构

```
[主控] ──Tailscale──→ [建网机] ──LAN SSH──→ [节点 A]
                          │                 ──→ [节点 B]
                          │                 ──→ [节点 C]
                       注册中心 registry_server.py（27182）
```

**三种角色**：

| 角色 | 必装工具 | 说明 |
|------|---------|------|
| 🧱 **建网** | 注册中心(`registry_server.py`) + SSH服务 + Tailscale | 不关机的机器，网络的地基；主控通过 Tailscale 连它，它再跳转到节点。注册中心是自带 python 脚本，**免装、无 Docker** |
| 🖥️ **主控** | Tailscale | 你日常用的电脑/手机；通过 Tailscale 连建网机，再经 SSH 跳转控制节点 |
| ⚙️ **节点** | SSH服务 | 局域网内的工作机器，只需开 SSH；不需要 Tailscale，不需要注册中心 |

SSH 跳转命令：`ssh -J user@建网IP user@节点IP "命令"` —— 这是**底层够到节点**的方式；**执行任务走 `dispatch`（它底层就用这条跳转 + 记账上大屏）、别直接拿它跑活儿**（见〈控制模式〉铁律）。

---

## 前置：脚本路径约定（先看这条，下文所有命令都依赖它）

下文示例里的 **`~/myainet/scripts/...` 是简写，指「本 skill 包内的 `scripts/` 目录」**（和这份 SKILL.md 同级）。skill 实际装在哪个 agent 的 skills 目录，就用那个真实路径，例如：

- opencode：`~/.config/opencode/skills/myainet/scripts/`
- Claude Code：`~/.claude/skills/myainet/skills/myainet/scripts/`（以实际加载位置为准）
- 手动 clone：clone 到哪就是哪

**别假设 `~/myainet` 真实存在**——跑命令前把前缀替换成本 skill 的真实位置（你加载本文件时就知道它在哪）。机器上若残留旧拷贝，以 skill 真身为准，别跑到旧目录里执行过期脚本。

## 前置：确保本机有 Python（agent 自动处理，别假设它存在）

本 skill 的脚本是 Python，且**零依赖**（只用标准库 + 内置的 `registry_client.py`，无需 `pip install` 任何东西，也无需 `redis-cli`）。所以只要有个 Python 解释器就能全跑。

运行任何脚本前，**agent 先确认本机有 Python；没有就自动装好再继续**，不要假设它存在（很多 Windows 机器就没有 Python）：

```bash
# 一条命令把所有写法试全——任一行打印出版本号，就是「有 Python」，直接往下用那条。
# ⚠️ Windows 上 python3 通常不存在（解释器叫 python / py）。别一看 python3 报错就判「没有 Python」，
#    必须把下面四种都试过、全失败才算真没有。
python --version || python3 --version || py -3 --version || py --version
```

没有就装（**层层兜底、不堵死**，agent 自行选当前可用的一条）：

- **Windows**：`winget install -e --id Python.Python.3.12`；winget 不可用 → 下载 python.org 安装器静默安装；再不行 → 解压官方 **embeddable zip** 到 myainet 目录直接用（免管理员、不污染系统）。
- **macOS**：`xcode-select --install`（装好即带 python3），或 `brew install python`。
- **Linux**：`sudo apt install -y python3`（或 `dnf` / `pacman` / `zypper` 对应包）。
- 全失败 → 给用户**明确的手动安装指引**，不要静默卡住。

装好后用找到的那条 Python 跑后续脚本。

> **上面这条「现场找 Python」只用于给*本机*做首次注册（本机还没注册卡）。**
> 已注册的机器——包括你要远程跑脚本的任何节点 / 建网机——它的 Python 解释器**就写在自己 `node:*` 卡的 `python` 字段里**（注册时 `sysinfo` 自报的真实解释器，Windows 上就是 `python` 而非 `python3` 的真路径）。
> **要在某台机器上跑脚本，先读它卡里的 `python`，用那条去跑——别现场试、别猜 `python3`/`python`。** 这跟「读注册中心、不扫机器」是同一条原则：机器装了什么、用什么解释器，都由它自己声明进注册中心，你只读不猜。

## 第一步：自动检测状态（机器级身份，不绑目录）

加载就跑这一句——它读机器级身份标记 `~/.myainet/identity.json` ＋ 查本机注册中心(27182)在不在，打印 facts：

```bash
python3 ~/myainet/scripts/identity.py
```

按打印的 `role` 决定走哪条。**身份是机器级的，换任何目录都认得**；能力 / 职责从身份推导，不另存权限位：

> **建网机和主控都能控制全网**（控制能力共享）；唯一区别是**建网机扛"监听 + 写大屏"那份常驻 infra**（注册中心 + 巡检 + 大屏），主控不扛。所以**没有"升主控"这回事**——建网机本就支持全部主控命令。

**① `role=建网机`**（本机注册中心(27182)在，或标记写了建网机）→ **先核验真建完没，再进控制台。** ⚠️ 光有注册中心在跑 ≠ 建网机建好了 —— 可能是残留进程，或上次只建了一半（缺 Tailscale / SSH / 大屏）。所以**用 `--verify` 逐项核，别想当然进控制台**：

```bash
python3 ~/myainet/scripts/setup_hub.py --verify     # 逐项核：注册中心 / 大屏 / 巡检 / SSH / Tailscale
```
- `✅ … 全部就位` → 进**控制模式**（见下）。
- `❌ 没建完，还差：X`（退出码非 0）→ **先补建**：跑 `python3 ~/myainet/scripts/setup_hub.py`（幂等，只补缺的、不重起已在跑的），补到 ✅ 再进控制模式。重启后大屏 / 巡检没自启也走这条。

**② `role=主控`** → 它也控制全网、但不扛 infra。连标记里的 `central`（中央建网机）→ 进**控制模式**，拿到和建网机一模一样的控制台与命令。
> **新机器要配成主控**（还没身份标记 / 是台干净机器）→ 跑确定性脚本，别手敲：`python3 ~/myainet/scripts/setup_control.py --central <建网机地址>`（同 LAN 填它 lan_ip，异地填它 Tailscale IP）。它按顺序做完「Tailscale → 写身份(主控,central=建网机) → SSH → 注册自己 → 存本地镜像 → 自检」，每步自验、`--verify` 可只核不动手。**主控的 central 必须是建网机地址、绝不能自指**（填 127.0.0.1 会让裸加载 skill 查不到注册中心、误判「注册表空」——脚本会当场拒）。

**③ `role=次建网机`**（建网机的精简版：本地注册中心 + 同步给主，不起大屏/巡检）→ 核验/补起：`python3 ~/myainet/scripts/setup_hub.py --main <central> --verify`，没建完就去掉 `--verify` 跑一遍补上。它是本 LAN 的 infra（数据随同步汇进主的大屏）；要看全网 / 派活去主或主控。

**④ `role=节点`** → 已认得自己（标记里有名 + 归属 + central），**不必再问角色**；要手动刷新就 `register_node.py --registry-host <central>`。平时它被动——建网机的巡检会定期重注册它、推它的状态。

**⑤ `role=（未知-新机器）`**（没标记、本机也没注册中心）→ 新机器，问角色、走对应路径，**路径跑完写下身份标记** `identity.py --set --role <角色> --central <中央地址>`，以后加载就直接认得：
  1. **建网** — 网络地基（24h 不关机）：注册中心 + 远程接入 + 写大屏
  2. **主控** — 你日常用的电脑：控制全网（借建网机注册中心 + 持本地镜像，抗掉线）
  3. **节点** — 加入网络成为工作节点（被控）

  > **老主控迁移**：若 `role=未知` 但当前目录有 `myainet-network-config.md`——这是标记机制之前建的老主控，当主控处理，并顺手补写标记 `identity.py --set --role 主控 --central <配置里的建网机地址>`。

---

### 控制模式：进来先给什么（没明确指令 → 实况 + 三条）

如果建网机上已启动 Dashboard，提示用户直接打开浏览器查看实时状态（地址从上次启动日志获取，或提示运行 `python3 ~/myainet/scripts/dashboard.py`）。

**没有明确指令时——一句实况 + 四条:**

读注册中心实时（谁在线/离线）+ 各卡 `problems`（闲置硬件 / 缺工具 / 盘满 / 持续离线），概括成**一句人话**，后面跟四条:

```
网里 2 台在线、nas 离线两天、desktop 那张 3060 闲着。要不要——
  ① 大屏    ② 评估    ③ 任务    ④ 其他
```

- **① 大屏** — 给 / 帮开 dashboard 地址（浏览器、手机都行；主控在 Tailscale/LAN 上直接开）。
- **② 评估** — 逐台**客观看这台硬件能干啥**，不给建议、不分配角色、不指点"哪里浪费"（那是 ④ 优化的事）。**只报能力 + 客观状态**：GPU+VRAM→能跑多大本地推理 / 能否生图·音频·数字人 / 能否训练；盘(读全盘)→能存多少、够不够当存储·工作区·向量库；CPU+常驻+runtime→能否自动化·轻服务；装了啥 agent→能否云端编程。客观状态(在线 / 盘空 / 已装模型)一并列。**不用 ✅/打勾矩阵；只说"能"、不说"该"**：

```
🧱 win-desktop   建网机  ✅在线  Win11·i5·RTX3060 12G·16G·盘475G(空216)
     GPU 12G → 本地推理(≈14B)/生图/轻训练 能；已装 ollama+模型 gemma4:12b·qwen3.5:9b；agent claude/codex → 也能云端编程
💻 mac-laptop    主控    ✅在线  M1·8G·盘228G(空33)
     M1 Metal 但 8G → 只够小模型；agent claude/codex → 云端编程；盘空 33G
🖥️ gpu-2         节点    ✅在线  Win10·i7·RTX2070 8G·盘2.3T(空2.25T·E:1.86T)
     GPU 8G → 本地推理(7B/14B-Q4)/生图·音频·数字人 能；E:1.86T 大盘 → 能当存储/工作区/向量库
```

（这是"能干啥"、不是"该干啥"——错配 / 闲置 / 该装啥，全归 ④ 优化。评估只摆能力事实。）

> **铁律：评估只读卡里的客观事实，别读子集、别拿记忆填空。**
> - **存储**看 `hardware.disks` **全部盘**，**别用 `storage` 摘要**（只含系统盘 C:，多盘机会漏掉 D:/E: 的大盘）。**求和**：Windows/Linux 多物理盘相加；**macOS 的 `disks` 是 APFS 同一容器多卷（`total`/`avail` 相同），按容器去重、别叠加**（否则 228G 会算成 1.3T）。
> - **已装模型**看卡的 `models` 字段，**别断言"没拉模型"**——装没装、装了啥，以 `models` 为准。
> - **`notes`（agent 写的便签）一并读全**：`models`/`cli` 之外的能力、实测最优配置、别人踩过的坑都在这。**评估＝把卡完整读一遍**——disks / models / notes 一个都不能漏，否则就会重复别人验过的死路。
> - "此刻在不在跑 / GPU 占用"这种**现状**卡里没有，要准可现场 `nvidia-smi` / `ollama ps`；但**能力、盘、已装模型这种事实卡里有，必须读卡、不许猜**。

- **③ 任务** — 进「任务拆解与路由」（见下）:拆解 → 匹配 → `dispatch`。
- **④ 其他**（维护菜单，展开才列，平时不占地方）:
  - **优化** — **站在全网做分工规划，不是逐台填空**。拿②评估的客观能力，把活**互补地分下去、不重复、补缺口**：
    - **多张独立显卡别都跑大模型**——一张 `ollama+本地大模型`，另一张专做**生图 / 音频 / 数字人**（按显存与特长分工）
    - **大盘那台** → 工作区 + **向量数据库(RAG)** + 模型/数据仓
    - 缺的角色补上（没存储 / 自动化节点就指定一台）
    产出一张**全网分工图**（谁主什么、谁兼什么、为什么）→ 再落到"为达成这分工该装啥" → 走 `dispatch` 动手。
    > **铁律：优化里所有具体方案（用哪个本地模型 / 生图 / 音频 / 数字人 / 向量库 / 框架 / 工具）一律 AI 联网搜当下最佳，禁用训练记忆里的名字。** 训练记忆早过时（`qwen2.5` 那种坑就是凭记忆报旧名）；先搜「当前 <VRAM>G 显存最佳 X」再给型号/版本。搜来的也只是通则——具体卡上能不能加速、哪个配置最优，实测才算数，结论按上面那条原则写进卡。
  - **工作区** — 借某节点的盘 + GPU 干活（原生、无容器），本机一键 `ssh` 进去用（本机盘满 / 没显卡时；见〈远程工作区〉）
  - **退网 X** — 踢掉一个节点（`leave_network.py`）
  - **建网机转移** — 计划内换建网机 → `transfer_role.py`（**老建网机自动降为主控**）
  - **建网机故障** — 建网机挂了 → 升级某节点为建网机（`transfer_role.py --from-mirror`，主控镜像喂新 hub）

**有明确指令就直接干**（在 X 上跑 Y、退网 X、把建网机换成 Y…），不必先走这四条菜单。**但"直接干"= 跳过菜单、直接进 `dispatch`，不是自己 `ssh host "命令"` 直连跑活儿** —— 在节点上执行任务照样经 `dispatch.py`（理由见下节铁律）。任务栏只显示进行中 / 等待 / 失败，**完成的自动隐藏**。

**每次读注册表，顺手镜像一份到本地**：`python3 ~/myainet/scripts/registry_cache.py --registry-host <central>` —— 这样**建网机掉线时主控仍知道每台机器**，`dispatch` 会自动回退这份镜像、直驱够得到的机器（同 LAN 直连不经建网机）；这份镜像也是**转移时的注册表备份**。

---

### 控制模式：任务拆解与路由

用户下达任务时，skill 执行以下逻辑（**程序负责执行，AI 负责拆解和路由**）：

> **铁律：在节点上跑任何活儿 = 经 `dispatch.py`，绝不自己 `ssh host "命令"` 直连执行。**
> 只有 dispatch 会把 `task:<id>` 记账（running→done/failed + 退出码 + 输出尾部）→ 上大屏、被巡检盯死活、可追溯。**你自己 ssh 直连跑 = 任务隐形**：大屏看不见、崩了没人知道、巡检也兜不住。哪怕只有一条命令、哪怕觉得 dispatch 麻烦，也走它。
> （只读探活例外：读卡 / `healthcheck` 这类不改状态的可以直连；**一旦是"执行命令 / 装东西 / 起服务 / 训练 / 改文件"——必经 dispatch**。够不到的远端节点也不例外：`ssh 它的建网机` 后在那台上跑 dispatch，不是自己穿过去直连。）
>
> **铁律：判节点死活 = `python3 ~/myainet/scripts/dispatch.py --node <名> --check`，绝不自己跑 `ping`。** ping 走 ICMP，**Windows 节点防火墙默认就拦 ICMP**——ping 100% 丢包是常态，跟机器死活无关。只凭 ping 判死活会把活得好好的机器误判成「硬 down」，进而触发无谓的「恢复/转移」（真实事故反复发生）。`--check` 走的是你真正控制它的那条 SSH 路（LAN→Tailscale→建网机跳板），连上=活、连不上才是真够不到。**看到自己想敲 `ping <节点>` 判在线，就停手，改 `--check`。**

> **配套——改了机器状态就 `report` 一笔：** 你（或你派的 agent）装/删软件、换版本、回滚、"试了又退"这类**会改变机器状态**的动作，边做边报一句：
> `python3 ~/myainet/scripts/report.py --registry-host <主IP> --node <节点> "在装新 ollama / 不行已回退"`（拿不准 / 出岔子加 `--warn`）。
> **dispatch 记"跑了啥命令"、report 记"改了啥 / 好不好"** —— 两个一起，网络才不会被"另一个任务偷偷改了机器又退"那种事搞失控。
>
> **一条原则：把有价值的经验写到卡上。** 装了 / 调出 / 试出对别的任务有用的事实——装了什么 `sysinfo` 采不到的能力（TTS / 生图 / 数字人 / 向量库）、什么配置实测最优、哪个方向是死路——就 `report.py --node <节点> --card "…"` 写进那台卡的 `notes`。**什么算"有价值"你判断。** 写进去 patrol 刷卡也冲不掉，下个任务读卡即知、不重复试错。
> 例：`--card "装了 IndexTTS2(TTS)；实测此卡 CPU 比 GPU 快 3-5x，别再试 GPU 加速"`。

**Step 1 拆解任务**

将用户的任务拆成子步骤，识别每步需要的能力类型。例如：

用户说："帮我跑完整的数据科学流程：下载数据集、清洗、训练、部署 API"

拆解为：
```
子任务 #1  下载数据集       → 需要：存储 + 网络
子任务 #2  数据清洗         → 需要：自动化（Python）
子任务 #3  训练模型         → 需要：推理/训练（GPU）
子任务 #4  部署推理 API     → 需要：项目部署
```

**Step 2 能力匹配**

对照各节点的能力（看具体事实：硬件 + 装了啥 + `problems`），为每个子任务分配节点：
```
#1 下载数据集   →  nas-box      (存储节点，磁盘最大)
#2 数据清洗     →  nuc-server   (自动化节点，Python 环境)
#3 训练模型     →  gpu-rig      (推理/训练节点，VRAM 最高)
#4 部署 API     →  macbook      (项目部署节点，SSD + 高 CPU)
```

如果某个子任务没有合适节点，明确告知用户并给出解决建议（安装工具/扩容）。

**Step 3 展示执行计划，等待用户确认**

```
📋 执行计划（共 4 步）
  #1  nas-box     下载数据集到 /data/dataset/
  #2  nuc-server  运行清洗脚本 clean.py
  #3  gpu-rig     启动训练，预计 2–4 小时
  #4  macbook     部署 FastAPI 服务，端口 8000

顺序执行，#1 完成后继续 #2。确认执行？
```

**Step 4 执行 & 实时更新任务状态 —— 用 `dispatch.py` 派每个子任务**

确认后，逐个子任务交给 `dispatch.py`（它负责 SSH 执行 + 写 `task:*` + 回显；控制台第③块就读这些）：

```bash
python3 ~/myainet/scripts/dispatch.py --registry-host <主IP> --node <节点> --name <任务名> "<命令>"
```

**那条 `<命令>` 怎么定（按事实卡判断：直接 shell，还是委托给那台的 agent）：**
- 先看节点 `hardware.os` → 出对的命令（mac `brew` / win `winget` / linux `apt`·`curl`）；
- **任务模糊、要随机应变（装软件、修环境、"把 X 搞定"）且节点装了 agent（看卡的 `agents`）→ 委托**：命令就写成那个 agent 的非交互调用，把目标甩给它本地搞定 —— `"claude -p '下载并装好 ollama'"` / `"codex exec '...'"`；
- 命令明确、或节点没 agent（纯 SSH 哑节点）→ 自己写死那条 shell 命令；
- 长任务（训练 / 大下载）加 `--detach` —— 甩后台、自动交给巡检盯死活；
- 命令**整体加引号**（跟 `ssh host "cmd"` 一个规矩，免得 `-h`/`-c` 被当选项、引号被拆）。

**够不到节点就穿它的归属建网机**（节点在别的 LAN，或你在外面够不到家里）：看节点卡的 `belongs_to` 字段——**别直连，而是 `ssh 那台建网机`、在它上面跑 dispatch**（本地 LAN 节点的建网机=主建网机；别的 LAN=那台次）。在建网机上跑时按 **它卡里的 `os`** 出命令：posix `python3 ~/myainet/scripts/dispatch.py …`，Windows `python C:\myainet\scripts\dispatch.py …`。够得到就直连、别多绕。统一模型 **主控 → 节点的建网机 → 节点**，没有"外地"特殊——`belongs_to` 就是这把通用路由键。

`dispatch` 把 `task:<id>` 从 `running` 更新到 `done` / `failed`（带退出码 + 输出尾部），失败如实报错，控制台任务栏实时反映。**判断（挑哪台 / 直连还是穿 hub / shell 还是委托 agent）在 AI，dispatch 只是手。**

---

### 控制模式：支持的快捷指令

| 用户说 | skill 动作 |
|--------|-----------|
| "刷新" / "状态" | 重新给一遍实况 + 逐台具体（不是打勾矩阵） |
| "检查在线" | 读大屏/注册中心的 status；要逐台确证用 `dispatch --node <名> --check`（经 SSH 实连）。**绝不自己 ping**——ICMP 常被防火墙拦，会把活机器误判成离线 |
| "评估" / "优化网络" / "现在该装什么" | 读三层事实（硬件/环境/网速）+ 各卡 `problems` → 逐台具体 + 建议（闲置硬件 / 缺工具 / 错配 / 瓶颈；AI 能力分本地/云端两看）= 开场②那条 |
| "在 X 上跑 `命令`" / "在 X 上装 Y" / 派任务 | 任务拆解 → `dispatch.py --node X "命令"`（模糊任务委托给 X 的 agent，见上节）= 开场③ |
| "退网 X" / "X 不要了" | `leave_network.py --node X`（删卡 + 退 Tailscale；`--purge` 连软件卸；`--dry-run` 预览） |
| "把建网机换成 Y" / "建网机挂了换 Y" | **on-LAN**：Y 先按建网路径装好(注册中心+Tailscale+绑 0.0.0.0) → `transfer_role.py --old-host 主 --new-host Y`（老 hub 已死加 `--from-mirror`）→ 照清单收尾；**老建网机自动降为主控**（不是节点） |
| "在 X 上给我开个工作区" / "借 X 的盘当工作区" | 〈远程工作区〉：`setup_workspace.py` 在 X 最空的盘建 work_dir + 自报 OS 契约进卡（**原生、无容器、无 Docker**）；派活走 `dispatch --workspace`、进入 `ssh X` 后 cd（见专节） |

**巡检提醒（主动但不唠叨）：** 展示状态时若发现——某节点 `last_seen` 已很久没刷新（**持续**离线，不是偶尔一次 ping 不到），或整台建网机连不上——主动提一句并给出口：「X 已 N 天没上线，要**退网**还是等它回来？」「主建网机连不上，要不要把职能**转移**到 Y？」（**先分清软 / 硬 down，且必须用 `dispatch --node <名> --check` 实连判定，绝不只凭 ping**：机器 / Tailscale / SSH 还在、只是服务没起 = **软 down**，远程 SSH 进去 `healthcheck` 就救回；整台关机 / 断网 = **硬 down**，节点不在 Tailscale 上、外面够不到，只能**回 LAN** 把建网机转移给一台节点。⚠️ **`--check` 显示 ✅ 就是活的，哪怕 ping 100% 丢包**——Windows 默认拦 ICMP，ping 丢包绝不等于硬 down。下「硬 down」结论前，`--check` 必须也连不上。）**只在持续信号下提、一次说清，别反复唠叨；** 偶发抖动、短暂离线不提。

---

## 建网路径：搭建网络地基

> **建网就跑 `setup_hub.py` 这一个确定性脚本 —— 别手动逐步拼命令（会漏步、会写错 OS 命令）。** 它按顺序做完「注册中心 → 身份 → SSH → 大屏+巡检 → 注册本机 → Tailscale」并**每步自检、结尾如实报告**；幂等，可反复跑。

### 一键搭建（主路径，照这个做）

**先**：确认机器条件（能 24h 常驻、有固定 LAN IP 或可设静态、RAM≥4G、存储≥20G），**问用户操作系统**（命令按 OS 选）。

**跑 setup_hub**：

macOS / Linux：
```bash
python3 ~/myainet/scripts/setup_hub.py
```

Windows（先把控制台切 UTF-8，否则中文/emoji 会显示乱码；**两条按你实际的 shell 选** —— `$env:`/`[Console]::` 是 PowerShell 语法，cmd 不认）：

PowerShell：
```powershell
chcp 65001 > $null; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; python $env:USERPROFILE\myainet\scripts\setup_hub.py
```

cmd：
```cmd
chcp 65001 >nul && python %USERPROFILE%\myainet\scripts\setup_hub.py
```

**它依次做**：① 起注册中心(27182) ② 写身份 ③ 开 SSH（弹一次管理员授权）④ 起大屏+巡检（已在跑则跳过）⑤ 注册本机 ⑥ 装 Tailscale + `tailscale up`（**下载安装它替你做，只有「浏览器登你自己账号」要你来**）⑦ 自检。

**看 ⑦ 的结论办事，别替它宣布成功**：
- `✅ 建网机建成：… 全部就位` → 成了，做下面两个收尾。
- `❌ 没建完，还差：X`（退出码非 0）→ **按缺项补**：最常见是 Tailscale 没登录（跑 `tailscale up` 浏览器授权）、SSH 没开。补完 `setup_hub.py --verify` 复验到 ✅ 为止。**没到 ✅ 别说"建好了"。**

**一个收尾（setup_hub 不做，手动跑一次）**：**Tailscale 代理绕过**（防 `100.x` 被代理吞成 502）：`python3 ~/myainet/scripts/tailscale_proxy_bypass.py`（用 Clash/Surge 还要在其规则里加 `100.64.0.0/10 DIRECT`）。
> 配钥匙不用单独跑 —— setup_hub 的注册步骤已**自动发布 + 安装公钥**（含 Windows 管理员账号的 `administrators_authorized_keys`，自动设 ACL）。

完事**告诉用户建网机已就绪**，给连接信息（LAN IP / Tailscale IP / `ssh 用户@IP`）；接下来每台节点上跑此 skill 选「节点」—— **节点零输入、广播自动发现这台建网机，不用给 IP**。

---

> **setup_hub 各步的手动等价命令（仅它某步失败时照着排查，正常别手动跑）→ 见 `references/build-manual.md`。**

## 多局域网（可选：主建网机 / 次建网机）

**只有一个局域网（机器都在一处）→ 跳过本节**，按上面装一台建网机即可。

机器分散在**多个局域网**（家 + 公司 + …，各自一层 NAT、互相 LAN 够不到）时：**每个局域网各放一台建网机**，靠 Tailscale 连成 mesh。

- **主建网机**（全网一台）：标准建网流程（注册中心 + 大屏 + Tailscale + SSH）。跑**唯一的大屏**、汇总全网、主控连它。
- **次建网机**（每多一个局域网一台）= **建网机的精简版**：跟主一样起本地注册中心 + SSH + Tailscale，**但不起大屏**，并且**把本地注册中心同步给主**。装法就一条：

```bash
# 在次建网机上跑（--main 填主的 Tailscale 地址，或 'auto' 让它在 tailnet 上自己探主）
python3 ~/myainet/scripts/setup_hub.py --main <主的Tailscale地址>
```

它就是 setup_hub 的次模式：起 `registry_server --main-host <主>`（本地注册中心 + 一根同步线）+ 开 SSH + 装 Tailscale，不起大屏/巡检。

> **填地址、少用 `auto`**：全网注册中心都在 27182，主和次在网络上应答一模一样，`auto`（在 tailnet 上探谁是主）只认"第一个在 27182 应答、不是自己的机器"——**只有一个主 + 这一台次时才稳**；有**多台次**时 auto 可能把另一台次当成主，所以多台次**老实填主的 Tailscale 地址**。

**关键：次只是"缩水的主"，不是另一套机制 ——**
- **节点零区分** —— 本地节点照样**广播找本地 hub、注册**（主还是次，节点根本不知道），`register_node` 一字不改、不碰 Tailscale；
- **同步替代了旧的"桥 + 推送"** —— 次的本地注册中心一有写入（node/pubkey/status）就**整批同步给主**；并把**主的控制方公钥拉下来**给本地节点装（外地节点照样装上主控钥匙、被主控免密够到）。主拿到全网并集、跑那块唯一大屏；
- **派活走 `ssh 次`** —— 主控要使唤外地节点，就 `ssh 次` 再在次上 `dispatch` 本地派（次连着它那个 LAN）。想让主控 AI 自动选对次，注册时给节点加 `--belongs-to <次名>`（路由键；不加也行，手动指定即可）；
- **本地自成一体** —— 主 / Tailscale 临时断了，本地网照常跑、缓着，通了再同步追上。

**跨 LAN 执行：** 主控要在外地节点上跑命令时，不自己穿过去，而是 **`ssh 次` 一跳、在次上跑 `dispatch.py --node <本LAN节点> "命令"`**（次本就连着它那个 LAN 的节点）—— 用现成 dispatch，不用两级跳。

> **外地节点的在线状态**：主 ping 不到别的 LAN 的内网 IP、也收不到实时探活（次精简版不跑 patrol）。所以同步上来的外地节点，主大屏标 **「已注册（远程·未探活）」**（拓扑上是琥珀色虚线节点），**不误判离线、也不计入离线数** —— 次同步时给卡盖了 `synced_from` 来源戳，大屏据此把"远程探不到"和"本地真死了"分开。要更实时可在次上加轻量探活，但默认从简。

---

## 监控：巡检（`patrol.py`）+ 进程盯守（`watch_job.py`）

建网机的巡检引擎是 **`patrol.py`（常驻）**，每轮干两件事:① 探活本 LAN 报在线/离线（ping+SSH口兜底，ICMP 被拦也判得准）（多局域网时把 `status:*` 推给主，见上节）;② 顺带检查你登记要盯的进程。下面讲「盯进程」怎么用。

把一个**已经在跑的脚本/进程**交给建网机盯着：进程崩了大屏就看得见，关了笔记本也不影响——盯它的是常驻的建网机，不是你。

**① 主控登记**（任意能连到主 注册中心 的机器上）：

```bash
# 按进程名匹配，最省事（不用记 pid）
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --node <节点名/IP> --name 夜间训练 --match "train.py"
# 或按 pid 精确盯
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --node mac-studio --name 渲染 --pid 4321
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --list             # 看登记了哪些
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --unwatch 夜间训练   # 撤
```

**② 建网机巡检自动盯**：`patrol.py` 每轮顺带检查登记在册、还在 `running` 的活儿，把死活写进 `task:*`，大屏可见。**主建网机（单局域网就那一台）常驻 `patrol.py`**（次精简版不跑它，外地节点从简，见〈多局域网〉）：

```bash
# 主建网机 / 单局域网那一台：--registry-host 填 127.0.0.1（只查它够得着的节点）
nohup python3 ~/myainet/scripts/patrol.py --registry-host 127.0.0.1 > ~/patrol.log 2>&1 &
```

只查**自己够得着的节点**上的活儿（本机直接查、本 LAN 节点走 SSH 查；够不到的交给那个 LAN 的 hub）。查不出（SSH 不通 / 没拿到明确结果）就**保持原状，绝不误判 stopped**。

**posix 与 Windows 节点都支持**：posix 用 `kill -0`（pid）/ `pgrep -f`（命令行匹配）；Windows 经 SSH 跑 PowerShell——`Get-Process -Id`（pid）/ `Get-CimInstance Win32_Process` 按命令行匹配，用 `-EncodedCommand` 传输绕开 cmd/powershell 转义。节点是不是 Windows，从注册卡的 `hardware.os` 自动判。

**边界（v1）**：只**盯**已在跑的进程（你怎么起的随意，最好后台 nohup 起）；进程没了标 `stopped`，但**拿不到退出码**（要退出码得在启动时把 `$?` 写文件——「带监控启动」助手是后续）。

---

## 远程工作区：借节点的盘 + GPU 干活（原生，无容器）

**用途**：某台机器（常是主控 / 你手头这台）盘满了、或没 GPU，而网里另一台节点盘大 / 有显卡 —— 把那台设成「工作区」，本机 `ssh <别名>` 一步进去、用它的盘 + GPU 干活；数据落那台盘上、本机零占用。

**何时设（别滥设）**：要**持续 / 交互**地占着那台干（反复进去、文件长期留在 `work_dir`、当远程开发盒）才设。**一次性任务**（跑一条命令、一次训练）直接 `dispatch --node X "命令"` 就够，**不必设工作区**。**从不自动设** —— 触发只有两个：你明说「在 X 上开个工作区」，或控制模式 ② 评估发现「本机盘满 / 没 GPU + 某节点有富余」时主动提议。

**为什么 SSH 进节点干活、不把它的盘挂回本地**：跨 OS 文件系统 + 跨 LAN 延迟，又慢又脆（NTFS 没 Unix 权限位、网络挂载的软链易碎）。改成「在节点本地干、只把*终端* SSH 过去」—— 盘和算力都在节点本地，只有键盘回显走网络。

**原生 = 就用那台的 OS / python / GPU，不套任何运行时。** 换来三点：
- **主控能从空机器远程部署** —— 部署全是 SSH 能跑的 operate 层，没有"先装个运行时"那道墙；
- **GPU 直连**（Win/Linux 原生 CUDA、Mac 原生 Metal，无虚拟层，比任何隔离层都直）；
- **顺 myainet 的纹理** —— 网本就异构、`dispatch` 本就按节点 `os` 出命令。代价：工作区是**那台的原生环境**（Win 给你 PowerShell、不是 bash）—— 所以下面 ③ 那条「读契约 + 走 dispatch」是硬前提，agent 才不跨系统出错。

**流程**（三系统同形，路径 / shell 不同由 `dispatch` 兜）：

**① 选盘** — 读那台卡的 `hardware.disks`（每块盘 `{mount, total_gb, avail_gb}`，sysinfo 自报：Win 列盘符、posix 列真实挂载），挑 **`avail_gb` 最大那块** 定 `work_dir`（别默认系统盘）。例：win 的 D: 空 160G ≫ C: → `--dir D:\myainet-ws`。

**② 一句话部署**（主控远程对节点跑，operate 层、零运行时安装）：
```bash
ssh <节点> "<卡里的 python> ~/myainet/scripts/setup_workspace.py \
  --dir <选好的盘路径> --registry-host <建网机IP> --node-name <节点名>"
```
它干三件：建 `work_dir` → 写自报标记 `~/.myainet/workspace.json` → 触发 `register_node` 自报进卡。**一条命令、远程、无墙。**

> **跨 LAN 节点**（主控直接够不到它）：把 `ssh <节点>` 换成 `ssh -J <它的建网机> <节点>`，或直接在它的建网机上跑（同〈控制模式〉的 `belongs_to` 路由，**别直连外地节点**）。

**③ OS 契约自报进卡（关键，别漏）**

部署完，那台卡的 `workspace` 字段就带一份**完整 OS 契约**：`os` / `shell` / `work_dir` / `python`（确切解释器）/ `gpu` / `host_access`（本机能直用的 GPU、`ollama` 等，原生就在 localhost）/ `state`（盘 + GPU 占用）。`assemble_network` / 控制台 / `dispatch` 读卡即知「这台有工作区 + 它的 OS 契约」。

> **铁律：agent 永不猜 OS。** `/work` 还是 `D:\work`、`python3` 还是 `python` —— **全读卡里的契约，不假设**；**所有操作走 `dispatch`**（它按节点 `os` 出 posix/Windows 命令）。这是「机器自报进卡、读卡不猜」doctrine 的一部分，也是**原生工作区跨系统不出错的唯一保证**。

**④ 进入 / 派活**
- **进入**：`ssh <节点>` 落进它的 shell，`cd <work_dir>`。要一键就给本机 `~/.ssh/config` 加别名的 `RemoteCommand`：
  - posix 节点：`cd <work_dir> && exec $SHELL`
  - Windows 节点：`cmd /k cd /d <work_dir>`
- **派活**：一律 `dispatch --workspace`，它读卡 `work_dir`、**按 `os` 自动 cd**（agent 不手写跨 OS 路径）：
  ```bash
  # 解释器用卡里的 workspace.python（确切路径），别裸 python —— Win 上未必在 PATH，合上面铁律
  python3 ~/myainet/scripts/dispatch.py --node <节点> --workspace --name 训练 "<workspace.python> train.py"
  ```
  → dispatch 在那台 `work_dir` 里、用它的盘 + GPU 跑。**agent 给意图，cd 交给 dispatch，解释器读 `workspace.python`。**
  > dispatch 的自动 cd 按节点 **SSH 默认 shell**（Win 默认 cmd，用 `cd /d`）；别把 Windows 节点的默认 shell 改成 powershell，否则 `cd /d` 语法不符。

**⑤ git**
- `work_dir` 就是那块盘上的真实文件夹 —— 节点**自带的 git** 直接对着它操作；
- 没装 git 就在那台 `pip install dulwich`（纯 python 的 git，零系统依赖、三平台通用）。

**注意**
- **数据安全**：`work_dir` 是盘上真实目录，**没有容器可删、天然不丢**。
- **拆工作区**：删节点的标记 `~/.myainet/workspace.json`（要的话连 `work_dir` 一起删）+ 重注册 → 卡里 `workspace` 变 `null`，全网即知它不再是工作区。
- **原生即那台的环境**：Win 工作区是 PowerShell + Windows 路径、Mac/Linux 是 bash/zsh —— 正因如此，③「读契约 + 走 `dispatch`」不是建议、是前提。
- **连通抖动**：走 Tailscale DERP 中继时 `ssh <别名>` 偶尔超时，重试即可（不是真断）。

---

## 节点路径：扫描 & 注册

### Step 1 确认目标机器

默认分析本机。询问：本机还是远程？节点自定义名称（可选，默认 hostname）？建网机 IP 是多少？

如需远程采集（不在那台上装 skill）：把 `sysinfo.py` 喂过去跑（三平台通用、无需 bash）：
```bash
ssh [-p PORT] USER@HOST python3 - < scripts/sysinfo.py
```

### Step 2 采集硬件数据

运行 `scripts/sysinfo.py`（Python 脚本，macOS / Linux / Windows 三平台通用，无需 bash / WSL）：

```bash
python3 scripts/sysinfo.py
```

输出 key=value 格式，采集：CPU 型号/核心数、GPU 型号/VRAM/框架、RAM、存储总量/可用量/类型、内网 IP、OS、已安装工具（git、python3、node、ollama、opencode 等）、是否常驻设备、网络连通性和延迟。

### Step 3 四维评估

评估原则：**看潜力，不只看现状**。环境缺失（没有 Python、磁盘满）不等于不能用，给出解决步骤后重新评估。

> **下文表格 / 示例里出现的具体产品 / 模型 / 框架名一律只是举例、可能已过时——真要推荐时 AI 联网搜当下最佳，别照搬本文档的名字。** 稳定的运行时入口（Docker / Python / git / Ollama 本体）可直接用；会快速更替的（模型、对象存储、向量库、自动化框架、微调工具）必须搜当下。

---

#### 一、🧠 能跑本地大模型吗？

检查 GPU VRAM 和 RAM，**联网搜索当前最新可用的开源模型**（HuggingFace / Ollama 模型库），给出具体推荐：

| 硬件条件 | 结论 | 具体建议 |
|---------|------|---------|
| NVIDIA/AMD GPU VRAM ≥ 24GB | ✅ 可跑大参数模型 | 联网查当前 VRAM 对应的最新推荐模型及量化版本，给出 `ollama pull <model>` 命令 |
| NVIDIA/AMD GPU VRAM 8–23GB | ✅ 可跑中型模型 | 同上，按 VRAM 给具体模型和量化 |
| NVIDIA/AMD GPU VRAM 4–7GB | ⚠️ 可跑小模型 | 同上，说明速度限制 |
| Apple Silicon（统一内存）≥ 32GB | ✅ Metal 加速 | 联网查 Metal 支持最好的当前模型 |
| Apple Silicon 16–31GB | ⚠️ 中小模型 | 同上 |
| 无 GPU，RAM ≥ 64GB | ⚠️ CPU 推理（慢）| 说明每 token 耗时，建议只跑小模型 |
| 无 GPU，RAM < 32GB | ❌ 不适合本地模型 | 建议走云端 API |

推荐工具：Ollama（端口 `:11434`）安装命令：`curl -fsSL https://ollama.com/install.sh | sh`

**联网搜索动作**：拿到 VRAM/RAM 数据后，搜索"best open source LLM [VRAM]GB 2025"或"ollama models [VRAM]GB"，取最新结果中下载量高、评价好的模型推荐给用户，不使用硬编码的旧模型名单。

如果适合跑大模型，同时评估是否适合**训练/微调**：VRAM ≥ 16GB → 可以跑 LoRA/QLoRA（高效微调工具链 AI 搜当下）；VRAM ≥ 40GB → 可全量微调。

---

#### 二、💻 适合部署本地项目吗？

| 条件 | 结论 |
|------|------|
| CPU ≥ 8 核 + RAM ≥ 16GB + SSD | ✅ 适合：Web 服务、Docker 容器、数据处理管道 |
| CPU ≥ 4 核 + RAM ≥ 8GB | ⚠️ 适合轻量项目：小型 API、脚本服务、静态站点 |
| CPU < 4 核 或 RAM < 4GB | ❌ 不适合跑项目，适合做脚本节点 |

环境检查与修复：
- 无 Docker → `curl -fsSL https://get.docker.com | sh`
- 无 Python → `brew install python3` / `sudo apt install python3 python3-pip`
- 无 Node → `brew install node` / `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo bash -`
- 无 git → `brew install git` / `sudo apt install git`

适合部署项目的节点同时也是**测试节点**：用来跑单元测试、集成测试、CI 任务，让编程节点提交代码后推到这里自动测试。

---

#### 三、💾 存储状况如何？

读卡的 `hardware.disks` **全部盘**（每块 `{mount, total_gb, avail_gb}`）算存储——**最大空闲盘＝存储落点**；总量：**Windows/Linux 多物理盘相加**，**macOS 的 `disks` 是 APFS 同一容器的多个卷（`total`/`avail` 都相同），按容器去重、别叠加**（否则一块 228G 会被算成 1.3T）。**别只看 `storage` 摘要**（只含系统盘 C:，多盘机会把 D:/E: 的大盘漏掉，误判成"盘小 / 没存储节点"）：

**可用空间充足（可用 > 总量 30%）**：
- 总量 ≥ 1TB → 推荐作为存储节点：搭**对象存储**（存模型权重/数据集）+ **向量数据库**（RAG 知识库）——**具体选型 AI 搜当下主流**，按它给安装命令。
- 总量 < 1TB → 作为普通节点，存储不是优势

**可用空间紧张（可用 < 总量 20%）**：
- 先分析大文件：
  ```bash
  # macOS
  du -sh ~/* | sort -rh | head -20
  # Linux
  du -sh /home/*/* 2>/dev/null | sort -rh | head -20
  ```
- 给出具体清理建议（大文件列表、可安全删除的缓存目录）
- 用户清理后重新评估，清理后空间若达标则推荐存储节点

---

#### 四、⚙️ 兜底：脚本 / 自动化 / 测试节点

任何能联网的机器都适合，哪怕只有 2 核 4GB：

- **自动化编排**：搭一套自动化编排工具（**具体选型 AI 搜当下主流**），或 Python cron 脚本
- **数据采集**：Python + requests/playwright，适合定时爬取、API 轮询、数据清洗
- **轻量测试**：跑单元测试、接口测试，不需要大算力
- **API 中转**：给其他节点做请求转发、负载分担

环境不满足时直接给安装命令，装完即可用。

---

### Step 4 潜力结论与行动清单

汇总评估结果，用自然语言说明：

1. **这台机器现在能做什么**
2. **做以下几件事之后还能做什么**（具体列出命令或步骤）
3. **在 myainet 网络中建议承担的角色**（可以多个）

示例输出：
```
这台机器（gpu-rig）：
✅ 现在就能做：本地大模型推理（VRAM 16GB）
   → 联网查当下 16G 显存适配的主流模型（**别报训练记忆里的旧名，如 qwen2.5——多半已过时**）
   → 安装：ollama pull <当下查到的型号>
✅ 现在就能做：本地项目部署（12核 32GB RAM NVMe）
   → 适合跑 Docker 服务、数据处理管道

⚠️ 做一件事之后还能做：训练微调
   → VRAM 16GB 支持 LoRA/QLoRA
   → 高效微调工具链 AI 搜当下

⚠️ 清理磁盘后还能做：存储节点
   → 当前可用 45GB（总量 2TB，使用率 98%）
   → 发现大文件：/home/user/old-datasets/ 800GB
   → 清理后可用约 850GB，可搭对象存储 + 向量库（选型搜当下）

建议角色：🧠 推理节点（主）+ 💻 项目部署（兼）
清理磁盘后可增加：💾 存储节点
```

### Step 5 输出 ASCII 节点名片

```
╔══════════════════════════════════════════════════════╗
║             myainet NODE CARD                        ║
╠══════════════════════════════════════════════════════╣
║  Node     : <hostname>                               ║
║  Role     : <主角色 emoji + 名称>                    ║
║  Also     : <兼任角色>（如有）                       ║
╠══════════════════════════════════════════════════════╣
║  CPU      : <型号> (<核>C/<线程>T)                   ║
║  GPU      : <型号> <VRAM>GB  [<框架>]               ║
║  RAM      : <总量>GB                                 ║
║  Storage  : <类型> <可用>/<总>GB                     ║
║  OS       : <系统>                                   ║
╠══════════════════════════════════════════════════════╣
║  可接受任务：<具体任务类型>                          ║
║  推荐部署 ：<工具:端口>                              ║
║  待解决   ：<环境缺失或待清理的事项>（如有）         ║
╠══════════════════════════════════════════════════════╣
║  IP       : <内网 IP>                               ║
║  SSH      : ssh <user>@<ip>                         ║
╚══════════════════════════════════════════════════════╝
```

### Step 6 注册到 注册中心 & 保存档案

**先自动开启 SSH（节点必须能被建网机/主控控制，不能假设它已开）**：

```bash
python3 scripts/enable_ssh.py   # 三平台通用、幂等；需管理员（会提示输一次密码）
```

然后运行 `scripts/register_node.py`（也可用 `register_node.py --enable-ssh` 一步完成「开 SSH + 注册」）：

```bash
# 同一局域网：不用给 IP —— 它自己广播找建网机（discover.py）。这才是「不输 IP 就入网」。
python3 scripts/register_node.py
# 跨局域网 / 广播被挡（个别 AP 隔离的 WiFi）/ 自动没找到：才手动给地址
python3 scripts/register_node.py --registry-host <建网机IP 或 Tailscale 地址>
```

注册成功后输出：`✅ 节点 <hostname> 已注册到 myainet（长期保存）`，并打印 `🔑 换钥匙：装入 N 把控制方公钥`。

> **SSH 免密已焊进注册，通常不用单独配** —— `register_node` 注册成功就顺手发布/安装公钥了（节点装主控+建网机的公钥，零密码、替代 `ssh-copy-id`；Windows 管理员账号会自动写 `administrators_authorized_keys` 并设好 ACL）。要单独跑或排查才用：`python3 scripts/keysync.py --role node`（同样不给 IP 自动发现）。

默认注册是长期档案，不设置 TTL。节点是否在线由 Dashboard 刷新时通过 LAN ping、Tailscale ping、SSH 等检测判断。只有临时节点才传 `--ttl <秒数>`。

Markdown 档案格式 `<hostname>-node-card.md`：

```markdown
# 🤖 myainet 节点档案：<节点名称>

**生成时间**：<datetime>
**角色**：<主角色> + <兼任角色>（如有）

## 硬件规格
| CPU | GPU | RAM | 存储 | 系统 |
| ... | ... | ... | ...  | ...  |

## 能力评估

### 🧠 本地大模型
<能/不能，能的话具体推荐哪些模型（联网搜索当前最新），给出 ollama pull 命令>

### 💻 本地项目部署
<适合/勉强/不适合，适合哪类项目，缺少什么环境及安装命令>

### 💾 存储
<可用空间，是否推荐作存储节点；对象存储 / 向量库选型 AI 搜当下，或给出清理建议>

### ⚙️ 脚本 / 自动化 / 测试
<自动化编排工具（选型搜当下）/ Python cron / 测试框架，适合哪类任务>

## 待解决事项
<环境缺失、磁盘满等问题及具体修复命令，解决后能新增的能力>

## 网络信息
- **内网 IP**：xxx.xxx.xxx.xxx
- **SSH**：`ssh user@ip`
- **注册中心**：已注册 ✅

---
*Generated by myainet*
```

---

## 主控路径：查看全网 & 生成调度配置

主控用来查看整个 myainet 网络状态，生成可加载进任意 AI 工具的网络调度配置。

> **核心铁律：主控只读中央注册中心，绝不扫机器。**
> 每台机器**注册时自己已采集好**硬件 / agent / 模型，全在它的 `node:*` 卡里。主控就三步：**读卡 → 展示 → 出调度配置**。
> - ✅ **读注册中心 = `assemble_network.py`**（裸 socket 走 Tailscale，零依赖）：CPU / 显卡 / 内存 / 存储 / agent / 模型一次全拿到。这是主控获取全网信息的**唯一正路**。
> - ❌ **绝不 SSH 进已注册的机器跑 `sysinfo` / PowerShell 现扫硬件**。卡里都有，扫是多余；而且必踩坑——建网机是 Windows 时 `python3` 找不到（它叫 `python`）、中文系统 GBK 编码崩、PowerShell 超时，全是白受的罪。**看到自己想「连进去看看硬件」就停手，去读注册中心。**
> - 想要某台的**新数据** → 不是你去扫，而是让**那台机器自己**重注册（见下方可选步骤，它本地跑 `register_node` 自采集后写回中心）。

### Step 0 主控侧 Tailscale 检查

主控角色必须具备 Tailscale，才能在局域网外连接建网机。进入主控路径后先主动检查，不要只写待办：

```bash
command -v tailscale || command -v /usr/local/bin/tailscale
tailscale status || /usr/local/bin/tailscale status
tailscale ip -4 || /usr/local/bin/tailscale ip -4
```

**没装就装上 —— 装法跟建网机/节点完全一样，看系统不看角色（见〈建网路径 Step 3〉）：** macOS/Linux 走 CLI + 系统服务（`install-system-daemon` / systemd），Windows 走官方 App（只此一种，可静默装）。**绝不用 userspace**——不建网卡、没服务托管，漫游/重启就死，主控笔记本尤其会踩；macOS 的 `install-system-daemon` 走经典 utun、连系统扩展 GUI 都不用，skill 跑命令 + 你输次 `sudo` 密码 + 浏览器登一次即可。装好后跑一次 `python3 scripts/tailscale_proxy_bypass.py`（Clash 等会拦 `100.x`）。

主控拿到自己的 Tailscale IP 后继续。

### Step 0.5 确认连接方式

询问用户：你现在和建网机在同一局域网内，还是在外网（需要通过 Tailscale 连接）？

- **局域网内** → 直接使用建网机的局域网 IP
- **外网** → 使用建网机的 Tailscale IP

确认建网机 IP 后验证链路。**SSH 实连是唯一权威判据**（建网机若是 Windows，ICMP 默认被拦、ping 必失败，但那不代表不通）：

```bash
# 先这条——连得上就是通了，ping 通不通都不影响：
ssh -o BatchMode=yes -o ConnectTimeout=8 user@<建网机IP> hostname
# 下面两条仅作辅助参考，失败（尤其 Windows 建网机）很正常，别据此判「不通」：
ping -c 2 <建网机LAN_IP>
tailscale ping <建网机Tailscale_IP>
```
> ⚠️ ssh 通了就往下走，**别因为 ping 失败就停**——这正是今天反复踩的坑（ping 丢包被误当「建网机不通」）。

### Step 1 主控自报进网 +（可选）刷新节点名片

**主控自报**（注册自己 + **顺手把主控公钥发布到注册中心**，供建网机/节点免密装它 —— 不用再单独跑 keysync）：
```bash
python3 ~/myainet/scripts/register_node.py --role 主控
# 同局域网自动发现建网机；跨网加 --registry-host <建网机 Tailscale 地址>
```

**节点名片过时了才刷**（卡新鲜就跳过、直接 Step 2）：让那台节点**自己重跑 skill**，或用 `dispatch` 触发它 `register_node`。平时**建网机的 patrol 每 ~1 小时自动重注册本 LAN 节点**，通常根本不用管。原则不变：**让节点自采集，不是主控远程扫。**

### Step 2 读取所有已注册节点

运行 `scripts/assemble_network.py`：

```bash
python scripts/assemble_network.py \
  --registry-host <建网机IP> \
  --registry-port 27182 \
|| python3 scripts/assemble_network.py \
  --registry-host <建网机IP> \
  --registry-port 27182
```

### Step 3 展示原始网络状态 + 角色建议

展示每台节点的**硬件 + 实装的 agent/工具**（只列事实、不打分、不自动分配角色），由主控的 AI 按事实判断、用户确认：

```
🌐 myainet 网络原始状态
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧱 建网机    : <hostname>   LAN <LAN_IP>   TS <TS_IP>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
节点: <hostname>   IP: <ip>
  CPU    : <CPU>
  GPU    : <GPU> <VRAM>GB
  RAM    : <X>GB    存储: <存储>
  OS     : <OS>
  Python : <解释器路径>          ← 调它上面的脚本用这条（机器自报），别猜 python/python3
  Agents : claude, codex, …      ← 装了哪些 AI agent（带版本）
  CLI    : python3, docker, …    ← SSH 命令可控的工具/运行时
  GUI    : …                     ← 要靠 computer-use 控的 GUI 应用
  ★ 常驻设备，可作为建网机候选
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
共 <N> 个节点在线，待主控分配角色
```

展示完毕后，skill 给出角色分配建议，例如：

```
📋 建议角色分配（4 类能力，按事实给理由、不打分；一台可兼任）：
- <节点A>：🖥️ 本地算力(GPU)（RTX3060 12G → 本地推理/生图；训练吃力）
- <节点B>：☁️ 云端 AI（装了 claude/codex + 够得到 API → 编程/云端推理，不吃本地硬件）
- <节点C>：⚙️ 通用·自动化（CPU 多核 + 常驻 + python/docker → 脚本/定时/测试）
- <节点D>：💾 存储（盘大）

是否按此方案生成网络配置？可以调整任意节点的角色。
```

**怎么判角色（读三层事实，不打分、不写算法——AI 判；一台可兼任）：**

角色 = **4 类能力**，按能力一视同仁、跟在哪个 LAN 无关。**核心原则：角色只看"能力天花板"（稳的事实），不看当前占用**——满不满 / 忙不忙是【状态】、是"现在的问题"，归优化建议 + 巡检，**不改角色**。

| 能力角色 | 看什么（天花板） | 能接的任务 |
|---|---|---|
| 🖥️ **本地算力(GPU)** | GPU + **VRAM**（分档：小=推理 / 中=生图 / 大=训练） | 本地推理 / 生图 / 训练 |
| ☁️ **云端 AI** | 装了 agent + 有网 + 够得到 API（**跟本地硬件无关**，弱机也行） | 编程 / 云端推理 |
| 💾 **存储** | 盘**总容量大**（空不空是状态，不在此判） | 存模型 / 数据 |
| ⚙️ **通用·自动化** | CPU + 常驻 + runtime（python/docker） | 脚本 / 定时 / 测试 / 轻服务 |

读三层事实喂这张表：① 硬件→本地算力/存储；② 环境(agents/cli)→云端 AI/自动化；③ 网速→只影响"适不适合 serve/下载"，不是独立角色。另一轴是拓扑角色（主控/建网机/次建网机/节点，`always_on` 的是建网机候选）。

**优化建议 = 站在全网做分工，不是逐台挑刺；只提「facts 一摆就成立」的，每条给出口：**

- **能力缺口**（不是"闲置"）：有 GPU 却没装运行时（ollama）→ 本地算力**根本用不了**，这才是缺口。**GPU 空载本身正常、不是浪费**，别因为"没跑满"就硬塞活。
- **大局分工**：多张独立显卡**别都装一样**——按显存与特长分（一张本地大模型，另一张生图 / 音频 / 数字人）；大盘那台 → 工作区 + 向量库 + 数据仓；缺的角色（存储 / 自动化）补一台。
- **缺关键工具**：想当某角色却缺运行时（要跑 python 项目却没 python）→ 提装。
- **错配**：弱机硬扛重活（8G 机别派本地大模型，给它派云端）。
- **瓶颈**：节点在蜂窝 / 盘快满 / RAM 紧 → 提醒（如"主控盘只剩 33G，数据往大盘那台放"）。

> 具体方案（哪个模型 / 生图·音频·数字人工具 / 向量库 / 框架）**一律 AI 搜当下**，别报训练记忆里的旧名。

这些都写进**节点卡的 `problems` 字段**（`register_node` 从事实自动 derive 明显的 + AI 评估补角色相关的，re-register 刷新）。**派任务前先看目标机的 `problems`，能解决的先解决（装工具 / 清盘 / 装 agent / 装 ollama），任务才真能跑** —— 能力说"能不能"，`problems` 说"现在卡在哪"，两个都看齐了任务才落得了地。

**卡的更新 + 执行按实况：** 卡是快照、会过时——建网机 `dashboard` 每 30min 自刷；节点由 hub 的 `patrol --refresh-every`（默认 ≈1 小时）周期触发重注册，也可主控喊"重新注册 X"按需刷（刚在某台装完东西就刷它）。**但规划归规划、执行归执行：派任务那一刻按【真实状态】分配——pre-flight 查目标机此刻在不在线、要的工具/盘是不是真够，再定派给谁。`problems` 可以不解决（只是建议、不是硬门槛），但执行只认实况。**

等待用户确认或修改后进入 Step 4。

### Step 4 生成 myainet 网络配置

用户确认角色分配后，生成 `myainet-network-config.md`，保存在主控当前目录：

```
# myainet 网络配置
# 生成时间：<datetime>
# 节点数：<N>

你现在接入了一套 myainet 个人 AI 网络。接到任务：① 按类型选合适的节点（本地能干的先本地，再云端 API）；② **经 dispatch 执行、会改机器状态的动作 report 一笔** —— 别自己 ssh 直连跑活儿，那样网络对它就失去感知了。

【建网机（中枢）】<hostname>  LAN <LAN_IP> / TS <TS_IP>（外网用 TS）
- 注册中心 注册中心：<建网IP>:27182    脚本：~/myainet/scripts/

【<角色名>节点】<hostname>  <ip>
- 主能力：<角色> / 硬件：<简要硬件> / 可接任务：<具体任务类型>

...（其余节点同格式）

## 怎么执行（铁律）
1. **在节点上跑任何活儿 = 经 dispatch，不要自己 ssh "命令" 直连：**
   python3 ~/myainet/scripts/dispatch.py --registry-host <建网IP> --node <节点> --name <任务名> "<命令>"
   只有 dispatch 把任务记进账本（task:*）→ 上大屏、被巡检盯、可追溯；直连执行 = 任务隐形。
   （模糊任务可委托节点本地 agent：命令写成 "claude -p '目标'"；训练/大下载等长任务加 --detach。）
2. **会改变机器状态的动作，边做边 report**（装/删软件、换版本、回滚、试了又退…）：
   python3 ~/myainet/scripts/report.py --registry-host <建网IP> --node <节点> "在装新 ollama / 不行已回退"
   （拿不准或出岔子加 --warn。）这样"另一个任务偷偷改了机器"也不会让网络失控。
3. 只读探活（读卡 / healthcheck）可直连，不必经 dispatch。**判节点死活用 `dispatch --node <名> --check`（经 SSH 实连），不要自己跑 ping**——ICMP 常被防火墙拦、会把活机器误判成 down。
4. 够不到的节点 → ssh 它的建网机、在那台上跑 dispatch（看节点卡 belongs_to）；统一「主控→节点的建网机→节点」。
5. 节点无响应：告知用户，别静默重试超过 2 次。

## 退路（仅当本工具拿不到 myainet 脚本时）
非主控、手头没有 dispatch.py 的纯外部工具，才退回 SSH 直连（此时任务不记账、大屏看不到，尽量避免）：
ssh -J <user>@<建网IP> <user>@<节点IP> "命令"

---
*Generated by myainet · <datetime>*
```

生成后告知用户：
- 将 `myainet-network-config.md` 内容粘贴进任意 AI 工具的 system prompt，即可让该 AI 直接调度网络
- 下次运行此 skill 时会自动读取此文件，可直接下命令控制各台机器

---

## 注意事项

- **无需 redis / redis-cli / 任何 pip 包**：注册中心是自带的 `registry_server.py` + `registry_client.py`（纯标准库、裸 socket RESP）。别给任何机器装 `redis` 包——用不上。
- **注册中心 未部署**：先在建网机运行建网路径完成安装
- **节点离线检测**：`node:*` 是长期注册档案，不因 TTL 自动消失；在线/可控状态由 Dashboard 实时检测。临时节点可显式传 `--ttl <秒数>`。
- **GPU 检测顺序**：nvidia-smi → rocm-smi → system_profiler（macOS）→ lspci
- **Windows 建网机**：注册中心是自带 python 脚本（`registry_server.py`，免装无 Docker），SSH 用内置 OpenSSH Server，Tailscale 有原生客户端，可完整承担建网机角色
- **Windows 节点路径**：直接运行 `python3 scripts/sysinfo.py`，无需 WSL，三平台原生支持
- **网络连通性**：采集时检测能否访问 `api.anthropic.com`，在名片上注明墙内/墙外

---

## 跨平台注意事项（Win / Mac / Linux）

跨机器编排时各系统的坑，**编码最容易踩**（中文 Windows 尤其）。

### 编码 / UTF-8（重点）

- **入口脚本开头统一 UTF-8**：`register_node.py` / `dashboard.py` / `dispatch.py` / `patrol.py` 开头都 `os.environ.setdefault("PYTHONIOENCODING","utf-8")`（+ `PYTHONUTF8="1"`）并 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`。**新写脚本照抄这段**；子脚本（如 `sysinfo.py`）靠继承父进程这套环境变量来输出 UTF-8。
- **抓子进程输出必须显式 `encoding="utf-8", errors="replace"`**：`subprocess.run(..., capture_output=True, text=True)` 在**中文 Windows（GBK / cp936 locale）**会拿 GBK 去解子进程的 UTF-8 输出，碰到中文（应用名、CPU 串等）直接 `UnicodeDecodeError`、`stdout` 变 `None`。**这就是 win 建网机第一次注册崩的根因**。凡是抓另一个脚本 / 命令输出的地方都要带上 `encoding="utf-8"`。
- **emoji 打印**：`✅`/`⬜`/`🔍` 这类字符 GBK 控制台编不出来（`UnicodeEncodeError`），靠开头的 `reconfigure` 兜底；没兜的代码会崩在那行 `print`。
- **经 SSH 看到中文 / emoji 花了 ≠ 数据坏了**：Windows 控制台吐 GBK 字节，到你这边 UTF-8 终端就成乱码——**显示问题，不是存储问题**。验真假别靠肉眼：读 注册中心 里的值看**代码点**（`s.encode("unicode_escape")`），`建网机` 才是真「建网机」。
- **注册中心存取中文**：`registry_client.py` 裸 socket 全程 UTF-8、往返无损（`registry_server.py` 端 value 按 BLOB 存、字节级无损）。**别在 Windows 上用 `redis-cli` 传中文**——argv / 控制台 codepage 会乱；裸 socket 路径才干净。

### SSH / shell

- **Windows OpenSSH 默认 shell 不固定**：可能是 `cmd.exe`，也可能是 `bash`（装了 Git Bash 时）。**先探再下命令**（`echo $SHELL`、或先跑一条看报错），别假设是 cmd。路径写法跟着变：cmd 用 `%USERPROFILE%`、bash 用 `$HOME` / `~`、Windows 原生反斜杠 `C:\...` vs Git Bash 正斜杠 `/c/...`。
- **经 SSH 跑 PowerShell**：复杂命令用 `-EncodedCommand`（base64 / UTF-16LE）绕开 cmd/powershell 的转义 + 编码坑（`patrol.py` 盯进程就这么干）。

### 命令名 / 路径

- **Python 调用名**：Windows 多是 `python` 或 `py -3`；Mac/Linux 是 `python3`。脚本内部跑子进程用 `sys.executable`（自适应），但你**手动**敲命令时三个都试。
- **已分平台处理的（知道即可，别改）**：`ping`（Win `-n -w` / Mac `-c -t` / Linux `-c -W`）；常驻判定＝无电池为台式（Mac `pmset` / Win `Win32_Battery` / Linux `/sys/class/power_supply`）；本地模型扫描（ollama / LM Studio `~/.lmstudio` / HF 缓存 `~/.cache/huggingface`，都走 `Path.home()` 自适应）。

### 部署 / 网络

- **注册中心**：`registry_server.py`，三系统原生 python、免装无 Docker；**绑 `0.0.0.0`**，否则 LAN / Tailscale 够不到。
- **Tailscale CLI 位置**：macOS App 版包装器在 `/Applications/Tailscale.app/Contents/MacOS/Tailscale` 或 `/usr/local/bin/tailscale`（Homebrew CLI 可能连不上 App）；Win / Linux 在 PATH。
- **代理干扰**：Mac 上 Clash 等（7890/7892）会破 IPv6 和部分 CLI 的网络；Tailscale 走自己的 `100.x`，打不通直连时回退 **DERP 中继**（慢但通）。`tailscale_proxy_bypass.py` 已处理 `100.x` 绕过。

---

## 脚本说明

脚本都在 `scripts/` 下平铺，按职责分四组：

**核心 / 共用**
- `registry_server.py` — **零依赖注册中心**（顶替 Valkey/Redis）：sqlite + 标准库 RESP server，监听 27182。建网机跑这一个 python 进程即可，**三系统原生、免装、无 Docker/WSL**；卡落 `~/.myainet/registry.db`（WAL，崩了/重启不丢），只实现 myainet 用到的 SET(+EX)/GET/MGET/KEYS/DEL
- `registry_client.py` — 零依赖注册中心客户端（裸 socket 说 RESP，瞬断自动重连一次）；全项目读写注册中心都走它，节点**无需装任何东西**。连的就是 `registry_server.py`（RESP 只是线缆格式、不是 Valkey/Redis）
- `sysinfo.py` — 采集本机硬件 + 实装 agent/工具（Python，三平台通用）；远程采集 `ssh USER@HOST python3 - < sysinfo.py`
- `identity.py` — **机器级身份标记** `~/.myainet/identity.json`（role / central / name / belongs_to）。skill「第一步」跑它判身份（建网机 / 主控 / 次 / 节点 / 新机器）——**身份机器级、不绑目录**；标记优先、本机注册中心(27182)在则兜底判建网机。各路径结束 `--set` 写一笔
- `registry_cache.py` — **主控注册表本地镜像** `~/.myainet/registry-cache.json`。主控读全网时存一份原始卡；**建网机掉线**时 dispatch 回退它、直驱够得到的机器，也是**转移备份**（读不到不覆盖，保住上次的好镜像）

**建网 / 接入**
- `setup_hub.py` — **（一键建建网机，建网主入口）** 确定性依次：起注册中心 → 写身份 → 开 SSH → 起大屏+巡检 → 注册本机 → 装 Tailscale，**每步自验、结尾如实报告**（缺一就不报成功、退出码非 0）；**幂等**可反复跑（已起的跳过、缺的补）。`--verify` 只逐项核不动手；`--skip-ssh` 跳过开 SSH。**建网就跑这一个，别手动逐步拼**（根治 agent 漏步 / 写错 OS 命令）
- `setup_control.py` — **（一键配主控，主控入口；与 setup_hub 对称）** 确定性依次：装 Tailscale → 写身份(主控,central=建网机) → 开 SSH → 注册自己 → 存本地镜像 → 自检。`--central <建网机地址>` 必填（同 LAN=lan_ip / 异地=Tailscale IP），**自指毒值(127.0.0.1)当场拒**（根治 central 写错→误判注册表空的事故）；`--verify` 只核、`--skip-ssh` 跳 SSH。**配主控就跑这一个，别手敲 identity+register+cache 五步**
- `discover.py` — **（局域网自动发现建网机，零依赖）** 建网机的注册中心起一个 UDP 应答器；新机器广播「建网机在哪」，建网机回自己的 LAN IP。`register_node` / `keysync` 不给 `--registry-host` 时自动调它 → **不输 IP 就入网**。同 LAN 有效（广播不跨路由；个别 AP 隔离的 WiFi 会挡 → 退回手填）；跨网用 Tailscale 名字
- `enable_ssh.py` — 跨平台幂等开启 SSH 服务（建网 / 入伙时调用，需管理员）
- `netprobe.py` — 探测外网连通性 / NAT 类型 / 蜂窝 / 地区，给远程接入方式建议（默认 Tailscale）
- `tailscale_proxy_bypass.py` — 修正本机代理对 `100.x` 网段的绕过规则（Clash 等会拦 Tailscale）

**注册 / 触发**
- `register_node.py` — 采集 → 生成节点卡 → 写注册中心。卡有**两条分类轴**：① **拓扑角色** `role`（主控 / 建网机 / 次建网机 / 节点）+ `belongs_to`（节点归哪台建网机，路由键）；② **能力三层**：硬件 `hardware` / 环境 `agents`·`cli`·`gui` / **网速 `link`**。`--measure-link`【建网机用】自测本 LAN 外网底子（netprobe：net_class/cellular/nat/isp）+ 下行带宽（best-effort，`--speed-url` 可换端点），**节点不自测、继承它建网机的 link**。`--enable-ssh` 一步开 SSH；注册成功顺手写本机身份标记（`identity.py`，patrol 重注册会自愈 `central`，转移后节点自然指向新家）

**监控（建网机常驻）**
- `dashboard.py` — 建网机上的 HTTP 大屏，浏览器 / iPad 访问；读注册中心 + 本地探活（ping+SSH口兜底）+ 兜底读 `status:*`
- `patrol.py` — 巡检循环（常驻）：① 探活本 LAN 报在线（ping+SSH口兜底）、多局域网时推 `status:*` 给主；② 盯登记在册的进程、更新 `task:*`（posix + Windows 都支持）；③ `--refresh-every`（默认 ≈1 小时）周期触发本 LAN 节点重注册、刷新卡（防卡烂；节点照旧被动）；④ **每轮自动补装控制方公钥**（注册中心里新出现的 `pubkey:*` 下一轮就装进本机门，幂等——主控晚于建网机入伙也不用手跑 keysync，组网不挑顺序）
- `watch_job.py` — 登记 / 列出 / 撤销「让建网机盯一个在跑的进程」（写 `task:*`，由 patrol 查死活）
- `dispatch.py` — **（主控派任务，编排④的第一块）** 在某节点跑一条命令（本机 / SSH，posix + Windows）→ 写 `task:*`（running→done/failed + 退出码 + 输出尾部）→ 回显；`--detach` 长任务交巡检盯；`--workspace` 在节点工作区 `work_dir` 里跑（读卡按 `os` 自动 cd，agent 不手写跨 OS 路径）。判断（挑机器 / shell 还是委托 agent）在主控 AI，dispatch 只执行；**建网机掉线时 resolve 自动回退主控本地镜像**（命令照跑、任务记账缺）。**人体工学**：`--registry-host` 不填=从本机 identity 的 central 读；`--node` 支持模糊（机名子串 / 硬件型号如 `2070` / `gpu` 关键词，多台报歧义）；`--delegate "目标"`=委托模式（按卡自动挑 codex/claude/opencode 包非交互调用）；**`--check`=判节点死活（经 SSH 实连，唯一权威方式，别用 ping）**
- `setup_workspace.py` — **（把节点设成原生工作区）** 主控远程对节点跑：在选好的盘建 `work_dir` + 写自报标记 `~/.myainet/workspace.json` + 触发 register 自报 OS 契约进卡（**无容器、无 Docker**）；之后 `dispatch --workspace` 派活进去
- `report.py` — **（agent 汇报，监控模式②）** agent 主动往看板写一条**带判断**的 note（写 `task:*` status=note，大屏灰底可见）。机器写"还活着"（patrol），agent 写"发生了啥 / 好不好 / 要不要管"
- `healthcheck.py` — **（建网机自检）** 本地查 注册中心 / Dashboard / Patrol / Tailscale 在不在、挂的给启动命令（跨平台）；skill 认出建网机时先跑它
- `keysync.py` — **（SSH 换钥匙的共享逻辑，通常由 `register_node` 注册时 + patrol 自动调用，不单独跑）** 发布本机公钥 + 把控制方公钥装进本机门；全本机操作 `authorized_keys` + 注册中心 发布/拉取，**零密码、幂等、不覆盖你原有钥匙**。Windows 管理员账号**自动**写进 `administrators_authorized_keys` 并设好 ACL（用内置 SID，中文系统也对）
**汇总 / 生命周期**
- `assemble_network.py` — 读取所有节点，输出原始网络状态（硬件 + agent/cli/gui，**只列事实不打分**），供主控分配角色
- `leave_network.py` — 退网：删注册卡 + 退 / 卸 Tailscale（只动本机的安全闸，`--purge` 连软件一起卸，`--dry-run` 预览）
- `transfer_role.py` — 转移：把建网机「监听 + 写大屏」职责 + 注册表搬到新机——复制 `node:*` / `task:*` 老→新（**老 hub 已死加 `--from-mirror`，用主控镜像兜底**）+ 校验 + 打**搬家清单**（起新 hub 服务 / 改次 bridge / 改身份标记）。**不删老的**，`--dry-run` 预览；现实是 **on-LAN 转给节点**

## 参考文件

- `references/model-matrix.md` — 完整模型推荐矩阵（按 VRAM 细分）
- `references/build-manual.md` — 建网机各步的**手动等价命令**（仅 `setup_hub.py` 某步失败时排查用，正常别看）
