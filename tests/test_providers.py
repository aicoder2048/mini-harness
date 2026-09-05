"""Provider 层测试：注入 fake client，只验证「发出去的格式」和「回灌的格式」。"""

from types import SimpleNamespace as NS

import pytest

from providers import (
    AnthropicProvider,
    DeepSeekProvider,
    Reply,
    ToolCall,
    ToolResult,
    make_provider,
)
from tools import read_file


class FakeOpenAI:
    """记录 chat.completions.create 收到的参数，返回预设消息。"""

    def __init__(self, message):
        self.calls: list[dict] = []
        create = self._create
        self.chat = NS(completions=NS(create=create))
        self._message = message

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return NS(choices=[NS(message=self._message)])


class FakeAnthropic:
    def __init__(self, content):
        self.calls: list[dict] = []
        self.messages = NS(create=self._create)
        self._content = content

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return NS(content=self._content)


# --- DeepSeek（OpenAI 兼容协议）------------------------------------------------


def _deepseek_message(**overrides):
    base = {"content": None, "reasoning_content": None, "tool_calls": None}
    base.update(overrides)
    return NS(**base)


def test_deepseek_declares_tools_in_openai_function_wrapper():
    client = FakeOpenAI(_deepseek_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [read_file])

    assert client.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": read_file.description,
                "parameters": read_file.input_schema,
            },
        }
    ]


def test_deepseek_omits_tools_param_when_no_tools():
    client = FakeOpenAI(_deepseek_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [])
    assert "tools" not in client.calls[0]


def test_deepseek_normalizes_reply_and_appends_assistant_with_reasoning():
    msg = _deepseek_message(
        content="let me look",
        reasoning_content="thinking...",
        tool_calls=[NS(id="call_1", function=NS(name="read_file", arguments='{"path": "a.txt"}'))],
    )
    client = FakeOpenAI(msg)
    conversation = [{"role": "user", "content": "x"}]

    reply = DeepSeekProvider(client=client, model="m").chat(conversation, [read_file])

    assert reply == Reply(texts=["let me look"], tool_calls=[ToolCall("call_1", "read_file", {"path": "a.txt"})])
    assert conversation[-1] == {
        "role": "assistant",
        "content": "let me look",
        "reasoning_content": "thinking...",  # DeepSeek 思考模式 + tools 时必须原样回灌
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}
        ],
    }


def test_deepseek_tool_results_become_role_tool_messages():
    provider = DeepSeekProvider(client=FakeOpenAI(_deepseek_message()), model="m")
    out = provider.tool_results([ToolResult("c1", "file body"), ToolResult("c2", "no such file", is_error=True)])
    assert out == [
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "tool", "tool_call_id": "c2", "content": "Error: no such file"},
    ]


def test_deepseek_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider()


# --- Anthropic（原教程协议）------------------------------------------------------


def test_anthropic_declares_tools_with_input_schema():
    client = FakeAnthropic([NS(type="text", text="hi")])
    AnthropicProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [read_file])
    assert client.calls[0]["tools"] == [
        {"name": "read_file", "description": read_file.description, "input_schema": read_file.input_schema}
    ]


def test_anthropic_normalizes_reply_and_appends_raw_content_blocks():
    content = [NS(type="text", text="hi"), NS(type="tool_use", id="toolu_1", name="read_file", input={"path": "a"})]
    client = FakeAnthropic(content)
    conversation = [{"role": "user", "content": "x"}]

    reply = AnthropicProvider(client=client, model="m").chat(conversation, [read_file])

    assert reply == Reply(texts=["hi"], tool_calls=[ToolCall("toolu_1", "read_file", {"path": "a"})])
    assert conversation[-1] == {"role": "assistant", "content": content}  # 原样回灌，不压扁成字符串


def test_anthropic_tool_results_go_in_one_user_message():
    provider = AnthropicProvider(client=FakeAnthropic([]), model="m")
    out = provider.tool_results([ToolResult("t1", "ok"), ToolResult("t2", "boom", is_error=True)])
    assert out == [
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False},
                {"type": "tool_result", "tool_use_id": "t2", "content": "boom", "is_error": True},
            ],
        }
    ]


# --- 工厂 ---------------------------------------------------------------------


def test_make_provider_defaults_to_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert isinstance(make_provider(), DeepSeekProvider)


def test_make_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="unknown provider"):
        make_provider("gpt")
