# -*- coding: utf-8 -*-
"""读取前端的数据层 —— 让题目与文案只有一份真源。

web/data/questions.js 与 interpretations.js 的形式是 `window.XXX = <纯JSON>;`，
所以这里只需要剥掉前缀与尾分号就能 json.loads。

配套约束（改那两个文件时必须守住，否则后端会读不到）：
  - 赋值号右边必须是合法 JSON：key 带引号、对象内部不写注释、无尾逗号。
  - 注释只能写在 `window.XXX = ` 之前的块注释里。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "web" / "data"

# 赋值语句出现在行首，所以必须用 search + ^ + re.M：
#   match() 永远从字符串位置 0 开始，而这两个文件都以块注释开头，用 match() 必然失配。
# 注释里可能原样出现“window.QUIZ_DATA = ”这个字面串（questions.js 就是这么写的），
# 但它们前面有“ * ”前缀，不构成行首，因此不会被误判为赋值语句。
_ASSIGN = re.compile(r"^window\.([A-Z_][A-Za-z0-9_]*)\s*=\s*", re.M)


def _load_assigned_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    m = _ASSIGN.search(text)
    if not m:
        raise ValueError(f"{path.name} 找不到行首的 `window.XXX = {{...}};` 赋值语句，后端无法解析")
    body = text[m.end():].strip()
    body = body.rstrip()
    if body.endswith(";"):
        body = body[:-1]
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path.name} 赋值号右边的内容不是合法 JSON：{exc}。"
            "（检查 key 是否都带引号、对象内是否混入了注释或尾逗号）"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} 解析结果不是对象")
    return data


def load_questions() -> dict[str, Any]:
    return _load_assigned_json(DATA_DIR / "questions.js")


def load_texts() -> dict[str, Any]:
    return _load_assigned_json(DATA_DIR / "interpretations.js")
