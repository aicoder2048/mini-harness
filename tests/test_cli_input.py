"""终端输入测试：用 prompt_toolkit 的管道输入直接喂按键字节（含 Shift+Enter 的转义序列），不用真按键盘。"""

import threading
import time

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from cli_input import SlashCommandCompleter, make_reader

COMMANDS = {"exit": "退出本会话和程序", "quit": "同 /exit", "help": "帮助"}


@pytest.fixture
def keys():
    """keys(*chunks) -> 读一次输入的结果。每个 chunk 是终端发来的原始字节。"""
    with create_pipe_input() as inp:
        read = make_reader(COMMANDS, input=inp, output=DummyOutput())

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


# --- 斜杠命令补全 ----------------------------------------------------------------


def _complete(text):
    doc = Document(text, cursor_position=len(text))
    return list(SlashCommandCompleter(COMMANDS).get_completions(doc, CompleteEvent(text_inserted=True)))


def test_slash_alone_offers_every_command_with_its_description():
    completions = _complete("/")
    assert [c.text for c in completions] == ["/exit", "/quit", "/help"]
    assert completions[0].display_meta_text == "退出本会话和程序"


@pytest.mark.parametrize("typed,expected", [("/q", ["/quit"]), ("/E", ["/exit"]), ("/x", [])])
def test_prefix_filters_case_insensitively(typed, expected):
    assert [c.text for c in _complete(typed)] == expected


def test_completion_replaces_the_whole_typed_token():
    [c] = _complete("/qu")
    assert c.start_position == -3  # 用 /quit 整个替换已输入的 /qu


@pytest.mark.parametrize(
    "text",
    [
        "hello",
        "hello /q",  # 不在开头
        "/exit now",  # 已经在输参数了
        "/Users/",  # 路径，不是命令
        "",
    ],
)
def test_no_completions_outside_a_leading_slash_word(text):
    assert _complete(text) == []


def _type_slowly(*chunks):
    """像真人一样按键：补全是异步算的，一口气把所有字节塞进去的话，Enter 会赶在补全结果出来之前处理。"""
    with create_pipe_input() as inp:
        read = make_reader(COMMANDS, input=inp, output=DummyOutput())

        def typist():
            for chunk in chunks:
                time.sleep(0.1)
                inp.send_text(chunk)

        threading.Thread(target=typist, daemon=True).start()
        return read()


def test_tab_then_enter_completes_and_submits_the_command():
    assert _type_slowly("/qu", "\t", ENTER) == "/quit"


def test_arrow_down_selects_from_menu_then_enter_submits_it():
    assert _type_slowly("/", "\x1b[B", "\x1b[B", ENTER) == "/quit"  # 菜单顺序 exit、quit、help


def test_enter_without_selecting_submits_what_was_typed(keys):
    assert keys("/qu", ENTER) == "/qu"
