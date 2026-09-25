"""终端输入：多行编辑、方向键、历史记录（基于 prompt_toolkit）。

    Enter                         发送
    Shift+Enter / Option+Enter    换行（取决于终端发什么，见下）
    Ctrl+J                        换行（任何终端都能用）
    行尾 \\ 再 Enter                换行（和 Claude Code 一样）
    ← → ↑ ↓                       移动光标；在第一行按 ↑ / 最后一行按 ↓ 翻历史
    /                             弹出斜杠命令菜单；Tab / ↑↓ 选中，Enter 补全并发送
    Ctrl+C                        清空这一行      Ctrl+D（空行）  退出

Shift+Enter 的坑：很多终端默认让 Shift+Enter 和 Enter 发一样的 \\r，程序根本分不出来。
能分出来的终端发的是转义序列，常见两种格式，这里都注册成 Ctrl+J（换行）：
    \\x1b[13;2u       CSI u 格式：kitty、WezTerm、Ghostty，iTerm2 开了「Report modifiers using CSI u」
    \\x1b[27;2;13~    xterm modifyOtherKeys 格式（prompt_toolkit 默认把它当普通回车，这里改掉）
你的终端 Shift+Enter 不管用时，用 Option+Enter、Ctrl+J 或行尾反斜杠。
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable, Mapping

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

PROMPT = ANSI("\033[94mYou\033[0m: ")
CONTINUATION = "   … "  # 多行输入时后续行的前缀，和 "You: " 对齐

# 让终端发来的 Shift+Enter 序列变成 Ctrl+J，下面统一把 Ctrl+J 绑成「换行」
ANSI_SEQUENCES["\x1b[13;2u"] = Keys.ControlJ
ANSI_SEQUENCES["\x1b[27;2;13~"] = Keys.ControlJ


class SlashCommandCompleter(Completer):
    """只在输入框开头、第一个词以 / 开头时补全命令名（不区分大小写的前缀匹配），旁边显示说明。

    和 agent.slash_command 的判定一致：词中间再出现 / 就是路径（/Users/...），已经输入了空格就是在写参数，
    这两种都不补全。命令表由调用方传入——cli_input 不 import agent，避免循环引用。
    """

    _TYPING_COMMAND = re.compile(r"/[A-Za-z0-9_-]*")

    def __init__(self, commands: Mapping[str, str]) -> None:
        self.commands = commands

    def get_completions(self, document: Document, complete_event: CompleteEvent) -> Iterable[Completion]:
        typed = document.text_before_cursor
        if not self._TYPING_COMMAND.fullmatch(typed):
            return
        prefix = typed[1:].lower()
        for name, description in self.commands.items():
            if name.startswith(prefix):
                yield Completion(f"/{name}", start_position=-len(typed), display_meta=description)


def _key_bindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        buf = event.current_buffer
        # 不用特判补全菜单：Tab / ↑↓ 选中一项时 prompt_toolkit 已经把它填进输入框，这里照常发送即可
        if buf.document.text_before_cursor.endswith("\\"):  # 行尾反斜杠：去掉它，换行
            buf.delete_before_cursor(1)
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()  # 发送

    @kb.add("c-j")  # Ctrl+J，以及上面映射过来的 Shift+Enter
    @kb.add("escape", "enter")  # Option / Alt + Enter
    def _(event):
        event.current_buffer.insert_text("\n")

    return kb


def make_reader(commands: Mapping[str, str] | None = None, input=None, output=None) -> Callable[[], str | None]:
    """返回 read()：读一次输入。Ctrl+D（空行）返回 None，Ctrl+C 返回 ""（调用方跳过、重新提示）。

    commands：斜杠命令「名字 → 说明」，用于补全。input / output 留给测试注入管道输入；正常使用时不传，就是真终端。
    """
    session = PromptSession(
        completer=SlashCommandCompleter(commands or {}),
        complete_while_typing=True,  # 一敲 / 就弹菜单，不用先按 Tab
        multiline=True,  # 缓冲区能放多行；Enter 由上面的绑定决定「发送」还是「换行」
        key_bindings=_key_bindings(),
        history=InMemoryHistory(),
        prompt_continuation=CONTINUATION,
        input=input,
        output=output,
    )

    def read() -> str | None:
        try:
            return session.prompt(PROMPT)
        except EOFError:
            return None
        except KeyboardInterrupt:
            return ""

    return read


def make_default_reader(commands: Mapping[str, str] | None = None) -> Callable[[], str | None]:
    """真终端用 prompt_toolkit；stdin 不是终端（管道、脚本）时退回普通 input()，一行就是一条输入。"""
    if sys.stdin.isatty():
        return make_reader(commands)

    def read_line() -> str | None:
        try:
            return input("\033[94mYou\033[0m: ")
        except EOFError:
            return None

    return read_line
