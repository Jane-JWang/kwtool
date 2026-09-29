from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from playwright.sync_api import BrowserContext, Download, Page

from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_download, save_pdf_bytes


class RunCancelled(Exception):
    """The user stopped the current batch."""


@dataclass
class RunContext:
    browser: BrowserContext
    page: Page
    pdf_path: Path
    pdf_settings: PdfSettings
    site_settings: dict[str, bool | str | int]
    stop_event: Event
    report_status: Callable[[str], None]
    request_user_continue: Callable[[str], None]
    artifacts: list[Path]

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise RunCancelled()

    def wait_for_user(self, message: str) -> None:
        self.check_stop()
        self.request_user_continue(message)
        self.check_stop()

    def save_pdf(self, page: Page | None = None) -> Path:
        self.check_stop()
        settings = self.pdf_settings.validated()
        margin = f"{settings.margin_mm}mm"
        pdf = (page or self.page).pdf(
            format="A4",
            landscape=settings.landscape,
            scale=settings.scale,
            margin={"top": margin, "right": margin, "bottom": margin, "left": margin},
            print_background=settings.print_background,
        )
        self.check_stop()
        saved = save_pdf_bytes(pdf, self.pdf_path)
        self.pdf_path = saved
        self.artifacts.append(saved)
        return saved

    def save_download(self, download: Download) -> Path:
        self.check_stop()
        saved = save_download(download, self.pdf_path)
        self.artifacts.append(saved)
        return saved


class SiteAdapter(ABC):
    key: str
    display_name: str
    settings_schema: tuple[FieldSpec, ...] = ()

    @abstractmethod
    def run_one(self, context: RunContext, subject: str) -> None:
        """Inspect a single subject and save its artifacts through context."""
