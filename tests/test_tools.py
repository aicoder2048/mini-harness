"""工具层测试：只通过 Tool.run() 这个公共接口验证行为。"""

import json
import time

import pytest

import tools
from tools import ALL_TOOLS, ToolError, edit_file, list_files, read_file, run_bash


def test_all_tools_are_in_tutorial_order():
    assert [t.name for t in ALL_TOOLS] == ["read_file", "list_files", "edit_file", "run_bash"]


# --- read_file ---------------------------------------------------------------


def test_read_file_returns_file_content(tmp_path):
    (tmp_path / "a.txt").write_text("hello 老周", encoding="utf-8")
    assert read_file.run({"path": str(tmp_path / "a.txt")}) == "hello 老周"


def test_read_file_missing_raises_tool_error(tmp_path):
    with pytest.raises(ToolError):
        read_file.run({"path": str(tmp_path / "nope.txt")})


# --- list_files --------------------------------------------------------------


def test_list_files_returns_sorted_json_with_trailing_slash_dirs(tmp_path):
    (tmp_path / "b.py").write_text("")
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.md").write_text("")

    out = json.loads(list_files.run({"path": str(tmp_path)}))

    assert out == ["a.txt", "b.py", "sub/", "sub/c.md"]


def test_list_files_prunes_git_and_venv(tmp_path):
    for d in (".git", "__pycache__", ".venv", "venv"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "junk").write_text("")
    (tmp_path / "keep.py").write_text("")

    out = json.loads(list_files.run({"path": str(tmp_path)}))

    assert out == ["keep.py"]


def test_list_files_defaults_to_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.txt").write_text("")
    assert json.loads(list_files.run({})) == ["x.txt"]


# --- edit_file ---------------------------------------------------------------


def test_edit_file_replaces_unique_match(tmp_path):
    f = tmp_path / "f.js"
    f.write_text("fizzBuzz(100);\n")

    assert edit_file.run({"path": str(f), "old_str": "100", "new_str": "15"}) == "OK"
    assert f.read_text() == "fizzBuzz(15);\n"


def test_edit_file_empty_old_str_creates_file_and_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "new.txt"

    msg = edit_file.run({"path": str(target), "old_str": "", "new_str": "content"})

    assert target.read_text() == "content"
    assert "created" in msg


def test_edit_file_empty_old_str_refuses_to_overwrite_existing_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("precious")
    with pytest.raises(ToolError, match="already exists"):
        edit_file.run({"path": str(f), "old_str": "", "new_str": "oops"})
    assert f.read_text() == "precious"


def test_edit_file_empty_old_str_fills_existing_empty_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("")
    edit_file.run({"path": str(f), "old_str": "", "new_str": "content"})
    assert f.read_text() == "content"


def test_edit_file_rejects_missing_old_str(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("abc")
    with pytest.raises(ToolError, match="not found"):
        edit_file.run({"path": str(f), "old_str": "zzz", "new_str": "y"})


def test_edit_file_rejects_ambiguous_old_str(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("return None\nreturn None\n")
    with pytest.raises(ToolError, match="matched 2 times"):
        edit_file.run({"path": str(f), "old_str": "return None", "new_str": "return 1"})
    assert f.read_text() == "return None\nreturn None\n"  # 文件未被改坏


def test_edit_file_rejects_identical_old_and_new(tmp_path):
    with pytest.raises(ToolError, match="must differ"):
        edit_file.run({"path": str(tmp_path / "f"), "old_str": "a", "new_str": "a"})


# --- run_bash ----------------------------------------------------------------


def test_run_bash_needs_approval_and_others_do_not():
    assert [t.name for t in ALL_TOOLS if t.needs_approval] == ["run_bash"]


def test_run_bash_returns_stdout_and_stderr():
    out = run_bash.run({"command": "echo hello; echo oops >&2"})
    assert "hello" in out and "oops" in out


def test_run_bash_empty_output_is_explicit():
    assert run_bash.run({"command": "true"}) == "(no output)"


def test_run_bash_nonzero_exit_is_tool_error_with_output():
    with pytest.raises(ToolError, match="exit code 3") as e:
        run_bash.run({"command": "echo partial; exit 3"})
    assert "partial" in str(e.value)


def test_run_bash_timeout_kills_whole_process_group(monkeypatch):
    monkeypatch.setattr(tools, "BASH_TIMEOUT", 0.5)
    start = time.monotonic()
    with pytest.raises(ToolError, match="timed out"):
        run_bash.run({"command": "sleep 5; echo done"})  # sleep 是 sh 的孙进程，只杀 sh 会卡住等管道
    assert time.monotonic() - start < 3


def test_run_bash_truncates_long_output(monkeypatch):
    monkeypatch.setattr(tools, "MAX_OUTPUT_CHARS", 10)
    out = run_bash.run({"command": "printf '%0100d' 0"})
    assert out.startswith("0" * 10) and "truncated 90 chars" in out
