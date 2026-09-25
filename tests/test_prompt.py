"""System prompt 测试：builder 是纯函数，同样的 PromptContext 永远得到同样的 prompt。"""

import os
import subprocess

import pytest

import prompt
from prompt import PromptContext, build_system_prompt, current_git_branch, load_memory_index, load_project_context

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
def test_model_is_told_replies_render_as_markdown_in_terminal(names):
    p = _prompt(names)
    assert "# Communication" in p
    assert "renders Markdown" in p


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


# --- load_memory_index -------------------------------------------------------


def _note(d, rel, text, mtime):
    f = d / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    os.utime(f, (mtime, mtime))
    return f


def test_memory_missing_or_empty_dir_is_none(tmp_path):
    assert load_memory_index(str(tmp_path / "nope")) is None
    assert load_memory_index(str(tmp_path)) is None


def test_memory_index_lists_title_date_and_absolute_path_newest_first(tmp_path):
    old = _note(tmp_path, "a.md", "# 旧笔记\n正文", 1_700_000_000)  # 2023-11-14
    new = _note(tmp_path, "b.md", "---\ntitle: x\n---\n\n# 新笔记\n正文", 1_800_000_000)  # 2027-01-15
    out = load_memory_index(str(tmp_path))
    lines = out.splitlines()
    assert lines[0] == f"- 2027-01-15 新笔记 — {new}"
    assert lines[1] == f"- 2023-11-14 旧笔记 — {old}"
    assert "正文" not in out  # 只有索引，不含正文


def test_memory_title_falls_back_to_filename(tmp_path):
    _note(tmp_path, "no-heading.md", "just text", 1_700_000_000)
    assert "no-heading" in load_memory_index(str(tmp_path))


def test_memory_index_is_recursive_but_skips_hidden_dirs_and_non_md(tmp_path):
    _note(tmp_path, "proj/deep.md", "# 深层", 1_700_000_000)
    _note(tmp_path, ".git/x.md", "# 隐藏", 1_700_000_000)
    _note(tmp_path, "notes.txt", "# 不是 md", 1_700_000_000)
    out = load_memory_index(str(tmp_path))
    assert "深层" in out and "隐藏" not in out and "不是 md" not in out


def test_memory_index_caps_entries_and_says_how_many_more(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "MAX_MEMORY_ENTRIES", 2)
    for i in range(5):
        _note(tmp_path, f"n{i}.md", f"# note {i}", 1_700_000_000 + i)
    out = load_memory_index(str(tmp_path))
    assert out.count("\n- ") + out.startswith("- ") == 2
    assert "note 4" in out and "note 3" in out  # 最新的两条
    assert "3 more" in out


def test_memory_index_caps_characters(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "MAX_MEMORY_INDEX_CHARS", 150)
    for i in range(5):
        _note(tmp_path, f"n{i}.md", f"# {'long title ' * 3}{i}", 1_700_000_000 + i)
    out = load_memory_index(str(tmp_path))
    listed = out.splitlines()[:-1]
    assert sum(len(line) + 1 for line in listed) <= 150
    assert "more" in out.splitlines()[-1]


def test_memory_section_in_prompt_is_marked_as_possibly_outdated():
    p = _prompt(STEP5, memory_index="- 2026-09-24 某笔记 — /m/a.md")
    assert "# Memory" in p and "may be outdated" in p
    assert "- 2026-09-24 某笔记 — /m/a.md" in p


def test_no_memory_section_without_index():
    assert "# Memory" not in _prompt(STEP5)


def test_project_context_stays_last_after_memory():
    p = _prompt(STEP5, memory_index="- idx", project_context="PROJECT")
    assert p.endswith("PROJECT")


# --- 记忆写入规则 ---------------------------------------------------------------


def _mem_prompt(tool_names, **kw):
    return _prompt(tool_names, memory_dir="/proj/Memory", today="2026-09-24", **kw)


def test_write_rules_appear_even_before_any_note_exists():
    # 没有索引时也要有写入规则，否则第一篇笔记永远写不出来
    p = _mem_prompt(STEP4)
    assert "# Memory" in p and "No notes yet" in p
    assert "Writing notes" in p
    assert "/proj/Memory/<short-topic-slug>.md" in p
    assert "Date: 2026-09-24" in p


def test_write_rules_steer_what_not_to_save():
    p = _mem_prompt(STEP4)
    assert "Never save secrets" in p
    assert "todo" in p  # 课程 9.1：跨会话的待办清单会变成陈旧的垃圾抽屉
    assert "AGENTS.md" in p  # 全体贡献者都该遵守的规则，建议写进 AGENTS.md


def test_read_only_tool_set_shows_index_but_no_write_rules():
    p = _mem_prompt(STEP2, memory_index="- 2026-09-24 某笔记 — /proj/Memory/a.md")
    assert "某笔记" in p
    assert "Writing notes" not in p


def test_no_memory_section_without_dir_or_index():
    assert "# Memory" not in _prompt(STEP5, today="2026-09-24")


def test_write_rules_say_notes_are_knowledge_not_code():
    # 实际发生过：agent 把几百行分析脚本当「记忆」存进 Memory/（因为 /tmp 会被清空）
    p = _mem_prompt(STEP4)
    assert "not code" in p
    assert "skill" in p  # 可复用的代码应该建议做成 skill
