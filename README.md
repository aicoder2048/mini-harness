# mini-harness

跟学 Thorsten Ball《How to Build an Agent》的 Python 复现（对应 `docs/Thorsten-Ball-构建Agent-Python跟学版.pdf`），
模型 Provider 用 **DeepSeek**（OpenAI 兼容协议）。

约 400 行、四个工具、一个循环：一个能读、能找、能改你代码、能跑命令的 agent。
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

## 目录

```
src/
  step1_chat.py   第 1 步：聊天循环（还不是 agent），直接调 OpenAI 兼容 SDK
  tools.py        四个工具 read_file / list_files / edit_file / run_bash + 手写 JSON Schema
  providers.py    DeepSeekProvider：工具声明 / assistant 回灌 / 工具结果回灌的线上格式全收在这里
  prompt.py       system prompt：由工作目录 / 工具集 / git 分支 / AGENTS.md 拼出的分段 prompt
  agent.py        第 2–5 步：agent 循环 + 危险工具确认，--step 控制工具集
tests/            pytest，不打真实 API（fake client / fake provider）
AGENTS.md         给 agent 看的项目说明（命令、架构、约定、踩过的坑）
.env.example      环境变量示例；复制成 .env 再填 key（.env 已在 .gitignore，不会提交）
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

`run_bash` 每条命令执行前都会问 `允许执行? [y/N]`，只有 `y` / `yes` 放行；拒绝会作为错误回灌给模型。
命令 30 秒超时（连子进程一起杀），输出超过 10000 字符截断。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek key，放 `.env`（照 `.env.example`）或 export |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 可换 `deepseek-v4-pro` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 一般不用改 |

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
  harness 是通用的，项目自己的命令和约定由项目自己说；本仓库也带了一份。
- **`edit_file` 更严**：`old_str` 必须唯一命中；空 `old_str` 不会覆盖非空的已有文件。
- **坏参数不崩溃**：模型给的工具参数不是合法 JSON 对象时，作为错误结果回灌，而不是让程序退出。
- **二进制文件不崩溃**：`read_file` / `edit_file` 读到非 UTF-8 文件（图片、PDF）时作为错误结果回灌。
- **工具调用上限**：同一次用户输入后最多连续 20 轮工具调用（`agent.py` 的 `MAX_TOOL_ROUNDS`），
  到了就暂停交回给你，回复「继续」接着做——防止模型原地打转烧 token。
- **`run_bash` + 确认**：`Tool.needs_approval=True` 的工具执行前由 agent 询问用户。

## 测试

```bash
uv run pytest
uvx ruff check src tests && uvx ruff format --check src tests
```
