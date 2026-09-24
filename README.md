# mini-harness

[![CI](https://github.com/aicoder2048/mini-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/aicoder2048/mini-harness/actions/workflows/ci.yml)

跟学 Thorsten Ball《How to Build an Agent》的 Python 复现（对应 `docs/Thorsten-Ball-构建Agent-Python跟学版.pdf`），
模型 Provider 用 **DeepSeek**（OpenAI 兼容协议）。

`src/` 约 700 行（一半是注释）、四个工具、一个循环：一个能读、能找、能改你代码、能跑命令的 agent。
前三个工具与原文一致，`run_bash` 是额外加的第 5 步。

## 致谢 / Credits

本项目是跟学作品，思路与结构完全来自：

- **Thorsten Ball，[How to Build an Agent](https://ampcode.com/how-to-build-an-agent)**（Amp，2025-04-15）。
  这篇文章用不到 400 行 Go 说明了 code-editing agent 的全部要素就是「LLM + 一个循环 + 几个工具」。
  本仓库的四步递进、`read_file` / `list_files` / `edit_file` 三个工具的设计、`need_user_input` 那个循环，都出自这篇文章。
  原文没有官方代码仓库，代码直接写在文章里——强烈建议先读原文。
- **Janitha Rathnayake，[How to Build an Agent by Thorsten Ball (Python Version)](https://medium.com/@jbrathnayake98/how-to-build-an-agent-by-thorsten-ball-python-version-ebbabb8665f6)**（Medium，2025-08-19）。
  Python 移植时的参考。
- **Joel Hooks，[Build Your Own AI Coding Agent Harness](https://vercel.com/academy/build-ai-agent-harness)**（Vercel Academy）。
  system prompt 的设计（分段、随工具集变化、验证段、`AGENTS.md` 注入）参考了其中模块 3「系统提示词」。

在此之上，本仓库换成了 DeepSeek（OpenAI 兼容协议），并额外加了 system prompt、`run_bash` 工具等，见下文「与 PDF 代码清单的差异」。

## 学习地图：WHAT / WHY / HOW

模型只会一件事：看一段文本，吐一段文本。除此之外的一切——读文件、跑命令、记住上文、决定何时停——都是 **harness**。

```
Agent   = Model + Harness
Harness = Loop + Tools + Context + Control

output_t    = M(context_t)              # 模型：只负责「想」——provider.chat()
context_t+1 = H(context_t, output_t)    # harness：执行工具、回灌结果、决定下一步
```

模型是无状态的纯函数；agent 的「状态」和「行动」全部发生在 H 里。

| 组件 | WHAT（是什么） | WHY（没有它会怎样） | HOW（去哪读） |
|---|---|---|---|
| **Loop** 循环 | `need_user_input`：有工具调用就不等用户，直接再问模型 | 模型只能「请求」动作，执行和续轮全靠 harness | `agent.py` `Agent.run` |
| **Loop** 预算 | `--max-rounds`：连续工具轮数上限，到了交回给人 | 没刹车的循环会原地打转、烧 token | `agent.py` `MAX_TOOL_ROUNDS` |
| **Tools** 工具 | `Tool` = name / description / input_schema / run | 模型的「手」；description 本身就是写给模型的 prompt | `tools.py` `Tool` |
| **Tools** 接口设计 | `edit_file` 必须唯一命中；`run_bash` 超时 | 接口宽松，模型就会沉默地做错事 | `tools.py` `_edit_file`、`_run_bash` |
| **Tools** 有界输出 | 每个工具都有上限（`read_file` 500 行 + 分页），截断时说明还剩多少 | 结果进了历史就要每轮重发；悄悄截断会让模型误以为看到了全部 | `tools.py` `MAX_READ_LINES` 等 |
| **协议适配** | 内部只认 `Reply` / `ToolCall` / `ToolResult` | 各家 API 线上格式不同，循环不该关心 | `providers.py` `chat`、`tool_results` |
| **Context** 对话历史 | 每轮把 conversation 全量重发 | 服务端无状态，「记忆」只存在于本地这个列表 | `step1_chat.py`、`Agent.run` |
| **Context** system prompt | 写「策略」而非「能力」；按实际挂载的工具拼段 | 工具说明「能做什么」，prompt 说明「该怎么做」 | `prompt.py` `build_system_prompt` |
| **Context** 项目上下文 | 启动时读 `AGENTS.md` 注入 prompt | harness 是通用的，每个项目各有各的命令和约定 | `prompt.py` `load_project_context` |
| **Context** 跨会话记忆 | **读**：启动时把 `Memory/` 的笔记列成索引，正文按需 `read_file`；**写**：prompt 告诉模型何时、怎么用 `edit_file` 记笔记 | 模型无状态，跨会话「记得什么」全由 harness 决定；全文注入会在开工前就占掉上万 token | `prompt.py` `load_memory_index`、`_memory_section` |
| **Context** 用量遥测 | 每次调用后在 stderr 打印 input / cached / output token | 看不见曲线，就判断不了修复有没有用 | `Agent._log_usage` |
| **Context** 修剪 | 输入超预算时，把旧工具结果**批量**换成占位符 | 历史只增不减；逐轮滑动修剪会让前缀缓存全部失效 | `Agent.run`、`DeepSeekProvider.prune_tool_results` |
| **Control** 错误回灌 | 工具失败 → 错误结果交回模型，而不是抛异常 | 模型能自己纠错；程序一崩，agent 就死了 | `Agent._execute`、`DeepSeekProvider._tool_call` |
| **Control** 人工审批 | `needs_approval` 的工具执行前问 `[Y/n/a]`，可「本会话都允许」 | 模型是在**你的机器上**执行命令 | `agent.py` `ConsoleApprover` |

**建议阅读顺序**

1. `step1_chat.py`：最裸的一次 API 调用；理解「记忆」= 本地一个列表
2. `agent.py` 的 `Agent.run`：整个 agent 就是这个循环（不到 40 行）
3. `tools.py` 的 `Tool` 和 `read_file`：一个工具的四要素
4. `providers.py` 的 `chat` / `tool_results`：内部结构 ↔ 线上格式
5. `prompt.py` 的 `build_system_prompt`：prompt 如何由运行时状态拼出来
6. 其余（`_execute` 的各种失败处理、`_run_bash` 的超时与进程组）是加固，最后再看

想看最朴素的教程原版：`git checkout 7b7ffd1`。之后每个 commit 只加一件事，commit message 写明了原因——`git log` 本身就是一份课程。

**工程上的划分**（业界名词并无标准定义，这是一种好用的切法）：

```
Harness Engineering        怎么搭整台机器
├── Context Engineering    每一轮窗口里放什么
│     └── Prompt Engineering   其中固定、手写的那部分（system prompt、工具描述）
├── Tool Engineering       模型能做什么、工具接口怎么设计
├── Control Engineering    何时调用、何时停、何时问人、预算与失败处理
└── Evaluation             怎么证明改了之后变好了
```

**还没覆盖的**

- **对话摘要**（类似 `/compact`）：目前只修剪工具结果；长对话本身的文字还会一直增长
- **记忆的整理**：读和写都有了，但没有定期合并、删除过时笔记的机制（prompt 只要求「写之前先看有没有同主题的、写错了就改」）；
  也没有删除工具，过时笔记只能改写或由人手动删
- **Evaluation**：有单元测试和 live 冒烟测试，但没有衡量 agent 行为好坏的 eval（同一任务跑多次、统计通过率、对比改动前后）。
  方案已写成 [`docs/eval-plan.md`](docs/eval-plan.md)（占位，尚未实现）
- **沙箱**：`run_bash` 直接在本机执行，只靠人工审批。概念、接入方式、服务商全景（exe.dev、E2B、Vercel 等）和「能否用自己的 Mac mini」
  见 [`docs/sandbox.md`](docs/sandbox.md)（尚未实现）
- 子 agent、流式输出与中途打断

## 目录

```
src/
  step1_chat.py   第 1 步：聊天循环（还不是 agent），直接调 OpenAI 兼容 SDK
  tools.py        四个工具 read_file / list_files / edit_file / run_bash + 手写 JSON Schema
  providers.py    DeepSeekProvider：工具声明 / assistant 回灌 / 工具结果回灌的线上格式全收在这里
  prompt.py       system prompt：由工作目录 / 工具集 / git 分支 / AGENTS.md / 记忆索引拼出的分段 prompt
  agent.py        第 2–5 步：agent 循环 + 危险工具确认，--step 控制工具集
tests/            pytest：快速测试全用 fake；test_live.py 打真实 API（默认跳过，-m live 运行）
AGENTS.md         给 agent 看的项目说明（命令、架构、约定、踩过的坑）
CLAUDE.md         只有一行 `@AGENTS.md`：Claude Code 读 CLAUDE.md，靠这个 import 读到同一份说明
.env.example      环境变量示例；复制成 .env 再填 key（.env 已在 .gitignore，不会提交）
Memory/           跨会话记忆：普通 Markdown 笔记，启动时列成索引（已在 .gitignore，只留本地）
```

## 准备

```bash
cp .env.example .env && chmod 600 .env   # 再把 DEEPSEEK_API_KEY 换成你的 key（https://platform.deepseek.com）
uv sync
```

下面所有命令都用 `uv run --env-file .env` 让 uv 把 `.env` 注入环境；
如果你已经 `export DEEPSEEK_API_KEY=...`，去掉 `--env-file .env` 即可。

## 五步

```bash
uv run --env-file .env src/step1_chat.py        # 第 1 步：连问两轮，第二轮引用第一轮 → 它"记得"
uv run --env-file .env src/agent.py --step 2    # 第 2 步：+ read_file    → 「帮我解开 secret.txt 里的谜题」
uv run --env-file .env src/agent.py --step 3    # 第 3 步：+ list_files   → 「这个目录里的 Python 文件都在干什么？」
uv run --env-file .env src/agent.py --step 4    # 第 4 步：+ edit_file    → 「建一个 fizzbuzz.js，能用 node 跑」
uv run --env-file .env src/agent.py             # 第 5 步（默认）：+ run_bash → 「解 input/math.txt 的题，用 python 验算」
```

Ctrl-D 退出。工具调用会以绿色 `tool:` 行打印，失败以红色 `→ error` 打印并回灌给模型。

`run_bash` 每条命令执行前都会问 `允许执行? [Y/n/a]`：**回车或 `y` 允许这一次**，`n` 拒绝（作为错误回灌给模型），
`a` 本会话内不再询问 `run_bash`；Ctrl-D 视为拒绝，输错会再问一次。
命令 30 秒超时（连子进程一起杀），输出超过 10000 字符截断。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek key，放 `.env`（照 `.env.example`）或 export |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 可换 `deepseek-v4-pro` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 一般不用改 |
| `MINI_HARNESS_MEMORY_DIR` | `Memory` | 记忆笔记目录，相对路径按工作目录解析：默认即 `<项目>/Memory`（已在 `.gitignore`，只留本地）。目录不存在就不加载 |

DeepSeek 默认开思考模式；代码里 `reasoning_effort="low"`，改 `providers.py` 里的构造参数即可调。

## 与 PDF 代码清单的差异

PDF 用的是 Anthropic SDK；本仓库换成 DeepSeek 的 OpenAI 兼容协议，线上格式有四处不同，全部收在 `providers.py`：

| 点 | PDF（Anthropic 协议） | 本仓库（DeepSeek / OpenAI 兼容协议） |
|---|---|---|
| 工具声明 | `{name, description, input_schema}` | `{"type":"function","function":{name, description, parameters}}`；`Tool` 本身不知道哪家 API |
| assistant 回灌 | `message.content` 块列表原样回灌 | `content + tool_calls + reasoning_content`（思考模式带 tools 时必须回灌） |
| 工具结果回灌 | 一条 user 消息，内含多个 `tool_result` 块，带 `is_error` | 每个结果一条 `role=tool` 消息；没有 `is_error`，用 `Error:` 前缀 |
| 模型 | `claude-sonnet-5` | `deepseek-v4-flash`（`DEEPSEEK_MODEL` 可换） |

循环的形状（`need_user_input` 那个布尔量）、三个工具的语义、`list_files` 的剪枝，全部与 PDF 一致。

在原文之上加的东西：

- **动态 system prompt**（`prompt.py`）：`build_system_prompt(PromptContext)` 是纯函数，按运行时状态拼出
  `# Agency` / `# Guardrails` / `# Verification` / `# Project Instructions` 各段——哪些段出现取决于挂了哪些工具
  （`--step 2` 只有 `read_file`，就不谈编辑和验证）。请求时拼成最前面一条 `role=system` 消息，不存进 conversation。
- **`AGENTS.md`**：启动时若工作目录里有这个文件，就注入为 `# Project Instructions`（超过 20000 字符截断）。
- **记忆索引**：启动时把 `Memory/`（或 `MINI_HARNESS_MEMORY_DIR` 指定的目录）下最近 20 篇 `.md`（按修改时间，最多 3000 字符）
  列成 `# Memory` 段，每条「日期 标题 — 路径」，**只有索引不含正文**，并标明「可能过时、先读原文再依赖」。
  只在启动时读一次，会话中途不重建 system prompt，保住前缀缓存。记忆本身不进仓库；就是普通 Markdown 文件，不依赖任何第三方记忆工具。
- **记忆写入**：有 `edit_file` 时，`# Memory` 段还带写入规则（**没有任何笔记时也出现**，否则第一篇永远写不出来）：
  用户说「记住」、或得出以后仍然成立的结论时写一篇；一个主题一个文件 `Memory/<slug>.md`，首行 `# 标题`（索引只显示这行）+ `Date:`；
  同主题已有笔记就更新而不是重复建；**不存任务进度、待办清单**（课程 9.1：跨会话的清单会变成陈旧的垃圾抽屉）、
  **不存机密**；全体贡献者都该遵守的规则建议写进 AGENTS.md（课程 3.4）。不需要新工具。
  实测：让它记住「发布口令」，它按规则拒绝保存，只提议记「口令存在哪」。
  harness 是通用的，项目自己的命令和约定由项目自己说；本仓库也带了一份。
- **`edit_file` 更严**：`old_str` 必须唯一命中；空 `old_str` 不会覆盖非空的已有文件。
- **坏参数不崩溃**：模型给的工具参数不是合法 JSON 对象时，作为错误结果回灌，而不是让程序退出。
- **二进制文件不崩溃**：`read_file` / `edit_file` 读到非 UTF-8 文件（图片、PDF）时作为错误结果回灌。
- **工具调用上限**：同一次用户输入后最多连续 20 轮工具调用（`--max-rounds N` 可调），
  到了就暂停交回给你，回复「继续」接着做——防止模型原地打转烧 token。
- **终端渲染 Markdown**：模型回复用 `rich` 渲染；system prompt 的 `# Communication` 段告诉模型
  「输出会按 Markdown 渲染在一个窄终端里」，让它用列表、代码块，少用宽表格和 HTML。
- **上下文管理**（参考 Vercel 课程模块 5）：
  1. 遥测：每次调用后 stderr 一行 `· in 4,210 (cached 3,800) · out 120`
  2. 有界输出：`read_file` 每次最多 500 行（`offset` / `limit` 分页），`list_files` 最多 500 项，
     `run_bash` 保留最后 1 万字符（报错在结尾）；截断都会告诉模型还剩多少
  3. 修剪：上次输入超过 6 万 token 时，把最近 5 个之外的旧工具结果**一次性**换成占位符。
     不每轮滑动修剪，因为 DeepSeek 按前缀自动缓存，改一条旧消息其后缓存全失效。
     实测：预算要明显大于「保留的结果」本身，否则退化成每轮修剪；保留太少，模型会把清掉的文件重新读一遍。
- **`run_bash` + 确认**：`Tool.needs_approval=True` 的工具执行前由 agent 询问用户 `[Y/n/a]`；
  「本会话都允许」的状态存在 `ConsoleApprover` 对象里，每个会话一个，Agent 只看 `approve(call) -> bool`。

## 测试

```bash
uv run pytest                                  # 快速测试，全用 fake，不打真实 API
uvx ruff check src tests && uvx ruff format --check src tests
uv run --env-file .env pytest -m live         # live 冒烟测试：打真实 DeepSeek API，要 key、要花钱
```

`tests/test_live.py` 里是 fake 证明不了的东西：DeepSeek 接不接受我们的消息顺序（暂停后接「继续」、修剪后的 conversation），
以及模型会不会照 prompt 行事（翻页、被拒后不重试、用 AGENTS.md 回答）。默认跳过，没 key 时自动 skip；
改了 provider / prompt / 工具描述 / 修剪之后手动跑一次。

每次 push 到 `main` 和每个 PR，GitHub Actions 会在干净的 Ubuntu 上自动跑同样的检查（`.github/workflows/ci.yml`）。
测试不打真实 API，所以 CI 不需要 API key。
