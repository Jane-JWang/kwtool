from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from playwright.sync_api import BrowserContext, Download, Page

from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_download, save_pdf_bytes


PRINT_HEADER = ('<div style="width:100%;font-size:8px;color:#111;padding:0 0.35in;'
                'display:flex;justify-content:space-between">'
                '<span class="date"></span><span class="title"></span><span style="width:90px"></span></div>')
PRINT_FOOTER = ('<div style="width:100%;font-size:8px;color:#111;padding:0 0.35in;'
                'display:flex;justify-content:space-between">'
                '<span class="url" style="max-width:85%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></span>'
                '<span><span class="pageNumber"></span>/<span class="totalPages"></span></span></div>')


class RunCancelled(Exception):
    """The user stopped the current batch."""


class SiteUnavailable(ConnectionError):
    """The site cannot be reached, so retrying each row would be wasteful."""


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
    report_user_action: Callable[[bool], None] = lambda _needed: None

    def check_stop(self) -> None:
        if self.stop_event.is_set():
            raise RunCancelled()

    def wait_until(self, ready: Callable[[], bool], message: str, timeout_seconds: float = 300,
                   user_action: bool = False) -> None:
        """Wait for a site-specific completion signal, while allowing cancellation."""
        import time

        self.report_status(message)
        if user_action:
            self.report_user_action(True)
        deadline = time.monotonic() + timeout_seconds
        try:
            while not ready():
                self.check_stop()
                if time.monotonic() >= deadline:
                    raise TimeoutError(message)
                time.sleep(0.25)
            self.check_stop()
        finally:
            if user_action:
                self.report_user_action(False)

    def save_pdf(self, page: Page | None = None, *, margin: dict[str, str] | None = None) -> Path:
        self.check_stop()
        settings = self.pdf_settings.validated()
        pdf = (page or self.page).pdf(
            format="A4",
            landscape=settings.landscape,
            scale=settings.scale,
            display_header_footer=True,
            header_template=PRINT_HEADER,
            footer_template=PRINT_FOOTER,
            print_background=settings.print_background,
            prefer_css_page_size=False,
            margin=margin or {side: f"{settings.margin_mm}mm" for side in ("top", "right", "bottom", "left")},
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
    default_pdf_settings = PdfSettings(print_background=False)

    @abstractmethod
    def run_one(self, context: RunContext, subject: str) -> None:
        """Inspect a single subject and save its artifacts through context."""
