"""第 2–5 步：把聊天循环变成 agent。

    uv run src/agent.py --step 2               # + read_file
    uv run src/agent.py --step 3               # + list_files
    uv run src/agent.py --step 4               # + edit_file（原文到此为止）
    uv run src/agent.py --step 5               # + run_bash（每条命令先问你，默认）

整个 agent 就是这一个循环：
  用户输入 → 模型 → 模型说要用工具？→ 执行 → 结果回灌 → 再问模型 → ⋯
                   ↘ 没有工具调用 → 打印答复，回到用户输入

chatbot 和 agent 在代码上的唯一区别，是下面那个 need_user_input 布尔量。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable

from rich.console import Console
from rich.markdown import Markdown

from prompt import (
    AGENTS_FILE,
    PromptContext,
    build_system_prompt,
    current_git_branch,
    load_memory_index,
    load_project_context,
)
from providers import DeepSeekProvider, Provider, ToolCall, ToolResult, Usage
from tools import ALL_TOOLS, Tool, ToolError

# 同一次用户输入之后，最多连续几轮「模型要工具 → 执行 → 回灌」。防止模型原地打转、烧 token。
# 到上限就暂停交回给用户；教程（Vercel Academy harness）里对应 stopWhen: stepCountIs(10)。
MAX_TOOL_ROUNDS = 20

# 上下文管理：上一次请求的输入超过预算，就把「最近几个之外」的旧工具结果一次性换成占位符。
# 故意不每轮都修剪：DeepSeek 按前缀自动缓存，改动一条旧消息，它之后的缓存全部失效。
# 超预算才批量清理一次，之后前缀又稳定下来。
CONTEXT_BUDGET = 60_000  # 输入 token
KEEP_TOOL_RESULTS = 5

console = Console()  # 只用来把模型回复渲染成 Markdown；其余输出仍是普通 print


def prompt_user() -> str | None:
    """读一行用户输入；Ctrl-D 返回 None。"""
    try:
        return input("\033[94mYou\033[0m: ")
    except EOFError:
        return None


class ConsoleApprover:
    """needs_approval 的工具执行前在终端问 [Y/n/a]。

    回车 / y：允许这一次；n：拒绝；a：本会话内这个工具都不再询问。
    「本会话都允许」的状态存在这个对象里，所以 Agent 只需要 approve(call) -> bool，
    每个 Agent 各自 new 一个，互不串会话。Ctrl-D 视为拒绝：输入已经结束时不该默认放行。
    """

    PROMPT = "\033[95m      允许执行? [Y/n/a]（回车=允许，a=本会话都允许 {tool}）\033[0m "

    def __init__(self, read: Callable[[str], str] = input) -> None:
        self.read = read
        self.always: set[str] = set()  # 本会话内不再询问的工具名

    def __call__(self, call: ToolCall) -> bool:
        if call.name in self.always:
            print(f"\033[2m      （本会话已允许 {call.name}，自动执行）\033[0m")
            return True
        while True:
            try:
                answer = self.read(self.PROMPT.format(tool=call.name)).strip().lower()
            except EOFError:
                return False
            if answer in ("", "y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            if answer in ("a", "all", "always"):
                self.always.add(call.name)
                return True
            # 拼错（比如 yse）既不当允许也不当拒绝，再问一次


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: list[Tool],
        get_user_input: Callable[[], str | None] = prompt_user,
        system: str = "",
        approve: Callable[[ToolCall], bool] | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
        context_budget: int = CONTEXT_BUDGET,
        keep_tool_results: int = KEEP_TOOL_RESULTS,
    ) -> None:
        if max_tool_rounds < 1:
            raise ValueError(f"max_tool_rounds must be >= 1, got {max_tool_rounds}")
        self.provider = provider
        self.tools = {t.name: t for t in tools}
        self.get_user_input = get_user_input
        self.system = system
        self.approve = approve or ConsoleApprover()  # 每个 Agent 一个新会话，不共享「a」的状态
        self.max_tool_rounds = max_tool_rounds
        self.context_budget = context_budget
        self.keep_tool_results = keep_tool_results

    def _execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用。失败也返回结果，交给模型自己纠错。"""
        print(f"\033[92mtool\033[0m: {call.name}({json.dumps(call.input, ensure_ascii=False)})")
        tool = self.tools.get(call.name)
        try:
            if tool is None:
                raise ToolError(f"unknown tool: {call.name}")
            if call.input_error:
                raise ToolError(call.input_error)
            if tool.needs_approval and not self.approve(call):
                raise ToolError("the user denied this tool call")
            return ToolResult(call.id, tool.run(call.input))
        except ToolError as e:
            error = str(e)
        except KeyError as e:  # 模型漏传了必填参数
            error = f"missing argument: {e}"
        print(f"\033[91m      → error\033[0m: {error}")
        return ToolResult(call.id, error, is_error=True)

    @staticmethod
    def _log_usage(usage: Usage) -> None:
        """每次调模型后一行灰字，打到 stderr：不和回复混在一起。看 in 的数字怎么随轮数涨，就是上下文管理的起点。"""
        line = f"· in {usage.input_tokens:,} (cached {usage.cached_tokens:,}) · out {usage.output_tokens:,}"
        print(f"\033[2m{line}\033[0m", file=sys.stderr)

    def run(self) -> None:
        conversation: list[dict] = []
        tools = list(self.tools.values())
        print(f"Chat with {self.provider.label} — {len(tools)} 个工具可用 (Ctrl-D 退出)")

        need_user_input = True
        tool_rounds = 0  # 自上次用户输入以来连续的工具轮数
        while True:
            if need_user_input:
                user_input = self.get_user_input()
                if user_input is None:
                    break
                if not user_input:
                    continue
                conversation.append({"role": "user", "content": user_input})
                tool_rounds = 0

            # provider.chat 会把 assistant 回复按本家格式 append 进 conversation。
            reply = self.provider.chat(conversation, tools, self.system)

            if reply.usage:
                self._log_usage(reply.usage)
                if reply.usage.input_tokens > self.context_budget:
                    pruned = self.provider.prune_tool_results(conversation, self.keep_tool_results)
                    if pruned:
                        print(
                            f"\033[2m· 上下文超过 {self.context_budget:,} token 预算，清理了 {pruned} 个旧工具结果\033[0m",
                            file=sys.stderr,
                        )
            for text in reply.texts:
                print(f"\033[93m{self.provider.label}\033[0m:")
                console.print(Markdown(text))  # Markdown 按块渲染，所以标签单独一行
            results = [self._execute(call) for call in reply.tool_calls]

            if not results:
                need_user_input = True  # 没工具调用 → 回到用户
                continue

            conversation.extend(self.provider.tool_results(results))
            tool_rounds += 1
            if tool_rounds >= self.max_tool_rounds:
                # 结果已经回灌，每个 tool_call id 都有回复；用户下一句（比如「继续」）接在 tool 消息后面即可。
                print(f"\033[91m已连续 {tool_rounds} 轮工具调用，先暂停交回给你；回复「继续」让它接着做。\033[0m")
                need_user_input = True
                continue
            need_user_input = False  # 有工具调用 → 不等用户，直接再问模型


def tools_for_step(step: int) -> list[Tool]:
    """2=read_file, 3=+list_files, 4=+edit_file, 5=+run_bash"""
    return ALL_TOOLS[: step - 1]


def _positive_int(text: str) -> int:
    value = int(text)  # 非数字抛 ValueError，argparse 会报成 invalid value
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {value}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="跟学版 code-editing agent")
    p.add_argument(
        "--step",
        type=int,
        choices=[2, 3, 4, 5],
        default=5,
        help="2=read_file, 3=+list_files, 4=+edit_file, 5=+run_bash",
    )
    p.add_argument(
        "--max-rounds",
        type=_positive_int,
        default=MAX_TOOL_ROUNDS,
        help=f"一次用户输入后最多连续几轮工具调用，到了暂停交回给你（默认 {MAX_TOOL_ROUNDS}）",
    )
    return p.parse_args(argv)


def _memory_index(cwd: str) -> str | None:
    """MINI_HARNESS_MEMORY_DIR 指向笔记目录；不设就不加载。相对路径按工作目录解析（如 Memory → <cwd>/Memory）。"""
    setting = os.environ.get("MINI_HARNESS_MEMORY_DIR")
    if not setting:
        return None
    memory_dir = os.path.normpath(os.path.join(cwd, os.path.expanduser(setting)))
    if not os.path.isdir(memory_dir):
        print(f"\033[91mMINI_HARNESS_MEMORY_DIR 指向的目录不存在：{memory_dir}（本次不加载记忆）\033[0m")
        return None
    index = load_memory_index(memory_dir)
    if index:
        count = sum(line.startswith("- ") for line in index.splitlines())
        print(f"已加载记忆索引：{count} 条（{memory_dir}）")
    return index


def main() -> None:
    args = parse_args()

    cwd = os.getcwd()
    tools = tools_for_step(args.step)
    project_context = load_project_context(cwd)
    if project_context:
        print(f"已加载 {AGENTS_FILE}（{len(project_context)} 字符）作为项目指令")
    ctx = PromptContext(
        working_directory=cwd,
        tool_names=[t.name for t in tools],
        git_branch=current_git_branch(cwd),
        project_context=project_context,
        memory_index=_memory_index(cwd),
    )
    Agent(DeepSeekProvider(), tools, system=build_system_prompt(ctx), max_tool_rounds=args.max_rounds).run()


if __name__ == "__main__":
    main()
