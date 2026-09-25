"""终端输入测试：用 prompt_toolkit 的管道输入直接喂按键字节（含 Shift+Enter 的转义序列），不用真按键盘。"""

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from cli_input import make_reader


@pytest.fixture
def keys():
    """keys(*chunks) -> 读一次输入的结果。每个 chunk 是终端发来的原始字节。"""
    with create_pipe_input() as inp:
        read = make_reader(input=inp, output=DummyOutput())

        def feed(*chunks):
            inp.send_text("".join(chunks))
            return read()

        yield feed


ENTER = "\r"
LEFT = "\x1b[D"
UP = "\x1b[A"


def test_enter_submits(keys):
    assert keys("hello", ENTER) == "hello"


@pytest.mark.parametrize(
    "newline",
    [
        "\x1b[13;2u",  # Shift+Enter，CSI u 格式（kitty / WezTerm / Ghostty / iTerm2 开了 CSI u）
        "\x1b[27;2;13~",  # Shift+Enter，xterm modifyOtherKeys 格式（prompt_toolkit 默认把它当普通回车）
        "\x1b\r",  # Option / Alt + Enter
        "\n",  # Ctrl+J：任何终端都能用的兜底
    ],
    ids=["shift-enter-csi-u", "shift-enter-xterm", "alt-enter", "ctrl-j"],
)
def test_newline_keys_insert_a_line_break_instead_of_submitting(keys, newline):
    assert keys("hello", newline, "world", ENTER) == "hello\nworld"


def test_backslash_then_enter_continues_on_next_line(keys):
    # 和 Claude Code 一样：行尾打 \ 再回车 = 换行（反斜杠本身不留下）
    assert keys("hello\\", ENTER, "world", ENTER) == "hello\nworld"


def test_left_arrow_moves_cursor_for_editing(keys):
    assert keys("helo", LEFT, "l", ENTER) == "hello"


def test_up_arrow_recalls_previous_input(keys):
    assert keys("first", ENTER) == "first"
    assert keys(UP, ENTER) == "first"


def test_ctrl_d_on_empty_line_means_quit(keys):
    assert keys("\x04") is None


def test_ctrl_c_clears_the_line_instead_of_crashing(keys):
    assert keys("half typed", "\x03") == ""
