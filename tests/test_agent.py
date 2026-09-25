"""Agent 循环测试：注入 fake provider 和脚本化输入，只看循环的形状。"""

import pytest

from agent import (
    MAX_TOOL_ROUNDS,
    Agent,
    ConsoleApprover,
    _memory,
    _skills,
    parse_args,
    slash_command,
    tools_for_step,
)
from providers import Reply, ToolCall, ToolResult, Usage
from tools import Tool, read_file


class FakeProvider:
    label = "Fake"

    def __init__(self, replies):
        self.replies = list(replies)
        self.chats: list[list[dict]] = []  # 每次 chat 收到的 conversation 快照
        self.systems: list[str] = []
        self.results: list[list[ToolResult]] = []
        self.prunes: list[int] = []  # 每次 prune_tool_results 收到的 keep_last

    def chat(self, conversation, tools, system=""):
        self.chats.append(list(conversation))
        self.systems.append(system)
        reply = self.replies.pop(0)
        conversation.append({"role": "assistant", "content": reply.texts})
        return reply

    def prune_tool_results(self, conversation, keep_last):
        self.prunes.append(keep_last)
        return 1

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


def test_reply_markdown_is_rendered_not_printed_raw(capsys):
    provider = FakeProvider([Reply(texts=["**bold** and `code`"])])
    Agent(provider, [], scripted("hi")).run()
    out = capsys.readouterr().out
    assert "bold" in out and "code" in out
    assert "**" not in out and "`" not in out


def test_token_usage_is_logged_to_stderr_not_stdout(capsys):
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(4210, 120, 3800))])
    Agent(provider, [], scripted("go")).run()
    out, err = capsys.readouterr()
    assert "in 4,210" in err and "cached 3,800" in err and "out 120" in err
    assert "4,210" not in out


def test_no_usage_line_when_provider_reports_none(capsys):
    Agent(FakeProvider([Reply(texts=["hi"])]), [], scripted("go")).run()
    assert capsys.readouterr().err == ""


def test_prunes_old_tool_results_when_input_exceeds_budget(capsys):
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(1001, 10))])
    Agent(provider, [], scripted("go"), context_budget=1000, keep_tool_results=5).run()
    assert provider.prunes == [5]
    assert "清理" in capsys.readouterr().err


def test_no_pruning_within_budget():
    provider = FakeProvider([Reply(texts=["hi"], usage=Usage(1000, 10))])
    Agent(provider, [], scripted("go"), context_budget=1000).run()
    assert provider.prunes == []


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


def _tool_reply(i):
    return Reply(tool_calls=[ToolCall(f"c{i}", "read_file", {"path": "/nonexistent"})])


def test_tool_rounds_cap_pauses_and_hands_back_to_user(capsys):
    provider = FakeProvider([_tool_reply(i) for i in range(10)])

    Agent(provider, [read_file], scripted("go"), max_tool_rounds=3).run()

    assert len(provider.chats) == 3  # 第 3 轮后不再问模型，回到用户（输入已用完 → 退出）
    assert len(provider.results) == 3  # 每轮的工具结果都回灌了，tool_call id 没有落单
    assert "3 轮" in capsys.readouterr().out


def test_tool_rounds_counter_resets_on_user_input():
    replies = [_tool_reply(1), _tool_reply(2), _tool_reply(3), Reply(texts=["done"])]
    provider = FakeProvider(replies)

    Agent(provider, [read_file], scripted("go", "继续"), max_tool_rounds=2).run()

    assert len(provider.chats) == 4  # 没重置的话，第 3 轮之后就又被暂停了
    assert provider.chats[2][-1] == {"role": "user", "content": "继续"}


def test_max_tool_rounds_must_be_positive():
    with pytest.raises(ValueError):
        Agent(FakeProvider([]), [], scripted(), max_tool_rounds=0)


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


def test_cli_defaults():
    args = parse_args([])
    assert (args.step, args.max_rounds) == (5, MAX_TOOL_ROUNDS)


def test_cli_max_rounds():
    assert parse_args(["--max-rounds", "5"]).max_rounds == 5


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_cli_rejects_non_positive_max_rounds(bad):
    with pytest.raises(SystemExit):
        parse_args(["--max-rounds", bad])


# --- ConsoleApprover：[Y/n/a] 审批 ---------------------------------------------


def _answers(*answers):
    """脚本化的键盘输入；记录被问了几次。用完抛 EOFError（相当于 Ctrl-D）。"""
    it = iter(answers)
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return read, prompts


def _call(name="run_bash"):
    return ToolCall("c1", name, {"command": "ls"})


@pytest.mark.parametrize("answer", ["", "y", "Y", "yes", "  y  "])
def test_approver_enter_or_y_allows_once(answer):
    read, _ = _answers(answer)
    assert ConsoleApprover(read)(_call()) is True


@pytest.mark.parametrize("answer", ["n", "N", "no"])
def test_approver_n_denies(answer):
    read, _ = _answers(answer)
    assert ConsoleApprover(read)(_call()) is False


def test_approver_ctrl_d_denies():
    read, _ = _answers()  # 没有输入 → EOFError
    assert ConsoleApprover(read)(_call()) is False


def test_approver_unrecognized_answer_asks_again():
    read, prompts = _answers("yse", "y")
    assert ConsoleApprover(read)(_call()) is True
    assert len(prompts) == 2


def test_approver_always_stops_asking_for_that_tool_this_session():
    read, prompts = _answers("a")
    approver = ConsoleApprover(read)
    assert approver(_call()) is True
    assert approver(_call()) is True
    assert approver(_call()) is True
    assert len(prompts) == 1  # 只问了第一次


def test_approver_always_is_per_tool():
    read, prompts = _answers("a", "n")
    approver = ConsoleApprover(read)
    assert approver(_call("run_bash")) is True
    assert approver(_call("deploy")) is False  # 别的工具照样要问
    assert len(prompts) == 2


def test_each_agent_gets_a_fresh_approver_session():
    # 默认审批器不能是模块级共享对象，否则上一个会话的「a」会带到下一个会话
    a1 = Agent(FakeProvider([]), [], scripted())
    a2 = Agent(FakeProvider([]), [], scripted())
    assert a1.approve is not a2.approve


# --- 记忆目录：默认 <cwd>/Memory ------------------------------------------------


def test_memory_defaults_to_memory_dir_in_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_HARNESS_MEMORY_DIR", raising=False)
    (tmp_path / "Memory").mkdir()
    (tmp_path / "Memory" / "n.md").write_text("# 项目笔记")
    memory_dir, index = _memory(str(tmp_path))
    assert memory_dir == str(tmp_path / "Memory")
    assert "项目笔记" in index


def test_missing_default_memory_dir_is_silent_but_still_writable(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MINI_HARNESS_MEMORY_DIR", raising=False)
    # 还没记过东西：没有索引，但目录仍然给出——第一篇笔记由 edit_file 连目录一起建出来
    assert _memory(str(tmp_path)) == (str(tmp_path / "Memory"), None)
    assert capsys.readouterr().out == ""


def test_explicit_missing_memory_dir_warns_and_disables_memory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_HARNESS_MEMORY_DIR", "nope")
    # 显式配置写错了：提醒，并且不让 agent 往一个拼错的新目录里写
    assert _memory(str(tmp_path)) == (None, None)
    assert "不存在" in capsys.readouterr().out


def test_explicit_relative_memory_dir_resolves_against_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("MINI_HARNESS_MEMORY_DIR", "notes")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "n.md").write_text("# 另一个目录")
    memory_dir, index = _memory(str(tmp_path))
    assert memory_dir == str(tmp_path / "notes") and "另一个目录" in index


# --- 斜杠命令 ------------------------------------------------------------------


@pytest.mark.parametrize("text", ["/exit", "/quit", "  /quit  ", "/EXIT", "/exit now"])
def test_exit_and_quit_end_the_session_without_calling_the_model(text):
    provider = FakeProvider([])
    Agent(provider, [], scripted(text, "never sent")).run()
    assert provider.chats == []


def test_exit_after_some_conversation_stops_there():
    provider = FakeProvider([Reply(texts=["hi"])])
    Agent(provider, [], scripted("hello", "/quit", "never sent")).run()
    assert len(provider.chats) == 1


def test_unknown_command_is_not_sent_to_model_and_lists_commands(capsys):
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/foo", "hello")).run()
    assert provider.chats[0] == [{"role": "user", "content": "hello"}]  # /foo 没进 conversation
    out = capsys.readouterr().out
    assert "/foo" in out and "/exit" in out and "/quit" in out


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/exit", "exit"),
        ("/Quit", "quit"),
        ("/help me", "help"),
        ("/Users/szou/a.py 看看这个", None),  # 路径不是命令：照常发给模型
        ("/", None),
        ("/123", None),
        ("hello /exit", None),
        ("", None),
    ],
)
def test_slash_command_parsing(text, expected):
    assert slash_command(text) == expected


def test_path_starting_with_slash_goes_to_the_model():
    provider = FakeProvider([Reply(texts=["ok"])])
    Agent(provider, [], scripted("/Users/szou/a.py 看看这个")).run()
    assert provider.chats[0][0]["content"] == "/Users/szou/a.py 看看这个"


# --- skill 目录：默认 <cwd>/skills，另加 MINI_HARNESS_SKILL_DIRS -------------------


def _write_skill(root, name, desc="d"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")
    return d


def test_skills_default_to_project_skills_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("MINI_HARNESS_SKILL_DIRS", raising=False)
    _write_skill(tmp_path / "skills", "run-checks")
    assert [s.name for s in _skills(str(tmp_path))] == ["run-checks"]


def test_no_skills_dir_is_silent(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MINI_HARNESS_SKILL_DIRS", raising=False)
    assert _skills(str(tmp_path)) == []
    assert capsys.readouterr().out == ""


def test_extra_skill_dirs_are_added_after_project_and_can_be_single_skills(tmp_path, monkeypatch):
    _write_skill(tmp_path / "skills", "run-checks", "project")
    single = _write_skill(tmp_path / "elsewhere", "stock-quote")
    _write_skill(tmp_path / "global", "run-checks", "global")  # 重名：项目内的优先
    monkeypatch.setenv("MINI_HARNESS_SKILL_DIRS", f"{single}:global")  # 相对路径按工作目录解析
    found = {s.name: s.description for s in _skills(str(tmp_path))}
    assert found == {"run-checks": "project", "stock-quote": "d"}


def test_explicit_missing_skill_dir_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MINI_HARNESS_SKILL_DIRS", "nope")
    assert _skills(str(tmp_path)) == []
    assert "不存在" in capsys.readouterr().out
