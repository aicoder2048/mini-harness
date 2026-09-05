# mini-harness

跟学 Thorsten Ball《How to Build an Agent》的 Python 复现（对应 `docs/Thorsten-Ball-构建Agent-Python跟学版.pdf`），
模型 Provider 用 **DeepSeek**（OpenAI 兼容协议）。

约 300 行、三个工具、一个循环：一个能读、能找、能改你代码的 agent。

## 目录

```
src/
  step1_chat.py   第 1 步：聊天循环（还不是 agent），直接调 OpenAI 兼容 SDK
  tools.py        三个工具 read_file / list_files / edit_file + 手写 JSON Schema
  providers.py    DeepSeekProvider：工具声明 / assistant 回灌 / 工具结果回灌的线上格式全收在这里
  agent.py        第 2–4 步：agent 循环，--step 控制工具集
tests/            pytest，不打真实 API（fake client / fake provider）
.env              DEEPSEEK_API_KEY=...（已在 .gitignore，权限 600）
```

## 准备

```bash
echo 'DEEPSEEK_API_KEY=sk-...' > .env   # https://platform.deepseek.com
uv sync
```

下面所有命令都用 `uv run --env-file .env` 让 uv 把 `.env` 注入环境；
如果你已经 `export DEEPSEEK_API_KEY=...`，去掉 `--env-file .env` 即可。

## 四步

```bash
uv run --env-file .env src/step1_chat.py        # 第 1 步：连问两轮，第二轮引用第一轮 → 它"记得"
uv run --env-file .env src/agent.py --step 2    # 第 2 步：+ read_file    → 「帮我解开 secret.txt 里的谜题」
uv run --env-file .env src/agent.py --step 3    # 第 3 步：+ list_files   → 「这个目录里的 Python 文件都在干什么？」
uv run --env-file .env src/agent.py --step 4    # 第 4 步：+ edit_file    → 「建一个 fizzbuzz.js，能用 node 跑」
```

Ctrl-D 退出。工具调用会以绿色 `tool:` 行打印，失败以红色 `→ error` 打印并回灌给模型。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek key，放 `.env` 或 export |
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

循环的形状（`need_user_input` 那个布尔量）、三个工具的语义、`edit_file` 的唯一性检查、`list_files` 的剪枝，全部与 PDF 一致。

## 测试

```bash
uv run pytest
uvx ruff check src tests && uvx ruff format --check src tests
```
