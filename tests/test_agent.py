"""Agent 循环测试：注入 fake provider 和脚本化输入，只看循环的形状。"""

import pytest

from agent import Agent, tools_for_step
from providers import Reply, ToolCall, ToolResult
from tools import Tool, read_file


class FakeProvider:
    label = "Fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.chats: list[list[dict]] = []  # 每次 chat 收到的 conversation 快照
        self.systems: list[str] = []
        self.results: list[list[ToolResult]] = []

    def chat(self, conversation, tools, system=""):
        self.chats.append(list(conversation))
        self.systems.append(system)
        reply = self.replies.pop(0)
        conversation.append({"role": "assistant", "content": reply.texts})
        return reply

    def tool_results(self, results):
        self.results.append(results)
        return [{"role": "tool", "content": r.content} for r in results]


def scripted(*inputs):
    it = iter(inputs)
    return lambda: next(it, None)  # 输入用完 → None（相当于 Ctrl-D）


def test_tool_call_loops_back_to_model_without_asking_user(tmp_path, capsys):
    secret = tmp_path / "secret.txt"
    secret.write_text("neigh")
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(secret)})]),
            Reply(texts=["a horse"]),
        ]
    )

    Agent(provider, [read_file], scripted("solve it")).run()

    assert len(provider.chats) == 2  # 只有一次用户输入，却问了模型两次
    assert provider.results == [[ToolResult("c1", "neigh")]]
    assert provider.chats[1][-1] == {"role": "tool", "content": "neigh"}
    assert "a horse" in capsys.readouterr().out


def test_tool_error_is_fed_back_not_raised(tmp_path):
    provider = FakeProvider(
        [
            Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "missing")})]),
            Reply(texts=["sorry"]),
        ]
    )

    Agent(provider, [read_file], scripted("go")).run()

    [[result]] = provider.results
    assert result.is_error and result.call_id == "c1"


def test_unknown_tool_is_an_error_result():
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "rm_rf", {})]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "unknown tool" in result.content


def test_missing_argument_is_an_error_result():
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "read_file", {})]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "missing argument" in result.content


def test_input_error_is_an_error_result_and_tool_is_not_run(tmp_path):
    bad = ToolCall("c1", "read_file", {}, input_error="invalid JSON")
    provider = FakeProvider([Reply(tool_calls=[bad]), Reply(texts=["ok"])])
    Agent(provider, [read_file], scripted("go")).run()
    [[result]] = provider.results
    assert result.is_error and "invalid JSON" in result.content


def _dangerous_tool(ran: list):
    return Tool("danger", "d", {"type": "object"}, run=lambda args: ran.append(args) or "done", needs_approval=True)


@pytest.mark.parametrize("approved", [True, False])
def test_needs_approval_tool_runs_only_when_user_approves(approved):
    ran, asked = [], []
    provider = FakeProvider([Reply(tool_calls=[ToolCall("c1", "danger", {"x": 1})]), Reply(texts=["ok"])])

    Agent(provider, [_dangerous_tool(ran)], scripted("go"), approve=lambda call: asked.append(call) or approved).run()

    [[result]] = provider.results
    assert [c.id for c in asked] == ["c1"]
    assert ran == ([{"x": 1}] if approved else [])
    assert result.is_error is (not approved)


def test_tools_without_needs_approval_never_ask(tmp_path):
    provider = FakeProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "x")})]), Reply(texts=["ok"])]
    )
    Agent(provider, [read_file], scripted("go"), approve=lambda call: pytest.fail("should not ask")).run()


def test_plain_text_reply_returns_to_user_and_keeps_history():
    provider = FakeProvider([Reply(texts=["hi"]), Reply(texts=["bye"])])

    Agent(provider, [], scripted("hello", "", "again")).run()  # 空行被跳过

    assert len(provider.chats) == 2
    assert provider.chats[1][0] == {"role": "user", "content": "hello"}  # 历史全量重发
    assert provider.chats[1][-1] == {"role": "user", "content": "again"}


def test_system_prompt_is_passed_on_every_model_call(tmp_path):
    provider = FakeProvider(
        [Reply(tool_calls=[ToolCall("c1", "read_file", {"path": str(tmp_path / "x")})]), Reply(texts=["ok"])]
    )
    Agent(provider, [read_file], scripted("go"), system="SYS").run()
    assert provider.systems == ["SYS", "SYS"]


def test_eof_exits_without_calling_model():
    provider = FakeProvider([])
    Agent(provider, [], scripted()).run()
    assert provider.chats == []


@pytest.mark.parametrize(
    "step,names",
    [
        (2, ["read_file"]),
        (3, ["read_file", "list_files"]),
        (4, ["read_file", "list_files", "edit_file"]),
        (5, ["read_file", "list_files", "edit_file", "run_bash"]),
    ],
)
def test_tools_for_step(step, names):
    assert [t.name for t in tools_for_step(step)] == names
