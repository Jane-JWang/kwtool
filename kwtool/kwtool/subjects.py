from __future__ import annotations

_ARGUMENT_PUNCTUATION = str.maketrans({"：": ":", "，": ",", "、": ","})


def parse_subject(value: str) -> tuple[str, dict[str, str]]:
    """Parse a subject and optional trailing square-bracket arguments."""
    text = value.strip()
    if not text:
        raise ValueError("主体名称不能为空")

    # Parentheses are common in legal entity names, so only a final [...] is
    # interpreted as kwargs. For example, 容诚会计师事务所（特殊普通合伙） stays intact.
    if not text.endswith("]"):
        if "[" in text or "]" in text:
            raise ValueError(f"主体参数格式错误：{value}")
        return text, {}
    separator = text.rfind("[")
    if separator < 0:
        raise ValueError(f"主体参数格式错误：{value}")
    name = text[:separator].strip()
    raw_arguments = text[separator + 1:-1].translate(_ARGUMENT_PUNCTUATION)
    if not name or "[" in raw_arguments or "]" in raw_arguments:
        raise ValueError(f"主体参数格式错误：{value}")
    arguments: dict[str, str] = {}
    if not raw_arguments.strip():
        return name, arguments
    for item in raw_arguments.split(","):
        parts = item.split(":", 1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            raise ValueError(f"主体参数格式错误：{value}")
        key, argument = (part.strip() for part in parts)
        if key in arguments:
            raise ValueError(f"主体参数重复：{key}")
        arguments[key] = argument
    return name, arguments
