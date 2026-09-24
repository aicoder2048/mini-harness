# Sandbox for Agent Harness（概念锚点 + 服务商全景）

> **状态：mini-harness 尚未实现沙箱。** 本文是概念参考和设计备忘：沙箱是什么、为什么需要、怎么接进 harness、
> 市面上有哪些方案，以及「能不能用自己的机器」。资料截至 2026-09-24。

## 一句话

**沙箱是 agent 执行动作的「场地」。** 它不决定模型做什么，而是决定模型做的事**最多能影响到哪里**。

回到 README 的公式：

```
Harness = Loop + Tools + Context + Control
```

Tools 回答「模型能做什么」，Control 回答「什么不能做」。沙箱位于两者交界：**同一个 `run_bash`，放在不同的场地里，风险完全不同。**

## 为什么需要：从「逐条审批」到「限定影响范围」

mini-harness 现在的 `run_bash` 直接在你的机器上执行模型生成的命令，唯一的防线是每条命令都问 `[y/N]`。这有两个问题：

1. **审批疲劳**：一个任务要按几十次 `y`，最后会不看内容就按
2. **看起来无害的命令也能出事**：`pip install 某个包`，包的安装脚本可以做任何事

沙箱换了一种思路：**不再逐条判断命令是否安全，而是让任何命令就算出问题，影响也只限于这个环境。** 影响范围被限定之后，审批才能放宽——这才是沙箱真正换来的东西：**自主性**。

IndyDevDan 在[这期视频](https://www.youtube.com/watch?v=SEI_qIW4o2c)里把沙箱的收益总结为三点：

| 收益 | 含义 | 只用本机容器能得到吗 |
|---|---|---|
| **隔离**（isolation） | 出事只坏在沙箱里 | 能 |
| **扩展**（scale） | N 个 agent、N 台机器，互不冲突 | 很难：受限于一台电脑 |
| **自主**（autonomy） | 人退出循环，只在开头规划、结尾审查 | 很难：还在你电脑上，你不敢放手 |

他的原话大意：「在你自己的电脑上跑 agent，即使放在容器里，也只得到隔离。」

## 隔离程度：从无到完整虚拟机

| 档位 | 做法 | 例子 | 代价 |
|---|---|---|---|
| 0. 无隔离 | 本机执行 + 人工审批 | **mini-harness 现状** | 全靠人盯 |
| 1. 系统级限制 | 限制进程能访问的文件和网络 | macOS Seatbelt、Linux bubblewrap | 和宿主共用内核；规则写漏就漏了 |
| 2. 虚拟环境 | 在内存里模拟 bash 和文件系统 | just-bash（Vercel 课程 4.3） | 不是真 Linux，很多命令跑不了 |
| 3. 容器 | 共享内核的进程隔离 | Docker | 共享内核，逃逸风险高于 VM |
| 4. microVM | 极简虚拟机，独立内核，启动快 | Firecracker 类方案 | 按时计费、每次调用有网络延迟 |
| 5. 完整 VM | 完整 Linux 机器，独立内核 | exe.dev（KVM / Cloud Hypervisor） | 同上；但环境最「真」 |

档位越高，隔离越彻底，也越接近「一台真电脑」；代价是成本、延迟和运维。

## 沙箱怎么接进 harness：先定接口

Vercel 课程模块 4 的做法是**先写接口，再写实现**。换成 Python 大概是：

```python
class Sandbox(Protocol):
    type: str                  # "local" / "exe.dev" / ...，用于日志
    working_directory: str
    def read_file(self, path: str) -> str: ...
    def exec(self, command: str) -> tuple[str, int]: ...    # (合并后的输出, 退出码)
    def stop(self) -> None: ...
```

工具不再直接用 `open()` 和 `subprocess`，而是调用 `sandbox.read_file()` / `sandbox.exec()`。换后端时工具代码一行不动——**模型看到的工具契约不变，变的只是命令在哪执行**。

课程给的原则：**接口尽量小**。现在加的每个方法，以后每个后端都得支持。快照、过期时间这类只有部分后端有的能力，做成可选。

在 mini-harness 里对应的改动：

| 现在 | 改成 |
|---|---|
| `tools.py` 的 `_read_text` 直接 `open()` | `sandbox.read_file()` |
| `_run_bash` 直接 `subprocess.Popen` | `sandbox.exec()` |
| `edit_file` 直接写文件 | 需要给接口加 `write_file`，或用 `exec` 通过标准输入写 |
| `run_bash.needs_approval=True` | 在隔离的沙箱里可以改为默认放行 |

## 沙箱防得住什么、防不住什么

| 威胁 | 沙箱能防吗 | 说明 |
|---|---|---|
| 误删文件、装坏环境 | ✓ | 坏在沙箱里，删掉重建 |
| 恶意安装脚本改动宿主机 | ✓（档位 3 以上更可靠） | |
| 多个 agent 互相踩文件 | ✓ | 一个 agent 一个环境 |
| **数据外泄** | ✗ | 沙箱通常能联网；模型被 prompt 注入诱导，把代码或密钥发出去，隔离挡不住 |
| **滥用放进沙箱的密钥** | ✗ | 放进去的密钥，模型就能用 |

所以**密钥边界**和沙箱同样重要。两个值得学的做法：

- **主密钥不进沙箱**：IndyDevDan 的[配套仓库](https://github.com/disler/inkwell-agent-sandboxes-and-software-factory)里，exe.dev 账号和 OpenRouter 主密钥只留在宿主机；每个沙箱在创建时拿到一把**临时的、有花费上限（$50）的子密钥**，销毁时吊销。仓库原话：「一层嵌套，靠凭证来保证，而不是靠删文件。」
- **密钥在出口注入**：exe.dev 的 Integrations 在 VM 的出站代理上注入请求头，VM 里的程序能调用 API，但**看不到密钥本身**。

## 服务商全景

> 截至 2026-09-24。每项都附来源；「二手」表示只在第三方资料里查到。价格只写**计费形态**，具体数字以官网为准。
> 分三类：**云端托管**（远程机器）、**本地**（在你自己的电脑上隔离）、**进程内**（根本不起真正的进程）。

### 云端托管

| 服务 | 隔离技术 | 生命周期 / 持久化 | 接口 | 计费形态 | 能否自托管 / 开源 |
|---|---|---|---|---|---|
| [E2B](https://e2b.dev) | Firecracker microVM | 单次运行最长 1h（Hobby）/ 24h（Pro）；可暂停，保留文件系统**和内存** | Python / JS SDK | 按秒计 vCPU 与内存；有免费档 | 基础设施开源（Apache-2.0）；企业版可部署到自己的云 |
| [Vercel Sandbox](https://vercel.com/docs/sandbox/concepts) | Firecracker microVM | **默认持久**：停止时快照文件系统，下次恢复；单次会话 45 min（Hobby）/ 5h（Pro） | JS / Python SDK、CLI | 按活跃 CPU（I/O 等待不计）+ 内存 + 快照存储 | 仅托管 |
| [Modal Sandboxes](https://modal.com/docs/guide/sandboxes) | 默认 gVisor；另有 beta 版「VM Sandboxes」（真 Linux 内核） | 默认 5 min，最长 24h；文件系统快照 | Python / JS / Go SDK | 按秒 | 仅托管 |
| [Daytona](https://www.daytona.io/docs/en/sandboxes/) | 默认 OCI 容器；另有 Linux VM 类（可 fork、暂停、内存快照） | 自动停止 / 归档 / 删除策略 | 多语言 SDK、REST、CLI、SSH | 按秒 | **2026-06 起转为闭源**（公开仓库停止维护）；可在自己的机器上跑 runner，但由 Daytona 控制面管理 |
| [Cloudflare Sandbox](https://developers.cloudflare.com/sandbox/concepts/security/) | 每个沙箱一台独立 VM | 空闲休眠、请求唤醒；磁盘默认不持久，可备份到 R2；2026-04 GA | 在 Workers 里用 TS SDK | 需 Workers 付费版；CPU 按活跃计 | 仅托管 |
| [Fly Sprites](https://fly.io/sprites/) | Firecracker microVM | 100 GB 持久磁盘；空闲挂起（含内存）、约 1 秒唤醒；写时复制检查点 | CLI、REST、多语言 SDK | 按活跃秒数计 CPU / 内存 / 存储 | 仅托管 |
| [Runloop](https://docs.runloop.ai/docs/devboxes/overview) | VM | 快照、挂起、恢复 | Python / TS SDK、REST、CLI | 按 CPU·小时、GB·小时（二手） | 企业版可部署到自己的 AWS VPC |
| [Northflank](https://northflank.com/product/sandboxes) | 主要是 Kata + Cloud Hypervisor；也可选 gVisor / Firecracker | 临时或持久 | API / CLI / UI | 按 vCPU·小时、GB·小时 | 可部署到自己的云、机房、裸金属（厂商自述） |
| [exe.dev](https://exe.dev) | KVM 虚拟机（文档称目前用 Cloud Hypervisor，「可能会换」） | **持久磁盘**；`cp` 复制整台 VM | `ssh` 和 HTTPS `/exec` | 按资源池月费（二手：$20/月起） | 仅托管 |
| [AWS Bedrock AgentCore Code Interpreter](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-session-characteristics.html) | 每个会话一台 microVM，结束时清理内存 | 默认 15 min，最长 8h | AWS SDK | 按活跃消耗（二手） | 仅托管 |
| [AWS Lambda MicroVMs](https://aws.amazon.com/blogs/aws/run-isolated-sandboxes-with-full-lifecycle-control-aws-lambda-introduces-microvms/) | Firecracker | 最长 8h；空闲挂起时**保留内存和磁盘**，有请求再恢复；2026-06-22 发布 | 控制台、CLI、HTTPS | 单独计价 | 仅托管 |
| [GKE Agent Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/agent-sandbox) | gVisor（也可用 Kata） | 有状态的单副本 Pod | Kubernetes CRD | 除 GKE 本身外不额外收费 | 开源（[kubernetes-sigs/agent-sandbox](https://github.com/kubernetes-sigs/agent-sandbox)），任何 Kubernetes 都能跑 |

### 本地（在自己的电脑上隔离）

| 方案 | 隔离技术 | 用法 | 开源 |
|---|---|---|---|
| [Claude Code sandbox](https://code.claude.com/docs/en/sandbox-environments)（[sandbox-runtime](https://github.com/anthropic-experimental/sandbox-runtime)） | 系统级：macOS Seatbelt；Linux / WSL2 用 bubblewrap，网络经代理 | Claude Code 里 `/sandbox`；或 `npx @anthropic-ai/sandbox-runtime <命令>` | 是（研究预览） |
| [OpenAI Codex CLI](https://learn.chatgpt.com/docs/permissions) | 系统级：macOS Seatbelt；Linux bwrap + seccomp（Landlock 兜底） | 只读 / 工作区可写 / 完全放开三档 | 是 |
| [Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) | microVM | `sbx run claude` 等；也能在 Docker 的云上跑 | 未说明 |
| [microsandbox](https://github.com/superradcompany/microsandbox) | libkrun microVM（macOS 上用 Apple Silicon 虚拟化） | `msb` CLI、多语言 SDK、MCP；支持快照和 fork | Apache-2.0 |
| [Apple container](https://github.com/apple/container) | 每个 Linux 容器跑在自己的轻量级 VM 里 | CLI | 是 |
| [Lima](https://github.com/lima-vm/lima) / [Tart](https://github.com/openai/tart) | 完整 Linux（Tart 还支持 macOS）虚拟机 | CLI；ssh 进 VM | 是 |
| [gVisor](https://gvisor.dev) / [Firecracker](https://firecracker-microvm.github.io/) | 底层构件：用户态内核 / KVM microVM 管理器 | 很多上面的服务都建在它们之上 | 是 |

### 进程内（不起真正的进程）

| 方案 | 做法 | 状态 |
|---|---|---|
| [just-bash](https://github.com/vercel-labs/just-bash) | 用 TypeScript 重写 bash 和 70 多个命令，跑在内存中的虚拟文件系统上 | 开源；Vercel 课程在用 |
| [Pydantic Monty](https://github.com/pydantic/monty) | 用 Rust 实现 Python 的一个子集；默认不能访问文件、环境变量、网络 | 开源 |
| [mcp-run-python](https://github.com/pydantic/mcp-run-python)（Pyodide + Deno，WASM） | 在 WebAssembly 里跑 Python | **2026-01-30 已归档**：作者认为无法在合理延迟下安全地运行 |

### 能在自己的硬件上跑吗

| 方式 | 方案 |
|---|---|
| 完全自己运行 | GKE Agent Sandbox（任何 Kubernetes）、microsandbox、E2B 开源基础设施、Lima / Tart / Apple container |
| 机器在你这里，控制面在厂商那里 | [Claude Code self-hosted environments](https://code.claude.com/docs/en/self-hosted-environments)（Team / Enterprise 公测，你运行的 runner 只需出站 HTTPS）、Daytona 的 Customer Managed Compute |
| 部署到自己的云账号 | E2B（企业版）、Runloop（企业版，AWS VPC）、Northflank |
| 不支持 | exe.dev、Fly Sprites、Vercel、Modal、Cloudflare |

### 几个趋势

- **快照 / 挂起 / 恢复成了标配**：E2B、Vercel、Fly Sprites、Runloop、Modal、AWS Lambda MicroVMs 都有。沙箱不再是「用完即删」，而是「随时存档、随时恢复」
- **云厂商下场了**：2026 年 AWS 发布 Lambda MicroVMs，Google 发布 GKE Agent Sandbox
- **Firecracker 是云端最常见的底座**：E2B、Vercel、Fly、AWS 都用它
- **开源在收缩**：Daytona 转为闭源；Pydantic 放弃了基于 WASM 的 Python 沙箱
- **本地隔离也在往 VM 走**：Docker Sandboxes、microsandbox、Apple container 都用轻量级 VM，而不是共享内核的容器

### 尚未核实或说法冲突的

- exe.dev 的虚拟机管理器：文档说 Cloud Hypervisor；创始人 2025 年底在 Hacker News 的评论说是「源自 crosvm 的 VMM」。本文采用文档说法
- Cloudflare、Docker Sandboxes、Modal VM Sandboxes 具体用哪种虚拟机管理器，官方未说明
- 标注「二手」的计费数字只在第三方资料里查到

## exe.dev 详解

**谁做的**：David Crawshaw（Tailscale 联合创始人、2019–2024 年任 CTO）和 Josh Bleecher Snyder（早期 Tailscale 工程师，长期参与 Go 编译器和运行时开发）。2026 年 4 月 A 轮，累计融资 3500 万美元（Amplify、CRV、Heavybit）。与 Anthropic 无关。

**核心理念**：给 agent 的应该是**一台真电脑**，不是一个容器。

| 项目 | 情况 |
|---|---|
| 产品形态 | **托管服务，不是库**；没有必须安装的 SDK |
| 隔离 | KVM 虚拟机，每台 VM 独立内核（文档称目前用 Cloud Hypervisor，「可能会换」） |
| 创建速度 | 官方称不到一秒 |
| 默认镜像 | `exeuntu`（Ubuntu 24.04）；root 权限，`apt`、`systemd` 可用 |
| 持久化 | **磁盘在重启后保留**；停机时只收磁盘费用 |
| 操作 | `ssh exe.dev new` 创建；`ssh <vm>.exe.xyz <命令>` 执行；`ssh exe.dev rm <vm>` 删除；`ssh exe.dev cp` 复制整台 VM |
| HTTPS API | `POST https://exe.dev/exec`，请求体就是 ssh 命令——**只有一套 API 要学** |
| 退出码 | 在响应的 `X-Exe-Exit` trailer 里；stdout 和 stderr 合在响应体里 |
| 网址 | 每台 VM 自动获得 `https://<vm>.exe.xyz/`；默认私有，分享后才可访问 |
| 权限 | API token 可以限定命令，甚至只能操作一台 VM：`--cmds="'ssh my-vm'"`；强烈建议设过期时间 |
| 内置 agent | 每台 VM 自带开源编程 agent **Shelley**（多模型），也读 `AGENTS.md` / `CLAUDE.md` |
| 计费 | 按资源池按月计费（如 2 vCPU / 8 GB 的池，含 100 GB 磁盘，池里可开多台 VM，有数量上限）；另有按用量计费。价格以[官网](https://exe.dev/pricing)为准 |
| 给 agent 读的文档 | [`llms.txt`](https://exe.dev/llms.txt)，每篇文档都有 `.md` 版本 |

**接进 harness 时要注意**（来自官方文档）：

1. **HTTPS `/exec` 限制很严**：不支持 stdin、不支持 pty、**30 秒超时**（超时返回 504）、请求体最大 64 KB。所以 harness 更适合**直接用 ssh**：ssh 能通过标准输入传文件内容，也没有 30 秒的硬限制
2. **命令经过两层解析**：HTTPS API 先用 shell 词法解析请求体，VM 上再解析一次；管道、重定向、引号都要能经受两层转义
3. **每次调用有网络延迟**（几十到几百毫秒），一个任务几十次工具调用会累积
4. **长任务要脱离请求**：用 `setsid nohup ... &` 后台运行，再轮询日志

## 能不能用自己的机器（比如 Mac mini）？

**exe.dev 本身不能。** 它是纯托管服务，[文档](https://exe.dev/llms.txt)里没有「部署在自有硬件上」的选项。文档里带 self-hosted 字样的条目（Gitea、GitHub Actions runner 等），都是指「在 exe.dev 的 VM 上自己部署某个软件」。

上面那期视频也**没有**演示本地机器方案。视频里本地电脑的角色是**指挥端**：Claude Code 在你的电脑上当总调度，VM 都在 exe.dev 云上。

**但可以在 Mac mini 上复刻 exe.dev 的「形状」**，关键在于：**exe.dev 的接口本质上就是 ssh。**

```
exe.dev：   ssh sbx-42.exe.xyz  "python3 -c 'print(2+2)'"
Mac mini：  ssh agent@mac-mini  "python3 -c 'print(2+2)'"
```

对 harness 来说，这两者是**同一个 `SshSandbox(host=...)`**，只是地址不同。实现一次 ssh 后端，云上和家里都能用。

在 Mac mini 上实现可以分三档：

| 方案 | 做法 | 隔离程度 |
|---|---|---|
| **A. 整台机器当沙箱** | 在 Mac mini 上开一个专用的普通用户；harness 通过 ssh 执行命令 | 与你的主力机物理隔离；但 agent 在 mini 上能动该用户能动的一切 |
| **B. mini 上跑 Linux VM** | 用 [Lima](https://github.com/lima-vm/lima) 或 [Tart](https://github.com/openai/tart)（原 Cirrus Labs 项目，现在仓库在 `openai/tart` 下）开 VM，一个 agent 一台；ssh 进 VM 执行 | 接近 exe.dev：独立内核，可随时销毁重建 |
| **C. Apple container** | Apple 官方的 [`container`](https://github.com/apple/container)：每个 Linux 容器跑在自己的轻量级 VM 里 | 比 Docker 共享内核更强；Apple Silicon 专用 |
| **D. microsandbox** | [microsandbox](https://github.com/superradcompany/microsandbox)：本地 microVM，带多语言 SDK、快照和 fork | 最像「本地版 E2B」：用 SDK 而不是 ssh 来管理沙箱 |

从外面连回家里的 mini，可以用 Tailscale 这类组网工具（顺带一提，exe.dev 的创始人正是 Tailscale 的联合创始人）。

**本地方案 vs exe.dev**：

| | Mac mini 本地 | exe.dev |
|---|---|---|
| 成本 | 一次性硬件费用，之后几乎免费 | 按月或按用量付费 |
| 扩展 | 受限于一台机器的内存和 CPU | 按需开很多台 |
| 数据位置 | 留在家里 | 在云上 |
| 运维 | 自己管：系统更新、网络、VM 镜像 | 托管 |
| 对外网址、分享 | 要自己配（如 Tailscale Funnel） | 自带 `https://<vm>.exe.xyz` 和分享 |
| 创建和销毁 | 取决于 Lima / Tart 的速度 | 秒级 |

**建议**：个人学习、数据不想出门、并发 agent 不多（几个）时，Mac mini + Lima / Tart 很合适；需要同时跑十几个 agent、要对外分享网址、不想运维时，用 exe.dev 这类托管服务。

## 对 mini-harness 的建议（尚未实现）

| 选项 | 内容 |
|---|---|
| **A. 只写文档**（当前选择） | 就是本文 |
| B. 只引入接口 | 加 `Sandbox` 接口，只实现 `LocalSandbox`，工具改为调用接口；行为不变，不需要账号——是「接口设计」的一节好课 |
| C. ssh 后端 | 在 B 的基础上加 `SshSandbox(host)`：**同一份实现，既能连 exe.dev，也能连自己的 Mac mini**；配一个 live 测试 |

C 的性价比很高：因为 exe.dev 就是 ssh，写一个 ssh 后端就同时覆盖了云端和本地两种场景。

## 参考资料

- Vercel Academy [Build Your Own AI Coding Agent Harness](https://vercel.com/academy/build-ai-agent-harness)：模块 4「沙箱抽象」、模块 7「沙箱生命周期」
- exe.dev：[官网](https://exe.dev)、[`llms.txt`](https://exe.dev/llms.txt)、[HTTPS API](https://exe.dev/docs/https-api.md)、[在 VM 上执行命令](https://exe.dev/docs/https-api-run-on-vm.md)、[Sandbox 产品页](https://exe.dev/sandbox)、[A 轮公告](https://blog.exe.dev/series-a)
- [Amplify Partners：exe.dev and the perfect little computer](https://www.amplifypartners.com/blog-posts/exe-dev-and-the-perfect-little-computer)
- [Flavio Copes：A deep dive into exe.dev](https://flaviocopes.com/exe-dev/)
- IndyDevDan：[Your Software Factory NEEDS Agent Sandboxes to SCALE (exe.dev)](https://www.youtube.com/watch?v=SEI_qIW4o2c) 及[配套仓库](https://github.com/disler/inkwell-agent-sandboxes-and-software-factory)
