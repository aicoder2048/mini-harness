# mini-harness

跟学 Thorsten Ball《How to Build an Agent》的 Python 复现（对应 `docs/Thorsten-Ball-构建Agent-Python跟学版.pdf`），
默认 Provider 从 Anthropic 换成了 **DeepSeek**（OpenAI 兼容协议），Anthropic 协议保留作对照。

约 300 行、三个工具、一个循环：一个能读、能找、能改你代码的 agent。

## 目录

```
src/
  step1_chat.py   第 1 步：聊天循环（还不是 agent），直接调 OpenAI 兼容 SDK
  tools.py        三个工具 read_file / list_files / edit_file + 手写 JSON Schema（与 provider 无关）
  providers.py    DeepSeekProvider（默认）/ AnthropicProvider：两家协议差异全收在这里
  agent.py        第 2–4 步：agent 循环，--step 控制工具集，--provider 切换协议
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

## 切换 Provider

```bash
# 原教程的 Anthropic 协议（需要 ANTHROPIC_API_KEY，默认模型 claude-sonnet-5）
uv run src/agent.py --provider anthropic

# 零改代码的第三种玩法：让 anthropic SDK 打 DeepSeek 的 Anthropic 兼容端点
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic \
ANTHROPIC_API_KEY=$DEEPSEEK_API_KEY \
ANTHROPIC_MODEL=deepseek-v4-flash \
uv run --env-file .env src/agent.py --provider anthropic
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | （必填） | DeepSeek key，放 `.env` 或 export |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | 可换 `deepseek-v4-pro` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 一般不用改 |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` | — | anthropic SDK 自己读 |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | 仅 `--provider anthropic` 时用 |

DeepSeek 默认开思考模式；代码里 `reasoning_effort="low"`，改 `providers.py` 里的构造参数即可调。

## 与 PDF 代码清单的差异

| 点 | PDF | 本仓库 | 为什么 |
|---|---|---|---|
| Provider | 只有 Anthropic | 默认 DeepSeek，Anthropic 保留 | 用户要求；两家协议差异集中在 `providers.py` |
| 工具声明 | `Tool.to_api()` 直接产 Anthropic 格式 | `Tool` 只存四要素，由 provider 包装 | 工具层不该知道哪家 API |
| assistant 回灌 | `message.content` 块列表 | DeepSeek：`content + tool_calls + reasoning_content`；Anthropic：同 PDF | DeepSeek 思考模式带 tools 时必须回灌 `reasoning_content` |
| 工具结果回灌 | 一条 user 消息，内含 `tool_result` 块 | DeepSeek：每个结果一条 `role=tool` 消息；Anthropic：同 PDF | 协议不同；OpenAI 格式没有 `is_error`，用 `Error:` 前缀 |
| 模型 | `claude-sonnet-5` | `deepseek-v4-flash` | 便宜、快；`DEEPSEEK_MODEL` 可换 |

循环的形状（`need_user_input` 那个布尔量）、三个工具的语义、`edit_file` 的唯一性检查、`list_files` 的剪枝，全部与 PDF 一致。

## 测试

```bash
uv run pytest
uvx ruff check src tests && uvx ruff format --check src tests
```
