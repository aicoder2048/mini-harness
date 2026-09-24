"""工具定义与三个内置工具：read_file / list_files / edit_file。

对应 PDF 第七章 tools.py。这一层与模型 Provider 无关：Tool 只描述
name / description / input_schema / run 四要素，具体发给哪家 API、
用什么 JSON 外壳，由 providers.py 负责包装。

原文用 Go 的结构体 + 反射生成 JSON Schema；某些 Python 移植版写成
`from jsonschema import generate_schema`——那个函数并不存在。这里直接手写
schema：零额外依赖，也更透明——模型看到的本来就只是这段 JSON。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], str]


class ToolError(Exception):
    """工具执行失败。会被包成 is_error 的结果回灌给模型，让它自己纠错。"""


# --- read_file ---------------------------------------------------------------


def _read_file(args: dict[str, Any]) -> str:
    path = args["path"]
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ToolError(str(e)) from e


read_file = Tool(
    name="read_file",
    description=(
        "Read the contents of a given relative file path. "
        "Use this when you want to see what's inside a file. "
        "Do not use this with directory names."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The relative path of a file in the working directory.",
            }
        },
        "required": ["path"],
    },
    run=_read_file,
)


# --- list_files --------------------------------------------------------------

_PRUNED_DIRS = {".git", "__pycache__", ".venv", "venv"}


def _list_files(args: dict[str, Any]) -> str:
    root = args.get("path") or "."
    try:
        out: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            # 原地修改 dirnames 是 os.walk 的剪枝约定；写成 dirnames = [...] 无效。
            dirnames[:] = [d for d in dirnames if d not in _PRUNED_DIRS]
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir != ".":
                out.append(rel_dir + "/")
            for name in filenames:
                out.append(name if rel_dir == "." else os.path.join(rel_dir, name))
        return json.dumps(sorted(out), ensure_ascii=False)
    except OSError as e:
        raise ToolError(str(e)) from e


list_files = Tool(
    name="list_files",
    description=(
        "List files and directories at a given path. "
        "If no path is provided, lists files in the current directory. "
        "Directories are returned with a trailing slash."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional relative path. Defaults to the current directory.",
            }
        },
        "required": [],
    },
    run=_list_files,
)


# --- edit_file ---------------------------------------------------------------


def _edit_file(args: dict[str, Any]) -> str:
    path, old, new = args["path"], args["old_str"], args["new_str"]
    if old == new:
        raise ToolError("old_str and new_str must differ")

    if old == "":  # 空 old_str 约定为「新建文件」
        # 原文没做这个检查。不做的话模型传个空 old_str 就能把整个已有文件清掉重写。
        if os.path.exists(path) and os.path.getsize(path) > 0:
            raise ToolError(f"{path} already exists; use a non-empty old_str to edit it")
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return f"Successfully created file {path}"

    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise ToolError(str(e)) from e

    occurrences = content.count(old)
    if occurrences == 0:
        raise ToolError("old_str not found in file")
    if occurrences > 1:
        # 原文没做这个检查。不做的话模型一次替换会命中多处，改坏文件还沉默通过。
        raise ToolError(f"old_str matched {occurrences} times; make it unique")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content.replace(old, new))
    return "OK"


edit_file = Tool(
    name="edit_file",
    description=(
        "Make edits to a text file.\n\n"
        "Replaces 'old_str' with 'new_str' in the given file. "
        "'old_str' and 'new_str' MUST be different from each other, and 'old_str' "
        "must match exactly one place in the file.\n\n"
        "If the file specified with path doesn't exist and 'old_str' is empty, "
        "it will be created. An empty 'old_str' never overwrites a non-empty existing file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "The path to the file"},
            "old_str": {
                "type": "string",
                "description": "Text to search for - must match exactly and appear exactly once",
            },
            "new_str": {"type": "string", "description": "Text to replace old_str with"},
        },
        "required": ["path", "old_str", "new_str"],
    },
    run=_edit_file,
)


ALL_TOOLS = [read_file, list_files, edit_file]
