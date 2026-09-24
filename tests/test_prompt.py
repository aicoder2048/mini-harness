"""System prompt 测试：builder 是纯函数，同样的 PromptContext 永远得到同样的 prompt。"""

import subprocess

import pytest

import prompt
from prompt import PromptContext, build_system_prompt, current_git_branch, load_project_context

STEP2 = ["read_file"]
STEP4 = ["read_file", "list_files", "edit_file"]
STEP5 = ["read_file", "list_files", "edit_file", "run_bash"]


def _prompt(tool_names, **kw):
    return build_system_prompt(PromptContext(working_directory="/some/project", tool_names=tool_names, **kw))


def test_role_line_has_working_directory_and_actual_tool_names():
    p = _prompt(STEP4)
    assert "/some/project" in p
    assert "Available tools: read_file, list_files, edit_file" in p


def test_agency_section_always_present():
    assert "# Agency" in _prompt(STEP2)
    assert "Do NOT explain what you WOULD do" in _prompt(STEP2)


def test_read_only_tool_set_has_no_editing_guardrails_or_verification():
    p = _prompt(STEP2)
    assert "before editing" not in p
    assert "# Guardrails" not in p
    assert "# Verification" not in p
    assert "denies" not in p


def test_edit_file_adds_guardrails_but_not_verification():
    p = _prompt(STEP4)
    assert "before editing" in p
    assert "# Guardrails" in p
    assert "# Verification" not in p  # 没法跑命令，就没法验证


def test_run_bash_adds_verification_and_denial_rule():
    p = _prompt(STEP5)
    assert "# Verification" in p
    assert 'Do NOT claim "tests pass"' in p
    assert "denies" in p


@pytest.mark.parametrize("names", [STEP2, STEP4, STEP5])
def test_no_git_branch_line_when_unknown(names):
    assert "Git branch" not in _prompt(names)


def test_git_branch_line_when_known():
    assert "Git branch at startup: feature-x" in _prompt(STEP2, git_branch="feature-x")


def test_no_project_section_without_project_context():
    assert "# Project Instructions" not in _prompt(STEP5)


def test_project_context_appended_last():
    p = _prompt(STEP5, project_context="- run tests with `make check`")
    assert "# Project Instructions (from AGENTS.md)" in p
    assert p.endswith("- run tests with `make check`")


# --- current_git_branch ------------------------------------------------------


def test_current_git_branch_outside_repo_is_none(tmp_path):
    assert current_git_branch(str(tmp_path)) is None


def test_current_git_branch_reads_branch_even_before_first_commit(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "feature-x", str(tmp_path)], check=True)
    assert current_git_branch(str(tmp_path)) == "feature-x"


# --- load_project_context ----------------------------------------------------


def test_no_agents_md_is_none(tmp_path):
    assert load_project_context(str(tmp_path)) is None


def test_agents_md_content_is_returned(tmp_path):
    (tmp_path / "AGENTS.md").write_text("# Project\n- 用 `make check` 验证\n", encoding="utf-8")
    assert load_project_context(str(tmp_path)) == "# Project\n- 用 `make check` 验证"


def test_blank_agents_md_is_none(tmp_path):
    (tmp_path / "AGENTS.md").write_text("  \n\n")
    assert load_project_context(str(tmp_path)) is None


def test_agents_md_directory_is_ignored(tmp_path):
    (tmp_path / "AGENTS.md").mkdir()
    assert load_project_context(str(tmp_path)) is None


def test_oversized_agents_md_is_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "MAX_PROJECT_CONTEXT_CHARS", 10)
    (tmp_path / "AGENTS.md").write_text("x" * 25)
    out = load_project_context(str(tmp_path))
    assert out.startswith("x" * 10) and "truncated 15 chars" in out
