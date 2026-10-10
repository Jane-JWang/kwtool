from __future__ import annotations

import re


_PUNCTUATION = str.maketrans({"（": "(", "）": ")", "：": ":", "，": ",", "、": ","})


def parse_subject(value: str) -> tuple[str, dict[str, str]]:
    """Parse a subject and optional trailing keyword arguments."""
    normalized = value.translate(_PUNCTUATION).strip()
    if not normalized:
        raise ValueError("主体名称不能为空")
    if "(" not in normalized and ")" not in normalized:
        return normalized, {}
    match = re.fullmatch(r"([^()]*)\(([^()]*)\)", normalized)
    if match is None or not match.group(1).strip():
        raise ValueError(f"主体参数格式错误：{value}")
    name = match.group(1).strip()
    arguments: dict[str, str] = {}
    if not match.group(2).strip():
        return name, arguments
    for item in match.group(2).split(","):
        parts = item.split(":", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError(f"主体参数格式错误：{value}")
        key, argument = (part.strip() for part in parts)
        if key in arguments:
            raise ValueError(f"主体参数重复：{key}")
        arguments[key] = argument
    return name, arguments
