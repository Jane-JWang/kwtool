from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Download


TOKEN = re.compile(r"\[([^\[\]]+)\]")
INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
ALLOWED_TOKENS = {"date", "id", "object", "website"}


def safe_filename(value: str, *, limit: int = 80) -> str:
    cleaned = INVALID.sub("_", value).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)[:limit].rstrip(" .")
    if not cleaned:
        cleaned = "未命名"
    if cleaned.upper().split(".")[0] in RESERVED:
        cleaned = f"_{cleaned}"
    return cleaned


def validate_template(template: str) -> str:
    template = template.strip()
    if not template:
        raise ValueError("请输入输出路径模板")
    tokens = set(TOKEN.findall(template))
    unknown = tokens - ALLOWED_TOKENS
    if unknown:
        raise ValueError(f"未知占位符：{', '.join(sorted(unknown))}")
    if "[" in TOKEN.sub("", template) or "]" in TOKEN.sub("", template):
        raise ValueError("输出模板中的方括号格式不正确")
    if Path(template).suffix.lower() != ".pdf":
        raise ValueError("输出路径模板必须以 .pdf 结尾")
    if any(part == ".." for part in Path(template).parts):
        raise ValueError("输出路径不能包含 ..；如需其他目录，请输入绝对路径")
    sample = TOKEN.sub("sample", template)
    sample_path = Path(sample)
    for part in sample_path.parts:
        if part == sample_path.anchor:
            continue
        if INVALID.search(part) or part.rstrip(" .") != part or part.upper().split(".")[0] in RESERVED:
            raise ValueError(f"输出路径包含 Windows 不支持的名称：{part}")
    return template


@dataclass(frozen=True)
class OutputPlan:
    base_dir: Path
    template: str
    created_at: datetime

    def __post_init__(self) -> None:
        validate_template(self.template)

    def render(self, index: int, subject: str, website: str) -> Path:
        values = {
            "date": self.created_at.strftime("%y%m%d"),
            "id": f"{index:03d}",
            "object": safe_filename(subject),
            "website": safe_filename(website),
        }
        rendered = TOKEN.sub(lambda match: values[match.group(1)], self.template.strip())
        path = Path(rendered)
        if not path.is_absolute():
            path = self.base_dir / path
        return path


def available_path(path: Path) -> Path:
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{suffix}{path.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1


def save_pdf_bytes(pdf: bytes, requested: Path) -> Path:
    requested.parent.mkdir(parents=True, exist_ok=True)
    candidate = available_path(requested)
    while True:
        try:
            with candidate.open("xb") as stream:
                stream.write(pdf)
            return candidate
        except FileExistsError:
            candidate = available_path(candidate)
        except Exception:
            candidate.unlink(missing_ok=True)
            raise


def save_download(download: Download, pdf_path: Path) -> Path:
    folder = pdf_path.with_name(f"{pdf_path.stem}_附件")
    folder.mkdir(parents=True, exist_ok=True)
    suggested = Path(download.suggested_filename).name
    suffix = Path(suggested).suffix
    if len(suffix) > 16:
        suffix = ""
    suffix = INVALID.sub("_", suffix)
    filename = f"{safe_filename(suggested[:-len(suffix)] if suffix else suggested, limit=160 - len(suffix))}{suffix}"
    destination = available_path(folder / filename)
    download.save_as(destination)
    return destination
