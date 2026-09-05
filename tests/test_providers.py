"""Provider 层测试：注入 fake client，只验证「发出去的格式」和「回灌的格式」。"""

from types import SimpleNamespace as NS

import pytest

from providers import DeepSeekProvider, Reply, ToolCall, ToolResult
from tools import read_file


class FakeOpenAI:
    """记录 chat.completions.create 收到的参数，返回预设消息。"""

    def __init__(self, message):
        self.calls: list[dict] = []
        self.chat = NS(completions=NS(create=self._create))
        self._message = message

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return NS(choices=[NS(message=self._message)])


def _message(**overrides):
    base = {"content": None, "reasoning_content": None, "tool_calls": None}
    base.update(overrides)
    return NS(**base)


def test_declares_tools_in_openai_function_wrapper():
    client = FakeOpenAI(_message(content="hi"))
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


def test_omits_tools_param_when_no_tools():
    client = FakeOpenAI(_message(content="hi"))
    DeepSeekProvider(client=client, model="m").chat([{"role": "user", "content": "x"}], [])
    assert "tools" not in client.calls[0]


def test_normalizes_reply_and_appends_assistant_with_reasoning():
    msg = _message(
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
        "reasoning_content": "thinking...",  # 思考模式 + tools 时必须原样回灌
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.txt"}'}}
        ],
    }


def test_tool_results_become_role_tool_messages():
    provider = DeepSeekProvider(client=FakeOpenAI(_message()), model="m")
    out = provider.tool_results([ToolResult("c1", "file body"), ToolResult("c2", "no such file", is_error=True)])
    assert out == [
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "tool", "tool_call_id": "c2", "content": "Error: no such file"},
    ]


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider()


def test_model_defaults_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    assert DeepSeekProvider(client=FakeOpenAI(_message())).model == "deepseek-v4-pro"
