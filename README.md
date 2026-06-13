# myainet

**中文** · [English](README.en.md)

**一张个人自动化 AI 网络 —— 让你的 AI agent 调度你所有的机器，而不只是它所在的那一台。**

> A **personal automation AI network**: give your AI coding agent a fleet — dispatch tasks
> across all your own machines (Windows / macOS / Linux), not just the one it runs on.

![myainet 大屏](docs/dashboard.png)

myainet 是一个 **agent skill**，把你自己的多台机器组成一张**个人自动化 AI 网络**：各机器各司其职，
你的 agent（Claude Code / codex / opencode）跨机器统一调度——在有显卡的机器上跑推理，在常驻机上挂服务，
人在外面也能控全网。全是你自己的机器、你自己说了算，不上云、不分享、不遥测。

## 前提

本 skill 的脚本全是 Python，且**零 pip 依赖**——只用标准库（连注册中心都是自带的纯 socket 脚本，
无需 Docker、无需 `pip install`）。正因为这样，每台要入网的机器只需要两样东西：

- **一个 Python 解释器（3.7+）**。脚本用了 3.7 起才有的写法，所以这是底线；新一点都行。
  Windows 上解释器通常叫 `python` / `py`（不一定有 `python3`），没装也没关系——skill 会自动帮你装好。
- **一个能加载 skill 的 agent**（Claude Code / codex / opencode / Claude Desktop 等）。装法见下。

## 安装

- **Claude Desktop** —— 下载本仓库的 [`myainet.skill`](myainet.skill)，在 Claude Desktop 里装这一个文件即可
  （脚本都打包在里面）。
- **Claude Code / codex / opencode 等** —— 把本仓库内容放进该 agent 的 skills 目录（`SKILL.md` 在根，
  和 `scripts/` 同级），或按你这个 agent 装 skill 的常规方式装。

## 搭建

一台台来，**从建网机开始**：

1. **建网机** —— 先装它，网络就建起来了。挑一台 24h 常驻的机器，加载 skill、选「建网机」，
   它一键起好注册中心（全网机器登记在这）+ 远程接入（SSH + Tailscale）+ 大屏。
2. **主控** —— 你随身、用得最多的那台（电脑 / 笔记本）。加载 skill、选「主控」，它连上建网机，
   从此能读全网、控全网（自动装 Tailscale）。
3. **节点** —— 剩下出算力 / 服务的设备。加载 skill、选「节点」，扫描本机、注册进网络
   （节点不用装 Tailscale，经建网机穿透够到）。

> 角色是**机器级**的：一台机器确认过角色后，这台上**任何别的 agent 再装本 skill 都能直接用**，不必重新确认。
>
> 多一个局域网就加一台**次建网机**（建网机的精简版）。

## 使用（搭建完之后）

在任意任务里直接调用本 skill —— agent 立刻拿到**全网机器的实况**（谁在线、谁有显卡、装了什么、在跑什么），
然后跟它说人话就能把活派下去：

- 「在有显卡的机器上跑这个训练」 → 在目标机原样跑、秒回
- 「在那台机器上把 ComfyUI 装好」 → 委托那台本地的 agent 自己搞定
- 「这个长任务甩后台跑，盯着别让它崩」 → 后台启动 + 巡检盯死活
- 「看下全网状态」 → 读注册中心，或开大屏（浏览器 / 手机，拓扑 + 算力 + 任务，中英可切）

**手机 / 平板没装 agent？** 浏览器打开建网机的大屏，上面有个命令框——直接跟建网机的 agent 对话、
把任务委托给它，手机上什么都不用装。（这正是"借建网机的 agent 用"：你这端没有 agent 时的入口。）

> **推荐：让一个 agent 直接调度全网资源，而不是串联多个 agent。**
> 你对话的那个 agent（主控 / 建网机上）直接把每台机器的显卡 / 磁盘 / 带宽当自己的用——
> 一个大脑、一条直接的指挥链，确定、快、看得见。skill **也支持全网多 agent 协同**
> （把活委托给各机器本地的 agent，适合「装环境 / 搞定模糊任务」），但多个 agent 串联更慢、
> 过程不透明——**能单 agent 直连，就别绕多 agent。**

## License

PolyForm Noncommercial 1.0.0 —— 非商业用途（个人 / 研究 / 学习）自由使用，**禁止商用**。
见 [LICENSE](LICENSE)。
