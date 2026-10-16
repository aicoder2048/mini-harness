"""Persona 发现与解析测试：.agents/personas/<id>.md，frontmatter + 正文（正文就是这个角色的提示词）。"""

import pytest

from personas import discover_personas, load_persona

KNOWN = ["read_file", "list_files", "edit_file", "run_bash"]


def _persona(root, filename, frontmatter, body="你是一个测试角色。\n"):
    root.mkdir(parents=True, exist_ok=True)
    path = root / filename
    path.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return path


def test_parses_fields_and_body(tmp_path):
    path = _persona(
        tmp_path,
        "wukong.md",
        "name: wukong\ntitle: 孙悟空\ndescription: 本领最大的程序员\nemoji: 🐒\ntools: read_file, run_bash",
        body="你是孙悟空。\n\n本领大，也要守规矩。\n",
    )
    p = load_persona(str(path), KNOWN)
    assert p.id == "wukong"
    assert p.title == "孙悟空"
    assert p.description == "本领最大的程序员"
    assert p.emoji == "🐒"
    assert p.tool_names == ("read_file", "run_bash")
    assert p.prompt == "你是孙悟空。\n\n本领大，也要守规矩。"
    assert p.label == "🐒 孙悟空"


def test_tool_order_and_spacing_do_not_matter_and_duplicates_collapse(tmp_path):
    path = _persona(tmp_path, "a.md", "name: a\ndescription: d\ntools:  run_bash ,read_file,run_bash")
    assert load_persona(str(path), KNOWN).tool_names == ("read_file", "run_bash")  # 按 KNOWN 的顺序


def test_no_tools_key_means_chat_only(tmp_path):
    path = _persona(tmp_path, "a.md", "name: a\ndescription: d")
    assert load_persona(str(path), KNOWN).tool_names == ()


def test_title_defaults_to_id(tmp_path):
    p = load_persona(str(_persona(tmp_path, "a.md", "name: a\ndescription: d")), KNOWN)
    assert p.title == "a"
    assert p.label == "a"


@pytest.mark.parametrize(
    "frontmatter,body,reason",
    [
        ("description: d", "正文", "name"),
        ("name: Wu Kong\ndescription: d", "正文", "name"),
        ("name: 悟空\ndescription: d", "正文", "name"),
        ("name: a", "正文", "description"),
        ("name: a\ndescription: d", "   \n", "empty"),
        ("name: default\ndescription: d", "正文", "reserved"),
        ("name: reload\ndescription: d", "正文", "reserved"),
    ],
)
def test_invalid_persona_raises_with_reason(tmp_path, frontmatter, body, reason):
    path = _persona(tmp_path, "x.md", frontmatter, body=body)
    with pytest.raises(ValueError, match=reason):
        load_persona(str(path), KNOWN)


def test_unknown_tool_is_rejected_by_name(tmp_path):
    path = _persona(tmp_path, "a.md", "name: a\ndescription: d\ntools: read_file, fly")
    with pytest.raises(ValueError, match="fly"):
        load_persona(str(path), KNOWN)


def test_no_frontmatter_is_rejected(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("你是谁？\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frontmatter"):
        load_persona(str(path), KNOWN)


# --- discover_personas ---------------------------------------------------------


def test_discover_sorts_by_id_and_warns_about_bad_files(tmp_path):
    _persona(tmp_path, "wukong.md", "name: wukong\ndescription: d\ntools: read_file")
    _persona(tmp_path, "bajie.md", "name: bajie\ndescription: d")
    _persona(tmp_path, "broken.md", "name: broken")  # 缺 description
    (tmp_path / "notes.txt").write_text("不是 .md，不看", encoding="utf-8")

    personas, warnings = discover_personas(str(tmp_path), KNOWN)

    assert [p.id for p in personas] == ["bajie", "wukong"]
    assert len(warnings) == 1
    assert "broken.md" in warnings[0]
    assert "description" in warnings[0]


def test_discover_keeps_first_of_duplicate_ids(tmp_path):
    _persona(tmp_path, "a.md", "name: same\ndescription: first")
    _persona(tmp_path, "b.md", "name: same\ndescription: second")
    personas, warnings = discover_personas(str(tmp_path), KNOWN)
    assert [p.description for p in personas] == ["first"]  # 按文件名顺序，先到先得
    assert any("same" in w for w in warnings)


def test_discover_warns_when_file_name_differs_from_id_but_still_loads(tmp_path):
    _persona(tmp_path, "monkey.md", "name: wukong\ndescription: d")
    personas, warnings = discover_personas(str(tmp_path), KNOWN)
    assert [p.id for p in personas] == ["wukong"]
    assert any("monkey" in w for w in warnings)


def test_missing_directory_is_silent(tmp_path):
    assert discover_personas(str(tmp_path / "nope"), KNOWN) == ([], [])
