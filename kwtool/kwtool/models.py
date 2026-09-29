from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


DEFAULT_TEMPLATE = "Output/[date]/[id]-[object]-[website].pdf"


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: Literal["bool", "text", "integer", "choice"]
    default: bool | str | int
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class PdfSettings:
    landscape: bool = False
    scale: float = 1.0
    margin_mm: int = 10
    print_background: bool = True

    def validated(self) -> PdfSettings:
        if not 0.1 <= self.scale <= 2.0:
            raise ValueError("PDF 缩放比例必须在 10% 至 200% 之间")
        if not 0 <= self.margin_mm <= 50:
            raise ValueError("PDF 页边距必须在 0 至 50 毫米之间")
        return self


@dataclass(frozen=True)
class BatchRequest:
    site_key: str
    subjects: tuple[str, ...]
    output_template: str
    pdf: PdfSettings
    site_settings: dict[str, bool | str | int] = field(default_factory=dict)


@dataclass(frozen=True)
class BrowserLayout:
    x: int
    y: int
    width: int
    height: int
