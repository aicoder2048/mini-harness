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
from collections.abc import Callable

from rich.console import Console
from rich.markdown import Markdown

from prompt import AGENTS_FILE, PromptContext, build_system_prompt, current_git_branch, load_project_context
from providers import DeepSeekProvider, Provider, ToolCall, ToolResult
from tools import ALL_TOOLS, Tool, ToolError

# 同一次用户输入之后，最多连续几轮「模型要工具 → 执行 → 回灌」。防止模型原地打转、烧 token。
# 到上限就暂停交回给用户；教程（Vercel Academy harness）里对应 stopWhen: stepCountIs(10)。
MAX_TOOL_ROUNDS = 20

console = Console()  # 只用来把模型回复渲染成 Markdown；其余输出仍是普通 print


def prompt_user() -> str | None:
    """读一行用户输入；Ctrl-D 返回 None。"""
    try:
        return input("\033[94mYou\033[0m: ")
    except EOFError:
        return None


def ask_approval(call: ToolCall) -> bool:
    """needs_approval 的工具执行前问一句；只有明确 y/yes 才放行，Ctrl-D 视为拒绝。"""
    try:
        answer = input("\033[95m      允许执行? [y/N]\033[0m ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: list[Tool],
        get_user_input: Callable[[], str | None] = prompt_user,
        system: str = "",
        approve: Callable[[ToolCall], bool] = ask_approval,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        if max_tool_rounds < 1:
            raise ValueError(f"max_tool_rounds must be >= 1, got {max_tool_rounds}")
        self.provider = provider
        self.tools = {t.name: t for t in tools}
        self.get_user_input = get_user_input
        self.system = system
        self.approve = approve
        self.max_tool_rounds = max_tool_rounds

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
    )
    Agent(DeepSeekProvider(), tools, system=build_system_prompt(ctx), max_tool_rounds=args.max_rounds).run()


if __name__ == "__main__":
    main()
