"""第 2–4 步：把聊天循环变成 agent。

    uv run src/agent.py --step 2               # + read_file
    uv run src/agent.py --step 3               # + list_files
    uv run src/agent.py --step 4               # + edit_file（完整版，默认）

整个 agent 就是这一个循环：
  用户输入 → 模型 → 模型说要用工具？→ 执行 → 结果回灌 → 再问模型 → ⋯
                   ↘ 没有工具调用 → 打印答复，回到用户输入

chatbot 和 agent 在代码上的唯一区别，是下面那个 need_user_input 布尔量。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable

from providers import DeepSeekProvider, Provider, ToolCall, ToolResult
from tools import ALL_TOOLS, Tool, ToolError


def prompt_user() -> str | None:
    """读一行用户输入；Ctrl-D 返回 None。"""
    try:
        return input("\033[94mYou\033[0m: ")
    except EOFError:
        return None


class Agent:
    def __init__(
        self,
        provider: Provider,
        tools: list[Tool],
        get_user_input: Callable[[], str | None] = prompt_user,
    ) -> None:
        self.provider = provider
        self.tools = {t.name: t for t in tools}
        self.get_user_input = get_user_input

    def _execute(self, call: ToolCall) -> ToolResult:
        """执行一次工具调用。失败也返回结果，交给模型自己纠错。"""
        print(f"\033[92mtool\033[0m: {call.name}({json.dumps(call.input, ensure_ascii=False)})")
        tool = self.tools.get(call.name)
        try:
            if tool is None:
                raise ToolError(f"unknown tool: {call.name}")
            if call.input_error:
                raise ToolError(call.input_error)
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
        while True:
            if need_user_input:
                user_input = self.get_user_input()
                if user_input is None:
                    break
                if not user_input:
                    continue
                conversation.append({"role": "user", "content": user_input})

            # provider.chat 会把 assistant 回复按本家格式 append 进 conversation。
            reply = self.provider.chat(conversation, tools)

            for text in reply.texts:
                print(f"\033[93m{self.provider.label}\033[0m: {text}")
            results = [self._execute(call) for call in reply.tool_calls]

            if not results:
                need_user_input = True  # 没工具调用 → 回到用户
                continue

            conversation.extend(self.provider.tool_results(results))
            need_user_input = False  # 有工具调用 → 不等用户，直接再问模型


def tools_for_step(step: int) -> list[Tool]:
    """2=read_file, 3=+list_files, 4=+edit_file"""
    return ALL_TOOLS[: step - 1]


def main() -> None:
    p = argparse.ArgumentParser(description="跟学版 code-editing agent")
    p.add_argument("--step", type=int, choices=[2, 3, 4], default=4, help="2=read_file, 3=+list_files, 4=+edit_file")
    args = p.parse_args()

    Agent(DeepSeekProvider(), tools_for_step(args.step)).run()


if __name__ == "__main__":
    main()
