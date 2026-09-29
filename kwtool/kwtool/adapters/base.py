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
    artifacts: list[Path]

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise RunCancelled()

    def wait_until(self, ready: Callable[[], bool], message: str, timeout_seconds: float = 300) -> None:
        """Wait for a site-specific completion signal, while allowing cancellation."""
        import time

        self.report_status(message)
        deadline = time.monotonic() + timeout_seconds
        while not ready():
            self.check_stop()
            if time.monotonic() >= deadline:
                raise TimeoutError(message)
            time.sleep(0.25)
        self.check_stop()

    def save_pdf(self, page: Page | None = None) -> Path:
        self.check_stop()
        settings = self.pdf_settings.validated()
        pdf = (page or self.page).pdf(
            format="A4",
            landscape=settings.landscape,
            scale=settings.scale,
            display_header_footer=True,
            print_background=settings.print_background,
            prefer_css_page_size=False,
            margin={"top": "10mm", "right": "10mm", "bottom": "10mm", "left": "10mm"},
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
