"""Skill 发现与解析测试：Agent Skills 标准（SKILL.md + YAML frontmatter），不依赖 PyYAML。"""

import pytest

import skills
from skills import discover_skills, parse_frontmatter, skills_index


def _skill(root, name, frontmatter, body="# Body\n"):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return d


# --- frontmatter：已安装的 58 个 skill 里实际出现的 4 种 description 写法 ---------


def test_single_line_values():
    fm = parse_frontmatter("---\nname: stock-quote\ndescription: Get a quote. Use for 股价.\n---\nbody")
    assert fm == {"name": "stock-quote", "description": "Get a quote. Use for 股价."}


@pytest.mark.parametrize(
    "raw,expected",
    [('"Quoted: with colon"', "Quoted: with colon"), ("'It''s single'", "It's single")],
)
def test_quoted_values(raw, expected):
    assert parse_frontmatter(f"---\nname: x\ndescription: {raw}\n---\n")["description"] == expected


def test_folded_block_joins_lines_with_spaces():
    text = "---\nname: x\ndescription: >\n  first line\n  second line\nlicense: MIT\n---\n"
    fm = parse_frontmatter(text)
    assert fm["description"] == "first line second line"
    assert fm["license"] == "MIT"


def test_folded_block_blank_line_becomes_paragraph_break():
    # YAML 标准：折叠块里的空行保留为换行（agent-reach 就是这么写的，和 PyYAML 对照发现）
    text = "---\nname: x\ndescription: >\n  para one\n  still one\n\n  para two\n---\n"
    assert parse_frontmatter(text)["description"] == "para one still one\npara two"


def test_folded_strip_block():
    assert parse_frontmatter("---\nname: x\ndescription: >-\n  a\n  b\n---\n")["description"] == "a b"


def test_literal_block_keeps_newlines():
    assert parse_frontmatter("---\nname: x\ndescription: |\n  a\n  b\n---\n")["description"] == "a\nb"


def test_plain_multiline_continuation():
    assert parse_frontmatter("---\nname: x\ndescription: one\n  two\n---\n")["description"] == "one two"


def test_nested_mapping_does_not_break_following_keys():
    text = "---\nname: x\nmetadata:\n  version: 1\n  author: me\ndescription: after nested\n---\n"
    assert parse_frontmatter(text)["description"] == "after nested"


def test_no_frontmatter_is_none():
    assert parse_frontmatter("# just markdown\n") is None
    assert parse_frontmatter("---\nname: x\n(never closed)\n") is None


# --- discover_skills -----------------------------------------------------------


def test_discovers_skills_one_level_down(tmp_path):
    _skill(tmp_path, "b-skill", "name: b-skill\ndescription: B")
    _skill(tmp_path, "a-skill", "name: a-skill\ndescription: A")
    (tmp_path / "not-a-skill").mkdir()
    found, warnings = discover_skills([str(tmp_path)])
    assert [s.name for s in found] == ["a-skill", "b-skill"]
    assert found[0].path == str(tmp_path / "a-skill" / "SKILL.md")
    assert warnings == []


def test_a_dir_can_point_directly_at_one_skill(tmp_path):
    d = _skill(tmp_path, "stock-quote", "name: stock-quote\ndescription: quotes")
    _skill(tmp_path, "other", "name: other\ndescription: not wanted")
    found, _ = discover_skills([str(d)])
    assert [s.name for s in found] == ["stock-quote"]


def test_first_dir_wins_on_duplicate_names(tmp_path):
    project = tmp_path / "project"
    extra = tmp_path / "extra"
    _skill(project, "run-checks", "name: run-checks\ndescription: project version")
    _skill(extra, "run-checks", "name: run-checks\ndescription: global version")
    found, warnings = discover_skills([str(project), str(extra)])
    assert [s.description for s in found] == ["project version"]
    assert any("run-checks" in w for w in warnings)


def test_missing_dirs_are_ignored(tmp_path):
    assert discover_skills([str(tmp_path / "nope")]) == ([], [])


@pytest.mark.parametrize(
    "frontmatter,reason",
    [("description: no name", "name"), ("name: only-name", "description"), ("name: [broken", "name")],
)
def test_invalid_skill_is_skipped_with_a_warning(tmp_path, frontmatter, reason):
    _skill(tmp_path, "bad", frontmatter)
    _skill(tmp_path, "good", "name: good\ndescription: fine")
    found, warnings = discover_skills([str(tmp_path)])
    assert [s.name for s in found] == ["good"]
    assert len(warnings) == 1 and "bad" in warnings[0] and reason in warnings[0]


def test_invocation_controls_follow_claude_code(tmp_path):
    _skill(tmp_path, "manual", "name: manual\ndescription: m\ndisable-model-invocation: true")
    _skill(tmp_path, "background", "name: background\ndescription: b\nuser-invocable: false")
    found = {s.name: s for s in discover_skills([str(tmp_path)])[0]}
    assert (found["manual"].model_invocable, found["manual"].user_invocable) == (False, True)
    assert (found["background"].model_invocable, found["background"].user_invocable) == (True, False)


# --- skills_index --------------------------------------------------------------


def test_index_lists_model_invocable_skills_one_line_each(tmp_path):
    _skill(tmp_path, "quote", "name: quote\ndescription: Get quotes.")
    _skill(tmp_path, "manual", "name: manual\ndescription: m\ndisable-model-invocation: true")
    index = skills_index(discover_skills([str(tmp_path)])[0])
    assert index == f"- quote: Get quotes. — {tmp_path / 'quote' / 'SKILL.md'}"  # manual 不进索引


def test_index_truncates_long_descriptions(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "MAX_DESCRIPTION_CHARS", 10)
    _skill(tmp_path, "long", "name: long\ndescription: " + "x" * 50)
    assert "xxxxxxxxxx… —" in skills_index(discover_skills([str(tmp_path)])[0])


def test_index_caps_total_size_and_says_how_many_more(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "MAX_INDEX_CHARS", 120)
    for i in range(5):
        _skill(tmp_path, f"s{i}", f"name: s{i}\ndescription: {'d' * 30}")
    lines = skills_index(discover_skills([str(tmp_path)])[0]).splitlines()
    assert sum(len(line) + 1 for line in lines[:-1]) <= 120
    assert "more" in lines[-1]


def test_index_is_none_without_model_invocable_skills(tmp_path):
    _skill(tmp_path, "manual", "name: manual\ndescription: m\ndisable-model-invocation: true")
    assert skills_index(discover_skills([str(tmp_path)])[0]) is None
    assert skills_index([]) is None
