---
name: myainet
description: >
  myainet personal-AI-network builder. Use this skill whenever the user wants to understand
  what tasks a given machine can take on in the AI era, generate a node card for a machine,
  or join several machines into a myainet personal AI network.
  Trigger examples (Chinese): 「这台机器能跑本地大模型吗」「帮我分析这台服务器能干啥」
  「给我的机器生成节点名片」「这台机器适合装 Claude Code / OpenCode 吗」
  「我想让多台设备协同工作，帮我组网」「扫描所有节点，生成网络配置」。
  Trigger examples (English): "can this machine run a local LLM", "analyze what this server can do",
  "generate a node card for my machine", "is this machine suitable for Claude Code / OpenCode",
  "I want several devices to work together, help me build a network", "scan all nodes and generate a network config".
  Supports three paths: hub-build path (set up registry center + remote access), control path (view the whole network and generate a dispatch config), node path (scan and register this machine).
---

# myainet — personal-AI-network builder

> **Language hard rule: this document is in English, but ALL user-facing interaction must be in the USER'S language.** When asking for a role / confirming an action / reporting status / giving advice, follow the language the user is writing in — look at what language the user is talking to you in, and if unsure, use the system locale (`echo $LANG` / Windows `Get-Culture`). Script output may be in Chinese — that's fine (you read it and relay it in the user's language); **do NOT default to English just because this document is in English.**

Join several machines into a myainet personal AI network — each playing its part. Once any AI tool loads the network config, it can dispatch the whole network uniformly:

```
任意 AI 工具（加载 myainet 网络配置后）按任务匹配 4 类能力节点：
  ├─→ 推理 / 生图 / 训练（吃显存）       →  🖥️ 本地算力(GPU) 节点（按 VRAM 分档）
  ├─→ 编程 / 云端推理（装 agent+API）    →  ☁️ 云端 AI 节点（不吃本地硬件）
  ├─→ 脚本 / 定时 / 测试 / 轻服务        →  ⚙️ 通用·自动化 节点
  └─→ 存模型 / 数据                      →  💾 存储 节点（盘大）
```

## Network architecture

```
[主控] ──Tailscale──→ [建网机] ──LAN SSH──→ [节点 A]
                          │                 ──→ [节点 B]
                          │                 ──→ [节点 C]
                       注册中心 registry_server.py（27182）
```

**Three roles**:

| Role | Required tools | Notes |
|------|---------|------|
| 🧱 **Hub** | registry center (`registry_server.py`) + SSH server + Tailscale | An always-on machine, the foundation of the network; the control machine connects to it over Tailscale, and it then hops to the nodes. The registry center is a bundled python script — **no install, no Docker** |
| 🖥️ **Control** | Tailscale | The computer/phone you use day to day; connects to the hub via Tailscale, then hops over SSH to control nodes |
| ⚙️ **Node** | SSH server | A work machine on the LAN — only needs SSH enabled; no Tailscale, no registry center needed |

SSH hop command: `ssh -J user@建网IP user@节点IP "命令"` — this is the **low-level way to reach a node**; **run tasks through `dispatch` (it uses this hop underneath + records to the dashboard), don't run work directly with it** (see the hard rule in 〈Control mode〉).

---

## Prerequisite: script path convention (read this first — every command below depends on it)

In the examples below, **`~/myainet/scripts/...` is shorthand for "the `scripts/` directory inside this skill package"** (a sibling of this SKILL.md). Whichever agent's skills directory the skill is actually installed in, use that real path, for example:

- opencode: `~/.config/opencode/skills/myainet/scripts/`
- Claude Code: `~/.claude/skills/myainet/skills/myainet/scripts/` (whatever the actual load location is)
- manual clone: wherever you cloned it

**Don't assume `~/myainet` really exists** — before running a command, replace the prefix with this skill's real location (you know where it is the moment you load this file). If an old copy lingers on the machine, the skill's real body is authoritative — don't end up running stale scripts in an old directory.

## Prerequisite: make sure Python is present on this machine (the agent handles it automatically — don't assume it exists)

This skill's scripts are Python, and **have zero dependencies** (standard library only + the bundled `registry_client.py`, no `pip install` of anything, no `redis-cli`). So as long as a Python interpreter exists, everything runs.

Before running any script, **the agent first confirms Python is present on this machine; if not, it installs it automatically and continues** — don't assume it exists (many Windows machines simply don't have Python):

```bash
# 一条命令把所有写法试全——任一行打印出版本号，就是「有 Python」，直接往下用那条。
# ⚠️ Windows 上 python3 通常不存在（解释器叫 python / py）。别一看 python3 报错就判「没有 Python」，
#    必须把下面四种都试过、全失败才算真没有。
python --version || python3 --version || py -3 --version || py --version
```

If absent, install it (**fall back layer by layer, never dead-end** — the agent picks whichever one currently works):

- **Windows**: `winget install -e --id Python.Python.3.12`; if winget isn't available → download the python.org installer and do a silent install; if that still fails → unzip the official **embeddable zip** into the myainet directory and use it directly (no admin, doesn't pollute the system).
- **macOS**: `xcode-select --install` (ships python3 once installed), or `brew install python`.
- **Linux**: `sudo apt install -y python3` (or the matching `dnf` / `pacman` / `zypper` package).
- All failed → give the user **explicit manual install instructions**, don't silently stall.

Once installed, run the rest of the scripts with whichever Python you found.

> **The "find Python on the spot" step above is only for the *first* registration of *this* machine (this machine has no node card yet).**
> An already-registered machine — including any node / hub you want to run scripts on remotely — has its Python interpreter **written in the `python` field of its own `node:*` card** (the real interpreter `sysinfo` self-reports at registration; on Windows that's the real path of `python`, not `python3`).
> **To run a script on a given machine, first read the `python` from its card and run with that one — don't probe on the spot, don't guess `python3`/`python`.** This is the same principle as "read the registry, don't scan the machine": what a machine has installed and which interpreter it uses are all declared by itself into the registry — you only read, never guess.

## Step one: auto-detect state (machine-level identity, not directory-bound)

Run this one line the moment you load — it reads the machine-level identity marker `~/.myainet/identity.json` and checks whether the local registry center (27182) is up, then prints facts:

```bash
python3 ~/myainet/scripts/identity.py
```

Decide which path to take based on the printed `role`. **Identity is machine-level — it's recognized from any directory**; capabilities / responsibilities are derived from identity, with no separately-stored permission bits:

> **Both the hub and the control machine can control the whole network** (control capability is shared); the only difference is the **hub carries the resident "listen + write dashboard" infra** (registry center + patrol + dashboard), while the control machine does not. So **there's no such thing as "promote to control"** — the hub already supports every control command.

**① `role=hub`** (the local registry center (27182) is up, or the marker says hub) → **first verify it was really fully built, then enter the console.** ⚠️ A running registry center alone ≠ a fully-built hub — it could be a leftover process, or last time only half got built (missing Tailscale / SSH / dashboard). So **use `--verify` to check each item — don't waltz into the console on assumption**:

```bash
python3 ~/myainet/scripts/setup_hub.py --verify     # 逐项核：注册中心 / 大屏 / 巡检 / SSH / Tailscale
```
- `✅ … 全部就位` → enter **control mode** (see below).
- `❌ 没建完，还差：X` (non-zero exit code) → **finish building first**: run `python3 ~/myainet/scripts/setup_hub.py` (idempotent — only fills the gaps, doesn't restart what's already running), build up to ✅, then enter control mode. Also take this path if the dashboard / patrol didn't auto-start after a reboot.

**② `role=control`** → it too controls the whole network but doesn't carry infra. Connect to the `central` (central hub) from the marker → enter **control mode** and get the exact same console and commands as the hub.
> **To set up a new machine as control** (no identity marker yet / a clean machine) → run the deterministic script, don't hand-type: `python3 ~/myainet/scripts/setup_control.py --central <建网机地址>` (same LAN → fill its lan_ip; remote → fill its Tailscale IP). It runs in order "Tailscale → write identity (control, central=hub) → SSH → register self → store local mirror → self-check", self-verifying each step; `--verify` can just check without changing anything. **The control machine's central must be the hub's address, and must never be self-referential** (filling 127.0.0.1 makes a bare skill load unable to find the registry center and wrongly conclude "registry is empty" — the script will reject it on the spot).

**③ `role=secondary hub`** (the slim version of a hub: local registry center + sync to the main, no dashboard/patrol) → verify/finish building: `python3 ~/myainet/scripts/setup_hub.py --main <central> --verify`; if not fully built, drop `--verify` and run it once to fill the gaps. It's the infra for this LAN (its data syncs up into the main's dashboard); to see the whole network / dispatch work, go to the main or the control machine.

**④ `role=node`** → it already knows itself (marker has a name + belonging + central), **no need to ask for a role again**; to refresh manually, `register_node.py --registry-host <central>`. Normally it's passive — the hub's patrol periodically re-registers it and pushes its status.

**⑤ `role=(unknown — new machine)`** (no marker, no local registry center either) → a new machine: ask for a role, take the matching path, and **once the path completes, write the identity marker** `identity.py --set --role <角色> --central <中央地址>` so later loads recognize it directly:
  1. **Hub** — network foundation (24h always-on): registry center + remote access + write dashboard
  2. **Control** — the computer you use day to day: control the whole network (borrows the hub's registry center + holds a local mirror, resilient to dropouts)
  3. **Node** — join the network as a work node (controlled)

  > **Migrating an old control machine**: if `role=unknown` but the current directory has `myainet-network-config.md` — this is an old control machine from before the marker mechanism. Treat it as control, and conveniently write the marker too: `identity.py --set --role 主控 --central <配置里的建网机地址>`.

---

### Control mode: what to give first when you enter (no explicit instruction → live status + three options)

If the Dashboard is already running on the hub, tell the user to open it directly in a browser for live status (get the address from the last start log, or prompt them to run `python3 ~/myainet/scripts/dashboard.py`).

**When there's no explicit instruction — open by reading the whole network, laying out facts machine by machine + a menu (assessment is built into the opening; there's no longer a separate "assess" item):**

The opening **directly reads every complete field of every card in the registry center** and lays out objective facts machine by machine — reporting facts / capabilities only, no advice (advice belongs under "optimize" in ③ Other). **The "read the whole card" discipline below is exactly what the opening must do** (it used to live in a separate "assess" item, which led to a loose summary at the opening and errors in OS / disk reporting — now they're merged: the opening reads per this discipline, no two-phase "loose summary first, then separate assessment"):

> **Hard rule: read only the objective facts in the card — don't read a subset, don't fill in blanks from memory.**
> - **Storage**: look at `hardware.disks` for **all disks**, **don't use the `storage` summary** (it only includes the system disk C:, so multi-disk machines miss the large D:/E: drives). Summing: on Windows/Linux add up multiple physical disks; **macOS's `disks` are multiple volumes of the same APFS container (`total`/`avail` are identical) — dedupe by container, don't add them up** (otherwise 228G gets counted as 1.3T).
> - **OS / CPU / GPU+VRAM / RAM**: read the corresponding card fields — **don't guess the version** (if the card says `Windows 10`, don't report it as Win11).
> - **Installed models**: see `models`; **experience notes**: see `notes`; agents: see `agents` — miss none (`notes` often contains the optimal config someone actually tested or a dead end they hit; not reading it means repeating the trial-and-error).
> - **Wakeable (WoL)**: see `wake`. If a node's card has `wake.mac`, mark it **wakeable** — **whether it's online or offline** (it's a machine attribute: wired NIC armed for WoL, so it can be woken remotely whenever it sleeps). An offline *wakeable* node is asleep, not dead — wake it with `wake.py`. `wake=null` = not wakeable (WiFi laptop / Ethernet unplugged / NIC not armed).
> - "Whether it's running right now / GPU utilization" isn't in the card — for accuracy probe live with `nvidia-smi` / `ollama ps`; but **capabilities, disks, OS, installed models, notes are all in the card — you must read the card, no guessing.**

Lay it out machine by machine + a menu (**no ✅/check-mark matrix; say "can", not "should" — mismatches / idle / what to install belong under ③ optimize**):

```
🧱 win-desktop   建网机  在线  Windows10·i5·RTX3060 12G·16G·盘空216G(C56+D160)·模型 gemma4:12b·qwen3.5:9b
💻 mac-laptop    主控    在线  M1·8G·盘空33G·agent claude/codex
🖥️ gpu-2         节点    在线  Windows10·i7·RTX2070 8G·16G·盘空2.25T(C54+D342+E1858)·无本地模型
要不要—— ① 大屏   ② 任务   ③ 其他
```

- **① Dashboard** — give / help open the dashboard address (browser or phone — fine; control on Tailscale/LAN opens it directly).
- **② Tasks** — enter "task decomposition & routing" (see below): decompose → match → `dispatch`.
- **③ Other** (maintenance menu — only listed when expanded, doesn't take up space otherwise):
  - **Optimize** — **plan the division of labor across the whole network, not fill in blanks machine by machine**. Take the objective capabilities read at the opening and **divide work out complementarily — no duplication, fill the gaps**:
    - **Don't run a big model on every discrete GPU** — one runs `ollama+本地大模型`, another specializes in **image generation / audio / digital humans** (divide by VRAM and strengths)
    - **The big-disk machine** → workspace + **vector database (RAG)** + model/data warehouse
    - Fill in missing roles (if there's no storage / automation node, designate one)
    Produce a **whole-network division-of-labor diagram** (who leads what, who doubles as what, why) → then derive "what to install to achieve this division" → run `dispatch` to do it.
    > **Hard rule: every concrete choice in optimize (which local model / image gen / audio / digital human / vector DB / framework / tool) must be searched online for the current best — training-memory names are banned.** Training memory is long stale (the `qwen2.5` pitfall is exactly reporting an old name from memory); first search "current best X for <VRAM>G VRAM" before giving a model/version. Even a searched result is only a general guideline — whether it actually accelerates on a given card, which config is optimal, only real testing decides; write the conclusion into the card per the rule above.
  - **Workspace** — borrow a node's disk + GPU to work (native, no container); `ssh` straight in from this machine to use it (when this machine's disk is full / has no GPU; see 〈Remote workspace〉)
  - **Leave network X** — kick a node off (`leave_network.py`)
  - **Transfer hub** — planned hub swap → `transfer_role.py` (**the old hub auto-demotes to control**)
  - **Hub failure** — hub died → promote a node to hub (`transfer_role.py --from-mirror`, control's mirror feeds the new hub)

**With an explicit instruction, just do it** (run Y on X, leave network X, swap the hub to Y…) — no need to walk the four-item menu first. **But "just do it" = skip the menu and go straight to `dispatch`, not run work yourself with `ssh host "命令"`** — executing a task on a node still goes through `dispatch.py` (reasons in the hard rule below). The task bar shows only in-progress / waiting / failed; **completed ones auto-hide**.

**Every time you read the registry, conveniently mirror a copy locally**: `python3 ~/myainet/scripts/registry_cache.py --registry-host <central>` — this way **when the hub drops the control machine still knows every machine**, `dispatch` auto-falls back to this mirror and directly drives reachable machines (same-LAN direct, bypassing the hub); this mirror is also **the registry backup for transfers**.

---

### Control mode: task decomposition & routing

When the user issues a task, the skill executes the following logic (**the program executes, the AI decomposes and routes**):

> **Hard rule: running any work on a node = through `dispatch.py`, never run it yourself with a direct `ssh host "命令"`.**
> Only dispatch records `task:<id>` (running→done/failed + exit code + output tail) → onto the dashboard, watched-for-life by patrol, traceable. **Running directly over ssh yourself = an invisible task**: the dashboard can't see it, no one knows if it crashed, patrol can't catch it either. Even for a single command, even if dispatch feels like a hassle, use it.
> (Read-only liveness is the exception: reading cards / `healthcheck` and the like, which don't change state, may go direct; **the moment it's "execute a command / install something / start a service / train / change a file" — it must go through dispatch**. An unreachable remote node is no exception either: `ssh 它的建网机` and run dispatch on that machine, not tunnel through yourself directly.)
>
> **Hard rule: judging a node's liveness = `python3 ~/myainet/scripts/dispatch.py --node <名> --check`, never run `ping` yourself.** ping uses ICMP, and **Windows nodes' firewalls block ICMP by default** — 100% ping loss is normal and unrelated to whether the machine is alive. Judging liveness by ping alone wrongly marks a perfectly alive machine as "hard down", then triggers a pointless "recover/transfer" (a real incident that keeps recurring). `--check` uses the SSH path you actually control it through (LAN→Tailscale→hub jump box); connected = alive, can't connect = genuinely unreachable. **The moment you catch yourself about to type `ping <节点>` to judge online status, stop and switch to `--check`.**

> **Companion — when you change machine state, file a `report`:** when you (or an agent you dispatched) install/remove software, swap versions, roll back, "tried it then backed out" — these **state-changing** actions — report a line as you go:
> `python3 ~/myainet/scripts/report.py --registry-host <主IP> --node <节点> "在装新 ollama / 不行已回退"` (add `--warn` if unsure / if something went wrong).
> **dispatch records "what command ran", report records "what changed / whether it's good"** — together, the network won't spiral out of control from "another task quietly changed the machine and backed out".
>
> **One principle: write valuable experience down — in the right home by scope.**
> - **Machine-level facts** (a capability `sysinfo` can't collect — TTS / image gen / digital human / vector DB installed; a machine-level config that tested best on this box) **→ that node's card `notes`** via `report.py --node <节点> --card "…"` — useful to *any* task touching this machine; a patrol card refresh won't wipe it.
> - **Project-level experience** (one of *your projects* working through a node — its pitfalls, the best config for *that project's* pipeline, project conventions) **→ that project's own folder MD** (e.g. the remote-workspace handle's `CLAUDE.md`/`AGENTS.md`; the project agent reads it next time it works in that folder). It belongs to the project, not to myainet — don't push project knowledge into the registry.
> **You judge what's "valuable" and which home it belongs in.**
> Example: `--card "装了 IndexTTS2(TTS)；实测此卡 CPU 比 GPU 快 3-5x，别再试 GPU 加速"`.

**Step 1 Decompose the task**

Break the user's task into sub-steps, identifying the capability type each step needs. For example:

The user says: "Run a full data-science pipeline for me: download the dataset, clean it, train, deploy an API"

Decompose into:
```
子任务 #1  下载数据集       → 需要：存储 + 网络
子任务 #2  数据清洗         → 需要：自动化（Python）
子任务 #3  训练模型         → 需要：推理/训练（GPU）
子任务 #4  部署推理 API     → 需要：项目部署
```

**Step 2 Capability matching**

Against each node's capabilities (look at concrete facts: hardware + what's installed + `problems`), assign a node to each sub-task:
```
#1 下载数据集   →  nas-box      (存储节点，磁盘最大)
#2 数据清洗     →  nuc-server   (自动化节点，Python 环境)
#3 训练模型     →  gpu-rig      (推理/训练节点，VRAM 最高)
#4 部署 API     →  macbook      (项目部署节点，SSD + 高 CPU)
```

If a sub-task has no suitable node, tell the user clearly and give a fix suggestion (install a tool / expand capacity).

**Step 3 Show the execution plan, wait for user confirmation**

```
📋 执行计划（共 4 步）
  #1  nas-box     下载数据集到 /data/dataset/
  #2  nuc-server  运行清洗脚本 clean.py
  #3  gpu-rig     启动训练，预计 2–4 小时
  #4  macbook     部署 FastAPI 服务，端口 8000

顺序执行，#1 完成后继续 #2。确认执行？
```

**Step 4 Execute & update task status in real time — dispatch each sub-task with `dispatch.py`**

Once confirmed, hand each sub-task to `dispatch.py` (it does the SSH execution + writes `task:*` + echoes back; the console's ③ block reads these):

```bash
python3 ~/myainet/scripts/dispatch.py --registry-host <主IP> --node <节点> --name <任务名> "<命令>"
```

**How to decide that `<命令>` (judge by the facts card: a direct shell, or delegate to that machine's agent):**
- First look at the node's `hardware.os` → produce the right command (mac `brew` / win `winget` / linux `apt`·`curl`);
- **Fuzzy task that needs improvisation (install software, fix environment, "get X done") and the node has an agent installed (check the card's `agents`) → delegate**: write the command as a non-interactive call to that agent and hand it the goal to figure out locally — `"claude -p '下载并装好 ollama'"` / `"codex exec '...'"`;
- Clear command, or the node has no agent (a plain-SSH dumb node) → write the shell command yourself;
- Long task (training / big download) → add `--detach` — fire-and-forget to the background, automatically handed to patrol to watch its liveness;
- **Quote the whole command** (same rule as `ssh host "cmd"`, so `-h`/`-c` aren't mistaken for options and quotes aren't split apart).

**If you can't reach a node, tunnel through its belonging hub** (the node is on another LAN, or you can't reach home from outside): look at the node card's `belongs_to` field — **don't connect directly; instead `ssh 那台建网机` and run dispatch on it** (a local-LAN node's hub = the main hub; another LAN = that secondary). When running on the hub, produce commands per **its card's `os`**: posix `python3 ~/myainet/scripts/dispatch.py …`, Windows `python C:\myainet\scripts\dispatch.py …`. If reachable, connect directly — don't take detours. The unified model is **control → the node's hub → the node**; there's no "remote" special case — `belongs_to` is exactly this universal routing key.

`dispatch` moves `task:<id>` from `running` to `done` / `failed` (with exit code + output tail), reports failures faithfully, and the console task bar reflects it live. **The judgment (which machine / direct vs tunnel through hub / shell vs delegate to agent) is the AI's; dispatch is just the hands.**

---

### Control mode: supported shortcut commands

| User says | skill action |
|--------|-----------|
| "刷新" / "状态" | Give the live status again + machine-by-machine specifics (not a check-mark matrix) |
| "检查在线" | Read the status from the dashboard/registry center; for per-machine confirmation use `dispatch --node <名> --check` (a real SSH connection). **Never ping yourself** — ICMP is often firewalled and would wrongly mark a live machine as offline |
| "唤醒 X" / "wake X" | `wake.py --node X` — via X's hub, broadcast a WoL magic packet on its LAN to wake it. Only works if X's card has `wake.mac` (wired NIC + WoL armed) and X is **asleep, not powered-off**. To use a sleeping node, wake it first, then `dispatch` |
| "评估" / "优化网络" / "现在该装什么" | Read three layers of facts (hardware/environment/network speed) + each card's `problems` → machine-by-machine specifics + advice (idle hardware / missing tools / mismatch / bottleneck; AI capability split into local/cloud) = same as the opening (assessment is the opening; read the full card and lay out facts) |
| "在 X 上跑 `命令`" / "在 X 上装 Y" / dispatch a task | Task decomposition → `dispatch.py --node X "命令"` (delegate fuzzy tasks to X's agent, see above section) = opening ③ |
| "退网 X" / "X 不要了" | `leave_network.py --node X` (delete card + leave Tailscale; `--purge` also uninstalls software; `--dry-run` previews) |
| "把建网机换成 Y" / "建网机挂了换 Y" | **on-LAN**: install Y per the hub-build path first (registry center + Tailscale + bind 0.0.0.0) → `transfer_role.py --old-host 主 --new-host Y` (add `--from-mirror` if the old hub already died) → wrap up per the checklist; **the old hub auto-demotes to control** (not a node) |
| "在 X 上给我开个工作区" / "借 X 的盘当工作区" | 〈Remote workspace〉: `setup_workspace.py` creates work_dir on X's emptiest disk + self-reports an OS contract into the card (**native, no container, no Docker**); dispatch work via `dispatch --workspace`, cd after `ssh X` (see the dedicated section) |

**Patrol reminders (proactive but not naggy):** when showing status, if you find that a node's `last_seen` hasn't refreshed in a long time (**persistently** offline, not the occasional missed ping), or a whole hub is unreachable — proactively mention it and give an exit: "X 已 N 天没上线，要**退网**还是等它回来？" "主建网机连不上，要不要把职能**转移**到 Y？" (**First distinguish soft / hard down, and you must judge with a real connection via `dispatch --node <名> --check`, never on ping alone**: machine / Tailscale / SSH still up, just the service didn't start = **soft down** — SSH in remotely and `healthcheck` rescues it; whole machine off / disconnected = **hard down** — the node isn't on Tailscale, unreachable from outside, so you can only **go back to the LAN** and transfer the hub to a node. ⚠️ **`--check` showing ✅ means it's alive, even with 100% ping loss** — Windows blocks ICMP by default, ping loss never equals hard down. Before concluding "hard down", `--check` must also fail to connect.) **Only raise it on a persistent signal, say it once and clearly, don't nag repeatedly;** don't mention transient jitter or brief offline.

---

## Hub-build path: lay the network foundation

> **To build a hub, run this one deterministic script `setup_hub.py` — don't hand-stitch commands step by step (you'll miss steps, you'll write wrong-OS commands).** It runs in order "registry center → identity → SSH → dashboard+patrol → register self → Tailscale" and **self-checks each step, reporting faithfully at the end**; idempotent, can be re-run repeatedly.

### One-command build (the main path — do this)

**First**: confirm the machine's conditions (can stay on 24h, has a fixed LAN IP or can be set static, RAM≥4G, storage≥20G), and **ask the user's operating system** (commands are chosen by OS).

**Run setup_hub**:

macOS / Linux:
```bash
python3 ~/myainet/scripts/setup_hub.py
```

Windows (first switch the console to UTF-8, otherwise Chinese/emoji show as garbled; **pick one of the two by your actual shell** — `$env:`/`[Console]::` is PowerShell syntax, cmd doesn't recognize it):

PowerShell:
```powershell
chcp 65001 > $null; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; python $env:USERPROFILE\myainet\scripts\setup_hub.py
```

cmd:
```cmd
chcp 65001 >nul && python %USERPROFILE%\myainet\scripts\setup_hub.py
```

**It does, in order**: ① start the registry center (27182) ② write identity ③ enable SSH (pops one admin authorization) ④ start dashboard+patrol (skipped if already running) ⑤ register self ⑥ install Tailscale + `tailscale up` (**it downloads and installs for you; only "log into your own account in the browser" is on you**) ⑦ self-check.

**Act on the conclusion of ⑦, don't declare success for it**:
- `✅ 建网机建成：… 全部就位` → done, do the two wrap-ups below.
- `❌ 没建完，还差：X` (non-zero exit code) → **fill in the missing items**: the most common are Tailscale not logged in (run `tailscale up` and authorize in the browser), SSH not enabled. After filling, re-verify with `setup_hub.py --verify` until ✅. **Don't say "built" before reaching ✅.**

**One wrap-up (setup_hub doesn't do it — run it once manually)**: **Tailscale proxy bypass** (prevents `100.x` from being swallowed into a 502 by the proxy): `python3 ~/myainet/scripts/tailscale_proxy_bypass.py` (with Clash/Surge, also add `100.64.0.0/10 DIRECT` to their rules).
> Setting up keys doesn't need a separate run — setup_hub's registration step already **auto-publishes + installs the public key** (including the Windows admin account's `administrators_authorized_keys`, with ACL set automatically).

When done, **tell the user the hub is ready**, give the connection info (LAN IP / Tailscale IP / `ssh 用户@IP`); next, run this skill on each node and choose "node" — **nodes need zero input, broadcast auto-discovers this hub, no IP needed**.

---

> **The manual-equivalent commands for each setup_hub step (only for troubleshooting when a specific step fails; don't run them manually under normal conditions) → see `references/build-manual.md`.**

## Multiple LANs (optional: main hub / secondary hub)

**Only one LAN (all machines in one place) → skip this section**, just install one hub per above.

When machines are spread across **multiple LANs** (home + office + …, each behind its own NAT, mutually unreachable over LAN): **place one hub on each LAN**, joined into a mesh by Tailscale.

- **Main hub** (one per network): standard hub-build flow (registry center + dashboard + Tailscale + SSH). Runs the **only dashboard**, aggregates the whole network, and the control machine connects to it.
- **Secondary hub** (one per additional LAN) = **the slim version of a hub**: like the main, it starts a local registry center + SSH + Tailscale, **but doesn't start a dashboard**, and **syncs its local registry center up to the main**. Installing it is one line:

```bash
# 在次建网机上跑（--main 填主的 Tailscale 地址，或 'auto' 让它在 tailnet 上自己探主）
python3 ~/myainet/scripts/setup_hub.py --main <主的Tailscale地址>
```

That's setup_hub's secondary mode: it starts `registry_server --main-host <主>` (local registry center + a sync thread) + enables SSH + installs Tailscale, and does not start dashboard/patrol.

> **Fill in the address, avoid `auto`**: all registry centers across the network are on 27182, and main and secondary answer identically on the network. `auto` (probing the tailnet for who's main) only recognizes "the first machine answering on 27182 that isn't itself" — **it's only stable with one main + this single secondary**; with **multiple secondaries**, auto might mistake another secondary for the main, so with multiple secondaries **honestly fill in the main's Tailscale address**.

**Key point: a secondary is just a "shrunken main", not a separate mechanism —**
- **No distinction for nodes** — local nodes still **broadcast for the local hub and register** (main or secondary — the node has no idea), `register_node` is unchanged and doesn't touch Tailscale;
- **Sync replaces the old "bridge + push"** — the moment the secondary's local registry center has a write (node/pubkey/status) it **batch-syncs to the main**; and it **pulls down the main's control-side public key** to install on local nodes (remote nodes get the control key installed too and are reachable by control passwordless). The main holds the union of the whole network and runs that single dashboard;
- **Dispatch via `ssh 次`** — for the control machine to use a remote node, `ssh 次` and then `dispatch` locally on the secondary (which is connected to that LAN). To let the control AI auto-pick the right secondary, add `--belongs-to <次名>` to the node at registration (the routing key; optional — you can also specify manually);
- **The local LAN is self-contained** — if the main / Tailscale temporarily disconnects, the local network keeps running, buffering, and catches up via sync once reconnected.

**Cross-LAN execution:** when the control machine wants to run a command on a remote node, it doesn't tunnel through itself; it does **one hop `ssh 次` and runs `dispatch.py --node <本LAN节点> "命令"` on the secondary** (which is already connected to its LAN's nodes) — use the existing dispatch, no two-level hop.

> **Remote nodes' online status**: the main can't ping other LANs' intranet IPs and gets no real-time liveness (the slim secondary doesn't run patrol). So for synced-up remote nodes, the main dashboard marks **"已注册（远程·未探活）"** (an amber dashed node on the topology), **doesn't wrongly mark them offline, and doesn't count them as offline** — at sync time the secondary stamps the card with `synced_from` as the source, and the dashboard uses that to separate "remote and unprobable" from "locally really dead". For more real-time status, add lightweight liveness on the secondary, but keep it minimal by default.

---

## Monitoring: patrol (`patrol.py`) + process watch (`watch_job.py`)

The hub's patrol engine is **`patrol.py` (resident)**, doing two things each round: ① probe liveness on this LAN and report online/offline (ping + SSH-port fallback, accurate even when ICMP is blocked) (with multiple LANs, push `status:*` to the main, see the previous section); ② also check the processes you registered to watch. Below covers how to use "watch a process".

Hand an **already-running script/process** to the hub to watch: if the process crashes, the dashboard sees it; closing your laptop doesn't matter — what watches it is the resident hub, not you.

**① Register from control** (on any machine that can reach the main registry center):

```bash
# 按进程名匹配，最省事（不用记 pid）
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --node <节点名/IP> --name 夜间训练 --match "train.py"
# 或按 pid 精确盯
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --node mac-studio --name 渲染 --pid 4321
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --list             # 看登记了哪些
python3 ~/myainet/scripts/watch_job.py --registry-host <主建网机IP> --unwatch 夜间训练   # 撤
```

**② The hub's patrol watches automatically**: `patrol.py` checks each round whatever is registered and still `running`, writes liveness into `task:*`, visible on the dashboard. **The main hub (the single one in a single-LAN setup) resides `patrol.py`** (the slim secondary doesn't run it; remote nodes stay minimal, see 〈Multiple LANs〉):

```bash
# 主建网机 / 单局域网那一台：--registry-host 填 127.0.0.1（只查它够得着的节点）
nohup python3 ~/myainet/scripts/patrol.py --registry-host 127.0.0.1 > ~/patrol.log 2>&1 &
```

It only checks work on **nodes it can reach** (this machine directly, this LAN's nodes over SSH; out-of-reach ones are left to that LAN's hub). If it can't tell (SSH down / no definite result) it **leaves things as is, never wrongly marks `stopped`**.

**Both posix and Windows nodes are supported**: posix uses `kill -0` (pid) / `pgrep -f` (command-line match); Windows runs PowerShell over SSH — `Get-Process -Id` (pid) / `Get-CimInstance Win32_Process` matched by command line, transmitted via `-EncodedCommand` to bypass cmd/powershell escaping. Whether a node is Windows is judged automatically from the registration card's `hardware.os`.

**Boundary (v1)**: it only **watches** an already-running process (start it however you like, ideally backgrounded with nohup); when the process is gone it marks `stopped`, but **can't get the exit code** (for an exit code you'd need to write `$?` to a file at startup — a "launch with monitoring" helper is a follow-up).

---

## Remote workspace: borrow a node's disk + GPU to work (native, no container)

**Purpose**: a machine (often control / the one in your hands) has a full disk or no GPU, while another node on the network has a big disk / a GPU — set that node as a "workspace", `ssh <别名>` in one step from this machine, and work using its disk + GPU; data lands on that node's disk, with zero usage on this machine.

**When to set one (don't over-set)**: only set it when you'll **continuously / interactively** occupy that machine (going in repeatedly, files staying long-term in `work_dir`, as a remote dev box). For a **one-off task** (run one command, one training run) just `dispatch --node X "命令"` — **no workspace needed**. **Never set one automatically** — there are only two triggers: you explicitly say "open a workspace on X", or the control-mode opening (reading all cards) finds "this machine's disk full / no GPU + some node has spare" and proactively proposes one.

**Why SSH into the node to work rather than mount its disk back locally**: cross-OS filesystems + cross-LAN latency make it slow and fragile (NTFS has no Unix permission bits, network-mounted symlinks break easily). Instead, "work locally on the node, only SSH the *terminal* over" — disk and compute stay local to the node, only keyboard echo travels the network.

**Native = use that machine's OS / python / GPU, no runtime wrapping.** Three benefits in return:
- **Control can deploy remotely from an empty machine** — deployment is all an operate layer SSH can run, with no "install a runtime first" wall;
- **Direct GPU** (Win/Linux native CUDA, Mac native Metal, no virtual layer — more direct than any isolation layer);
- **Goes with myainet's grain** — the network is heterogeneous by nature, and `dispatch` already produces commands by the node's `os`. The cost: the workspace is **that machine's native environment** (Windows gives you PowerShell, not bash) — which is exactly why ③ "read the contract + go through dispatch" below is a hard prerequisite, so the agent doesn't err across systems.

**Flow** (same shape on all three systems; differing paths / shells are handled by `dispatch`):

**① Pick a disk** — read that machine's card `hardware.disks` (each disk `{mount, total_gb, avail_gb}`, self-reported by sysinfo: Win lists drive letters, posix lists real mounts), pick the one with the largest **`avail_gb`** for `work_dir` (don't default to the system disk). Example: Win's D: with 160G free ≫ C: → `--dir D:\myainet-ws`.

**② One-line deploy** (control runs it remotely against the node — operate layer, zero runtime install):
```bash
ssh <节点> "<卡里的 python> ~/myainet/scripts/setup_workspace.py \
  --dir <选好的盘路径> --registry-host <建网机IP> --node-name <节点名>"
```
It does three things: create `work_dir` → write the self-report marker `~/.myainet/workspace.json` → trigger `register_node` to self-report into the card. **One command, remote, no wall.**

> **Cross-LAN nodes** (control can't reach it directly): replace `ssh <节点>` with `ssh -J <它的建网机> <节点>`, or run directly on its hub (same `belongs_to` routing as 〈Control mode〉; **don't connect directly to a remote node**).

**③ The OS contract self-reports into the card (key — don't skip)**

After deploy, that machine's card `workspace` field carries a **complete OS contract**: `os` / `shell` / `work_dir` / `python` (the exact interpreter) / `gpu` / `host_access` (GPUs the machine can use directly, `ollama` etc., native at localhost) / `state` (disk + GPU usage). `assemble_network` / the console / `dispatch` read the card and know "this machine has a workspace + its OS contract".

> **Hard rule: the agent never guesses the OS.** `/work` or `D:\work`, `python3` or `python` — **all read from the contract in the card, never assumed**; **all operations go through `dispatch`** (which produces posix/Windows commands by the node's `os`). This is part of the "machines self-report into the card, read the card don't guess" doctrine, and **the only guarantee that a native workspace works across systems without error.**

**④ Enter / dispatch**
- **Enter**: `ssh <节点>` drops into its shell, `cd <work_dir>`. For one-step entry, add a `RemoteCommand` to an alias in this machine's `~/.ssh/config`:
  - posix node: `cd <work_dir> && exec $SHELL`
  - Windows node: `cmd /k cd /d <work_dir>`
- **Dispatch**: always `dispatch --workspace` — it reads the card's `work_dir` and **auto-cd's by `os`** (the agent doesn't hand-write cross-OS paths):
  ```bash
  # 解释器用卡里的 workspace.python（确切路径），别裸 python —— Win 上未必在 PATH，合上面铁律
  python3 ~/myainet/scripts/dispatch.py --node <节点> --workspace --name 训练 "<workspace.python> train.py"
  ```
  → dispatch runs inside that machine's `work_dir`, using its disk + GPU. **The agent gives intent, the cd is left to dispatch, the interpreter is read from `workspace.python`.**
  > dispatch's auto-cd follows the node's **default SSH shell** (Win defaults to cmd, uses `cd /d`); don't change a Windows node's default shell to powershell, or `cd /d` syntax won't match.

**④b Local handle (for Desktop's Claude/codex)** — those apps can only pick/create a **local** folder as the workspace when opening a session; they can't open a remote workspace directly. The approach: **the user picks (or creates) a local folder on Desktop as the workspace and opens a session in it; you work inside that folder**:
```bash
python3 ~/myainet/scripts/setup_workspace.py --handle <远程节点>   # md 默认落【当前目录】=用户选定的工作区
```
It writes `CLAUDE.md`/`AGENTS.md` **into the current folder** (contents = that remote workspace's OS contract: node/work_dir/os/shell/python/gpu/ssh + how to dispatch). This way **the user's chosen folder now has the md** → the agent reads it automatically when opened → knows the real work is on the remote and goes there to do it (`dispatch --workspace` / `ssh`). **Locally there are just these two extra md files, taking no space.** (To land them elsewhere, use `--at <路径>`.)

**⑤ git**
- `work_dir` is a real folder on that disk — the node's **bundled git** operates against it directly;
- if git isn't installed, on that machine `pip install dulwich` (a pure-python git, zero system dependencies, works on all three platforms).

**Notes**
- **Data safety**: `work_dir` is a real directory on disk — **no container to delete, inherently no loss.**
- **Tear down the workspace**: delete the node's marker `~/.myainet/workspace.json` (and `work_dir` too if you want) + re-register → the card's `workspace` becomes `null`, and the whole network knows it's no longer a workspace.
- **Native means that machine's environment**: a Win workspace is PowerShell + Windows paths, Mac/Linux is bash/zsh — precisely because of this, ③ "read the contract + go through `dispatch`" is not a suggestion, it's a prerequisite.
- **Connectivity jitter**: over the Tailscale DERP relay, `ssh <别名>` occasionally times out — just retry (it's not a real disconnect).

---

## Node path: scan & register

### Step 1 Confirm the target machine

By default analyze this machine. Ask: this machine or remote? A custom node name (optional, defaults to hostname)? What's the hub's IP?

If you need remote collection (without installing the skill on that machine): feed `sysinfo.py` over to run (works on all three platforms, no bash needed):
```bash
ssh [-p PORT] USER@HOST python3 - < scripts/sysinfo.py
```

### Step 2 Collect hardware data

Run `scripts/sysinfo.py` (a Python script, works on macOS / Linux / Windows, no bash / WSL needed):

```bash
python3 scripts/sysinfo.py
```

Outputs key=value format, collecting: CPU model/core count, GPU model/VRAM/framework, RAM, storage total/available/type, intranet IP, OS, installed tools (git, python3, node, ollama, opencode, etc.), whether it's an always-on device, network connectivity and latency.

### Step 3 Four-dimension assessment

Assessment principle: **look at potential, not just the current state.** A missing environment (no Python, full disk) doesn't mean it can't be used — give the steps to fix it, then re-assess.

> **The specific product / model / framework names in the tables / examples below are just illustrative and may be stale — when actually recommending, the AI searches online for the current best; don't copy the names from this document.** Stable runtime entry points (Docker / Python / git / Ollama itself) can be used directly; the fast-churning ones (models, object storage, vector DB, automation frameworks, fine-tuning tools) must be searched for the current best.

---

#### 一、🧠 Can it run a local LLM?

Check GPU VRAM and RAM, **search online for the currently newest available open-source models** (HuggingFace / Ollama model library), and give concrete recommendations:

| Hardware | Conclusion | Concrete advice |
|---------|------|---------|
| NVIDIA/AMD GPU VRAM ≥ 24GB | ✅ Can run large-parameter models | Search online for the current recommended model and quantization for this VRAM, give the `ollama pull <model>` command |
| NVIDIA/AMD GPU VRAM 8–23GB | ✅ Can run mid-size models | Same as above, give specific model and quantization per VRAM |
| NVIDIA/AMD GPU VRAM 4–7GB | ⚠️ Can run small models | Same as above, note the speed limit |
| Apple Silicon (unified memory) ≥ 32GB | ✅ Metal acceleration | Search online for the current models best supported by Metal |
| Apple Silicon 16–31GB | ⚠️ Small/mid models | Same as above |
| No GPU, RAM ≥ 64GB | ⚠️ CPU inference (slow) | Note the per-token time, suggest running only small models |
| No GPU, RAM < 32GB | ❌ Not suitable for local models | Suggest going with a cloud API |

Recommended tool: Ollama (port `:11434`) install command: `curl -fsSL https://ollama.com/install.sh | sh`

**Online search action**: once you have the VRAM/RAM data, search "best open source LLM [VRAM]GB 2025" or "ollama models [VRAM]GB", take the newest results with high download counts and good reviews and recommend them to the user; don't use a hardcoded list of old model names.

If it's suitable for big models, also assess whether it's suitable for **training/fine-tuning**: VRAM ≥ 16GB → can run LoRA/QLoRA (search online for the current efficient fine-tuning toolchain); VRAM ≥ 40GB → can do full fine-tuning.

---

#### 二、💻 Suitable for deploying local projects?

| Condition | Conclusion |
|------|------|
| CPU ≥ 8 cores + RAM ≥ 16GB + SSD | ✅ Suitable: web services, Docker containers, data-processing pipelines |
| CPU ≥ 4 cores + RAM ≥ 8GB | ⚠️ Suitable for lightweight projects: small APIs, script services, static sites |
| CPU < 4 cores or RAM < 4GB | ❌ Not suitable for running projects, suitable as a script node |

Environment check & fix:
- No Docker → `curl -fsSL https://get.docker.com | sh`
- No Python → `brew install python3` / `sudo apt install python3 python3-pip`
- No Node → `brew install node` / `curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo bash -`
- No git → `brew install git` / `sudo apt install git`

A node suitable for deploying projects is also a **test node**: use it to run unit tests, integration tests, CI jobs — let the coding node push code here for automatic testing after committing.

---

#### 三、💾 What's the storage situation?

Compute storage from the card's `hardware.disks` **all disks** (each `{mount, total_gb, avail_gb}`) — **largest free disk = storage landing point**; total: **add up multiple physical disks on Windows/Linux**, **macOS's `disks` are multiple volumes of the same APFS container (`total`/`avail` all identical) — dedupe by container, don't add them up** (otherwise a single 228G gets counted as 1.3T). **Don't just look at the `storage` summary** (it only includes the system disk C:, so multi-disk machines miss the large D:/E: drives and get wrongly judged as "small disk / no storage node"):

**Plenty of free space (free > 30% of total)**:
- Total ≥ 1TB → recommend as a storage node: set up **object storage** (store model weights/datasets) + **vector database** (RAG knowledge base) — **search online for the current mainstream choices**, give install commands per that.
- Total < 1TB → as a regular node, storage isn't an advantage

**Tight free space (free < 20% of total)**:
- First analyze large files:
  ```bash
  # macOS
  du -sh ~/* | sort -rh | head -20
  # Linux
  du -sh /home/*/* 2>/dev/null | sort -rh | head -20
  ```
- Give concrete cleanup advice (list of large files, cache directories safe to delete)
- Re-assess after the user cleans up; if the freed space qualifies, recommend a storage node

---

#### 四、⚙️ Fallback: script / automation / test node

Any machine that can get online is suitable, even with just 2 cores / 4GB:

- **Automation orchestration**: set up an automation orchestration tool (**search online for the current mainstream choice**), or Python cron scripts
- **Data collection**: Python + requests/playwright, suitable for scheduled scraping, API polling, data cleaning
- **Lightweight testing**: run unit tests, interface tests, no big compute needed
- **API relay**: do request forwarding / load sharing for other nodes

When the environment isn't met, just give the install commands; ready to use once installed.

---

### Step 4 Potential conclusion & action list

Summarize the assessment results, explained in natural language:

1. **What this machine can do now**
2. **What it can also do after doing the following** (list specific commands or steps)
3. **The role(s) it's recommended to take in the myainet network** (can be multiple)

Example output:
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

### Step 5 Output the ASCII node card

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

### Step 6 Register to the registry center & save the archive

**First auto-enable SSH (a node must be controllable by the hub/control — don't assume it's already on)**:

```bash
python3 scripts/enable_ssh.py   # 三平台通用、幂等；需管理员（会提示输一次密码）
```

Then run `scripts/register_node.py` (you can also use `register_node.py --enable-ssh` to do "enable SSH + register" in one step):

```bash
# 同一局域网：不用给 IP —— 它自己广播找建网机（discover.py）。这才是「不输 IP 就入网」。
python3 scripts/register_node.py
# 跨局域网 / 广播被挡（个别 AP 隔离的 WiFi）/ 自动没找到：才手动给地址
python3 scripts/register_node.py --registry-host <建网机IP 或 Tailscale 地址>
```

On successful registration it outputs: `✅ 节点 <hostname> 已注册到 myainet（长期保存）`, and prints `🔑 换钥匙：装入 N 把控制方公钥`.

> **Passwordless SSH is welded into registration — usually no separate setup** — `register_node` publishes/installs public keys right after a successful registration (the node installs the control + hub public keys, zero password, replacing `ssh-copy-id`; a Windows admin account automatically gets `administrators_authorized_keys` written with the ACL set). Run it separately or for troubleshooting: `python3 scripts/keysync.py --role node` (likewise auto-discovers without an IP).

Registration is a long-term archive by default, with no TTL. Whether a node is online is detected at Dashboard refresh via LAN ping, Tailscale ping, SSH, etc. Only a temporary node passes `--ttl <秒数>`.

Markdown archive format `<hostname>-node-card.md`:

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

## Control path: view the whole network & generate the dispatch config

Control is used to view the entire myainet network's state and generate a network dispatch config that can be loaded into any AI tool.

> **Core hard rule: control only reads the central registry center, never scans machines.**
> Each machine **already collected** its hardware / agents / models **at registration time**, all in its `node:*` card. Control does just three things: **read cards → display → produce dispatch config**.
> - ✅ **Reading the registry center = `assemble_network.py`** (raw socket over Tailscale, zero dependencies): CPU / GPU / memory / storage / agents / models all fetched at once. This is the **only correct way** for control to get whole-network info.
> - ❌ **Never SSH into a registered machine to run `sysinfo` / PowerShell and scan hardware live.** It's all in the card — scanning is redundant; and you'll hit pitfalls for sure — when the hub is Windows `python3` isn't found (it's called `python`), a Chinese-locale GBK encoding crash, a PowerShell timeout, all suffering for nothing. **The moment you catch yourself wanting to "connect in and look at the hardware", stop and go read the registry center.**
> - Want **fresh data** for a machine → not for you to scan, but to have **that machine itself** re-register (see the optional step below — it runs `register_node` locally to self-collect and write back to the center).

### Step 0 Control-side Tailscale check

The control role must have Tailscale to connect to the hub from outside the LAN. After entering the control path, proactively check first — don't just write a todo:

```bash
command -v tailscale || command -v /usr/local/bin/tailscale
tailscale status || /usr/local/bin/tailscale status
tailscale ip -4 || /usr/local/bin/tailscale ip -4
```

**If not installed, install it — the install is identical to the hub/node, by system not by role (see 〈Hub-build path Step 3〉):** macOS/Linux go via CLI + system service (`install-system-daemon` / systemd), Windows goes via the official App (the only way, silent-installable). **Never use userspace** — it builds no network adapter, has no service supervision, and dies on roaming/reboot, which a control laptop especially hits; macOS's `install-system-daemon` uses the classic utun and doesn't even need the system-extension GUI — the skill runs the command + you enter a `sudo` password once + log in once in the browser. After installing, run `python3 scripts/tailscale_proxy_bypass.py` once (Clash etc. would block `100.x`).

Once control has its own Tailscale IP, continue.

### Step 0.5 Confirm the connection method

Ask the user: are you on the same LAN as the hub right now, or on an external network (needing a Tailscale connection)?

- **On the LAN** → use the hub's LAN IP directly
- **External network** → use the hub's Tailscale IP

Once the hub IP is confirmed, verify the link. **A real SSH connection is the only authoritative judgment** (if the hub is Windows, ICMP is blocked by default and ping will fail, but that doesn't mean it's unreachable):

```bash
# 先这条——连得上就是通了，ping 通不通都不影响：
ssh -o BatchMode=yes -o ConnectTimeout=8 user@<建网机IP> hostname
# 下面两条仅作辅助参考，失败（尤其 Windows 建网机）很正常，别据此判「不通」：
ping -c 2 <建网机LAN_IP>
tailscale ping <建网机Tailscale_IP>
```
> ⚠️ If ssh works, move on — **don't stop just because ping fails** — this is exactly the pitfall hit repeatedly today (ping loss mistaken for "hub unreachable").

### Step 1 Control self-reports into the network + (optional) refresh node cards

**Control self-report** (registers itself + **conveniently publishes the control public key to the registry center** for the hub/nodes to install passwordless — no need to run keysync separately):
```bash
python3 ~/myainet/scripts/register_node.py --role 主控
# 同局域网自动发现建网机；跨网加 --registry-host <建网机 Tailscale 地址>
```

**Refresh node cards only when stale** (skip if the cards are fresh — go straight to Step 2): have that node **re-run the skill itself**, or use `dispatch` to trigger its `register_node`. Normally **the hub's patrol auto-re-registers this LAN's nodes every ~1 hour**, so you usually don't need to bother. The principle is unchanged: **let the node self-collect, not control scanning remotely.**

### Step 2 Read all registered nodes

Run `scripts/assemble_network.py`:

```bash
python scripts/assemble_network.py \
  --registry-host <建网机IP> \
  --registry-port 27182 \
|| python3 scripts/assemble_network.py \
  --registry-host <建网机IP> \
  --registry-port 27182
```

### Step 3 Show the raw network state + role suggestions

Show each node's **hardware + actually-installed agents/tools** (facts only, no scoring, no auto-assigned roles); control's AI judges by facts and the user confirms:

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

After showing them, the skill gives role-assignment suggestions, for example:

```
📋 建议角色分配（4 类能力，按事实给理由、不打分；一台可兼任）：
- <节点A>：🖥️ 本地算力(GPU)（RTX3060 12G → 本地推理/生图；训练吃力）
- <节点B>：☁️ 云端 AI（装了 claude/codex + 够得到 API → 编程/云端推理，不吃本地硬件）
- <节点C>：⚙️ 通用·自动化（CPU 多核 + 常驻 + python/docker → 脚本/定时/测试）
- <节点D>：💾 存储（盘大）

是否按此方案生成网络配置？可以调整任意节点的角色。
```

**How to judge roles (read three layers of facts, no scoring, no algorithm written — the AI judges; one machine can double up):**

Role = **4 capability categories**, treated equally by capability regardless of which LAN it's on. **Core principle: a role looks only at the "capability ceiling" (stable facts), not current usage** — full or not / busy or not is [state], a "current problem", belonging to optimize advice + patrol, and **doesn't change the role**.

| Capability role | What to look at (ceiling) | Tasks it can take |
|---|---|---|
| 🖥️ **Local compute (GPU)** | GPU + **VRAM** (tiered: small = inference / mid = image gen / large = training) | local inference / image gen / training |
| ☁️ **Cloud AI** | has an agent + has network + can reach the API (**unrelated to local hardware** — a weak machine works too) | coding / cloud inference |
| 💾 **Storage** | **large total disk capacity** (empty or not is state, not judged here) | store models / data |
| ⚙️ **General · automation** | CPU + always-on + runtime (python/docker) | scripts / scheduled / tests / light services |

Feed three layers of facts into this table: ① hardware → local compute/storage; ② environment (agents/cli) → cloud AI/automation; ③ network speed → only affects "fit for serve/download", not an independent role. The other axis is the topology role (control/hub/secondary hub/node; the `always_on` ones are hub candidates).

**Optimize advice = plan the division of labor across the whole network, not nitpick machine by machine; only raise what "holds the moment the facts are laid out", and give each one an exit:**

- **Capability gap** (not "idle"): has a GPU but no runtime installed (ollama) → local compute is **simply unusable**, that's a gap. **An idle GPU is itself normal, not waste** — don't cram work in just because it's "not maxed out".
- **Big-picture division**: multiple discrete GPUs **shouldn't all install the same thing** — divide by VRAM and strengths (one runs a local big model, another does image gen / audio / digital humans); the big-disk machine → workspace + vector DB + data warehouse; fill a missing role (storage / automation) with a machine.
- **Missing a key tool**: wants to be a certain role but lacks the runtime (needs to run a python project but has no python) → propose installing it.
- **Mismatch**: a weak machine forced to do heavy work (don't dispatch a local big model to an 8G machine, give it cloud instead).
- **Bottleneck**: a node on cellular / disk nearly full / RAM tight → remind (e.g. "主控盘只剩 33G，数据往大盘那台放").

> Concrete choices (which model / image-gen·audio·digital-human tool / vector DB / framework) **must all be AI-searched for the current best**, don't report old names from training memory.

These all get written into the **node card's `problems` field** (`register_node` auto-derives the obvious ones from facts + the AI assessment adds role-related ones, refreshed on re-register). **Before dispatching a task, first look at the target machine's `problems`, fix what you can (install tools / clear disk / install agent / install ollama), so the task can actually run** — capability says "can or can't", `problems` says "what it's stuck on now"; align both and the task lands.

**Card updates + execute by reality:** the card is a snapshot, it goes stale — the hub's `dashboard` self-refreshes every 30min; nodes are periodically triggered to re-register by the hub's `patrol --refresh-every` (default ≈1 hour), and control can also say "re-register X" to refresh on demand (refresh a machine right after installing something on it). **But planning is planning, executing is executing: at the moment of dispatch, assign by [real state] — pre-flight check whether the target machine is online right now, whether the needed tools/disk are really sufficient, then decide whom to dispatch to. `problems` may go unsolved (it's only advice, not a hard gate), but execution only honors reality.**

Wait for the user to confirm or modify, then proceed to Step 4.

### Step 4 Generate the myainet network config

After the user confirms the role assignment, generate `myainet-network-config.md`, saved in control's current directory:

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

After generating, tell the user:
- Paste the contents of `myainet-network-config.md` into any AI tool's system prompt to let that AI dispatch the network directly
- Next time you run this skill it will auto-read this file, so you can directly issue commands to control each machine

---

## Notes

- **No need for redis / redis-cli / any pip package**: the registry center is the bundled `registry_server.py` + `registry_client.py` (pure standard library, raw socket RESP). Don't install the `redis` package on any machine — it's not used.
- **registry center not deployed**: first run the hub-build path on the hub to complete installation
- **Node offline detection**: `node:*` is a long-term registration archive, it doesn't auto-disappear on TTL; online/controllable status is detected live by the Dashboard. A temporary node may explicitly pass `--ttl <秒数>`.
- **GPU detection order**: nvidia-smi → rocm-smi → system_profiler (macOS) → lspci
- **Windows hub**: the registry center is a bundled python script (`registry_server.py`, no install no Docker), SSH uses the built-in OpenSSH Server, Tailscale has a native client — it can fully take on the hub role
- **Windows node path**: just run `python3 scripts/sysinfo.py`, no WSL needed, natively supported on all three platforms
- **Network connectivity**: at collection time, detect whether `api.anthropic.com` is reachable, and note inside/outside the firewall on the card

---

## Cross-platform notes (Win / Mac / Linux)

The pitfalls of each system when orchestrating across machines — **encoding is the easiest to hit** (Chinese Windows especially).

### Encoding / UTF-8 (key point)

- **Standardize UTF-8 at the top of entry scripts**: `register_node.py` / `dashboard.py` / `dispatch.py` / `patrol.py` all start with `os.environ.setdefault("PYTHONIOENCODING","utf-8")` (+ `PYTHONUTF8="1"`) and `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`. **Copy this block into newly written scripts**; sub-scripts (like `sysinfo.py`) output UTF-8 by inheriting this set of env vars from the parent process.
- **Capturing subprocess output must explicitly set `encoding="utf-8", errors="replace"`**: `subprocess.run(..., capture_output=True, text=True)` on **Chinese Windows (GBK / cp936 locale)** decodes the subprocess's UTF-8 output as GBK, and on hitting Chinese (app names, CPU strings, etc.) raises `UnicodeDecodeError` and `stdout` becomes `None`. **This is the root cause of the Windows hub crashing on first registration.** Anywhere you capture another script's / command's output must carry `encoding="utf-8"`.
- **Printing emoji**: characters like `✅`/`⬜`/`🔍` can't be encoded by the GBK console (`UnicodeEncodeError`), saved by the `reconfigure` at the top; code not covered will crash on that `print` line.
- **Seeing garbled Chinese / emoji over SSH ≠ corrupted data**: the Windows console spits GBK bytes, which become garbage at your UTF-8 terminal — **a display issue, not a storage issue.** To check real vs fake, don't trust your eyes: read the value in the registry center and look at the **code points** (`s.encode("unicode_escape")`); `建网机` is the real "建网机".
- **Storing/reading Chinese in the registry center**: `registry_client.py` is UTF-8 end-to-end over the raw socket, lossless round-trip (the `registry_server.py` side stores the value as a BLOB, byte-level lossless). **Don't use `redis-cli` to transmit Chinese on Windows** — argv / console codepage will garble it; only the raw-socket path is clean.

### SSH / shell

- **Windows OpenSSH's default shell isn't fixed**: it could be `cmd.exe`, or `bash` (when Git Bash is installed). **Probe before issuing commands** (`echo $SHELL`, or run one and look at the error first), don't assume cmd. Path notation follows: cmd uses `%USERPROFILE%`, bash uses `$HOME` / `~`, Windows native backslash `C:\...` vs Git Bash forward slash `/c/...`.
- **Running PowerShell over SSH**: for complex commands use `-EncodedCommand` (base64 / UTF-16LE) to bypass cmd/powershell escaping + encoding pitfalls (`patrol.py` does this when watching processes).

### Command names / paths

- **Python invocation name**: Windows is usually `python` or `py -3`; Mac/Linux is `python3`. Scripts run subprocesses with `sys.executable` internally (self-adapting), but when you **manually** type a command, try all three.
- **Already handled per-platform (just know it, don't change it)**: `ping` (Win `-n -w` / Mac `-c -t` / Linux `-c -W`); always-on determination = no battery means desktop (Mac `pmset` / Win `Win32_Battery` / Linux `/sys/class/power_supply`); local model scanning (ollama / LM Studio `~/.lmstudio` / HF cache `~/.cache/huggingface`, all via `Path.home()` self-adapting).

### Deployment / network

- **registry center**: `registry_server.py`, native python on all three systems, no install no Docker; **bind `0.0.0.0`**, otherwise LAN / Tailscale can't reach it.
- **Tailscale CLI location**: the macOS App-version wrapper is at `/Applications/Tailscale.app/Contents/MacOS/Tailscale` or `/usr/local/bin/tailscale` (the Homebrew CLI may not connect to the App); Win / Linux are on PATH.
- **Proxy interference**: on Mac, Clash etc. (7890/7892) break IPv6 and some CLIs' networking; Tailscale uses its own `100.x`, and when a direct hit fails it falls back to the **DERP relay** (slow but works). `tailscale_proxy_bypass.py` already handles the `100.x` bypass.

---

## Script reference

The scripts are all flat under `scripts/`, grouped by responsibility into four sets:

**Core / shared**
- `registry_server.py` — **zero-dependency registry center** (replaces Valkey/Redis): sqlite + a standard-library RESP server, listening on 27182. The hub runs just this one python process, **native on all three systems, no install, no Docker/WSL**; cards land in `~/.myainet/registry.db` (WAL, no loss on crash/restart), implementing only the SET(+EX)/GET/MGET/KEYS/DEL that myainet uses
- `registry_client.py` — zero-dependency registry-center client (speaks RESP over a raw socket, auto-reconnects once on a transient drop); all reads/writes to the registry center across the project go through it, and a node **needs nothing installed**. It connects to `registry_server.py` (RESP is just the wire format, not Valkey/Redis)
- `sysinfo.py` — collects this machine's hardware + actually-installed agents/tools (Python, works on all three platforms); remote collection `ssh USER@HOST python3 - < sysinfo.py`
- `identity.py` — **machine-level identity marker** `~/.myainet/identity.json` (role / central / name / belongs_to). The skill's "step one" runs it to judge identity (hub / control / secondary / node / new machine) — **identity is machine-level, not directory-bound**; the marker takes priority, with the local registry center (27182) being up as a fallback to judge hub. Each path writes one at the end with `--set`
- `registry_cache.py` — **control's local registry mirror** `~/.myainet/registry-cache.json`. When control reads the whole network it stores a copy of the raw cards; **when the hub drops** dispatch falls back to it and directly drives reachable machines, and it's also the **transfer backup** (if it can't read, it doesn't overwrite, preserving the last good mirror)

**Build / access**
- `setup_hub.py` — **(one-command hub build, the hub-build main entry)** deterministically in order: start registry center → write identity → enable SSH → start dashboard+patrol → register self → install Tailscale, **self-verifying each step and reporting faithfully at the end** (one missing and it doesn't report success, non-zero exit code); **idempotent**, re-runnable (skips what's running, fills what's missing). `--verify` only checks item by item without acting; `--skip-ssh` skips enabling SSH. **To build, run this one, don't hand-stitch step by step** (fixes the agent missing steps / writing wrong-OS commands at the root)
- `setup_control.py` — **(one-command control setup, the control entry; symmetric with setup_hub)** deterministically in order: install Tailscale → write identity (control, central=hub) → enable SSH → register self → store local mirror → self-check. `--central <建网机地址>` is required (same LAN = lan_ip / remote = Tailscale IP), and a **self-referential poison value (127.0.0.1) is rejected on the spot** (fixes the central-written-wrong → wrongly-empty-registry incident at the root); `--verify` only checks, `--skip-ssh` skips SSH. **To set up control, run this one, don't hand-type the five steps of identity+register+cache**
- `discover.py` — **(LAN auto-discovery of the hub, zero-dependency)** the hub's registry center runs a UDP responder; a new machine broadcasts "where's the hub", and the hub replies with its LAN IP. `register_node` / `keysync` auto-call it when `--registry-host` isn't given → **join without typing an IP**. Effective on the same LAN (broadcast doesn't cross routers; some AP-isolated WiFi blocks it → fall back to filling it in manually); across networks use the Tailscale name
- `enable_ssh.py` — cross-platform idempotent enabling of the SSH service (called when building / joining, needs admin)
- `netprobe.py` — probes external connectivity / NAT type / cellular / region, and suggests a remote-access method (defaults to Tailscale)
- `tailscale_proxy_bypass.py` — fixes the local proxy's bypass rules for the `100.x` subnet (Clash etc. block Tailscale)

**Register / trigger**
- `register_node.py` — collect → generate node card → write to the registry center. The card has **two classification axes**: ① **topology role** `role` (control / hub / secondary hub / node) + `belongs_to` (which hub the node belongs to, the routing key); ② **three capability layers**: hardware `hardware` / environment `agents`·`cli`·`gui` / **network speed `link`**. `--measure-link` [for the hub] self-measures this LAN's external baseline (netprobe: net_class/cellular/nat/isp) + downlink bandwidth (best-effort, `--speed-url` to swap the endpoint), **nodes don't self-measure, they inherit their hub's link**. `--enable-ssh` enables SSH in one step; on successful registration it conveniently writes this machine's identity marker (`identity.py`, patrol re-registration self-heals `central`, and after a transfer a node naturally points to its new home)

**Monitoring (resident on the hub)**
- `dashboard.py` — the HTTP dashboard on the hub, accessed by browser / iPad; reads the registry center + local liveness (ping + SSH-port fallback) + falls back to reading `status:*`
- `patrol.py` — the patrol loop (resident): ① probe liveness on this LAN and report online (ping + SSH-port fallback), push `status:*` to the main with multiple LANs; ② watch registered processes, update `task:*` (posix + Windows both supported); ③ `--refresh-every` (default ≈1 hour) periodically triggers re-registration of this LAN's nodes and refreshes cards (against card rot; nodes stay passive as before); ④ **auto-install control-side public keys each round** (a `pubkey:*` newly appearing in the registry center gets installed into this machine's door next round, idempotent — even if control joins later than the hub, no need to run keysync by hand; networking is order-agnostic)
- `watch_job.py` — register / list / cancel "have the hub watch a running process" (writes `task:*`, liveness checked by patrol)
- `dispatch.py` — **(control dispatches a task, the core of task execution)** run one command on a node (local / SSH, posix + Windows) → write `task:*` (running→done/failed + exit code + output tail) → echo; `--detach` hands a long task to patrol to watch; `--workspace` runs in the node's workspace `work_dir` (reads the card and auto-cd's by `os`, the agent doesn't hand-write cross-OS paths). The judgment (pick the machine / shell vs delegate to agent) is control's AI's, dispatch only executes; **when the hub drops, resolve auto-falls back to control's local mirror** (the command still runs, only task accounting is missing). **Ergonomics**: `--registry-host` empty = read from this machine's identity central; `--node` supports fuzzy match (hostname substring / hardware model like `2070` / `gpu` keyword, ambiguity reported with multiple matches); `--delegate "目标"` = delegate mode (auto-picks the codex/claude/opencode package's non-interactive call per the card); **`--check` = judge a node's liveness (a real SSH connection, the only authoritative way, don't use ping)**
- `setup_workspace.py` — **(set a node as a native workspace)** control runs it remotely against the node: create `work_dir` on the chosen disk + write the self-report marker `~/.myainet/workspace.json` + trigger register to self-report the OS contract into the card (**no container, no Docker**); afterwards `dispatch --workspace` sends work in. `--handle <节点>` instead writes a local handle (`CLAUDE.md`/`AGENTS.md` pointing at a remote workspace) into the current dir — for Desktop Claude/codex that can only pick a local folder
- `wake.py` — **(remotely wake a sleeping node, WoL)** reads the node card's `wake.mac` + `belongs_to` (its hub) → via the hub broadcasts a WoL magic packet on its LAN → polls `--check` until online. Only wakes sleep/hibernate (**not powered-off**); needs the node's NIC armed for WoL + wired LAN. Nodes with `wake=null` (WiFi laptop / not armed / no wired NIC) are refused. The hub is the send point because WoL needs a same-LAN broadcaster
- `report.py` — **(agent reports, monitoring mode ②)** an agent proactively writes a note **with judgment** to the board (writes `task:*` status=note, visible with a gray background on the dashboard). The machine writes "still alive" (patrol), the agent writes "what happened / good or not / whether to act"
- `healthcheck.py` — **(hub self-check)** locally checks whether the registry center / Dashboard / Patrol / Tailscale are up, giving the start command for any that are down (cross-platform); run it first when the skill recognizes a hub
- `keysync.py` — **(shared SSH key-exchange logic, usually invoked automatically by `register_node` at registration + by patrol, not run separately)** publishes this machine's public key + installs control-side public keys into this machine's door; all-local operation on `authorized_keys` + publish/pull via the registry center, **zero password, idempotent, doesn't overwrite your existing keys**. A Windows admin account is **automatically** written into `administrators_authorized_keys` with the ACL set (using built-in SIDs, correct on Chinese systems too)
**Aggregation / lifecycle**
- `assemble_network.py` — reads all nodes and outputs the raw network state (hardware + agent/cli/gui, **facts only, no scoring**), for control to assign roles
- `leave_network.py` — leave the network: delete the registration card + leave / uninstall Tailscale (only touches this machine's safety gate; `--purge` uninstalls software too, `--dry-run` previews)
- `transfer_role.py` — transfer: move the hub's "listen + write dashboard" responsibility + the registry to a new machine — copy `node:*` / `task:*` old→new (**add `--from-mirror` if the old hub already died, falling back to control's mirror**) + verify + print a **moving checklist** (start the new hub service / change the secondary bridge / change the identity marker). **It doesn't delete the old one**, `--dry-run` previews; in practice it's an **on-LAN transfer to a node**

## Reference files

- `references/model-matrix.md` — the full model recommendation matrix (broken down by VRAM)
- `references/build-manual.md` — the **manual-equivalent commands** for each hub-build step (only for troubleshooting when a specific `setup_hub.py` step fails; don't read it under normal conditions)
