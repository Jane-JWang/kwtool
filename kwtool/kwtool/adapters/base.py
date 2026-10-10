from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from playwright.sync_api import BrowserContext, Download, Page

from kwtool.models import FieldSpec, PdfSettings
from kwtool.diagnostics import capture_page
from kwtool.output import save_download, save_pdf_bytes


PRINT_HEADER = ('<div style="width:100%;font-size:8px;color:#111;padding:0 0.35in;'
                'display:flex;justify-content:space-between">'
                '<span>{datetime}</span><span class="title"></span><span style="width:90px"></span></div>')


def print_header(now: datetime) -> str:
    return PRINT_HEADER.format(datetime=f"{now.year}/{now.month}/{now.day} {now:%H:%M}")


PRINT_FOOTER = ('<div style="width:100%;font-size:8px;color:#111;padding:0 0.35in;'
                'box-sizing:border-box;position:relative">'
                '<span class="url" style="display:block;width:80%;overflow:hidden;'
                'text-overflow:ellipsis;white-space:nowrap"></span>'
                '<span style="position:absolute;right:0.35in;top:0;white-space:nowrap">'
                '<span class="pageNumber"></span>/<span class="totalPages"></span></span></div>')


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
    site_settings: dict[str, bool | str | int | float]
    stop_event: Event
    report_status: Callable[[str], None]
    artifacts: list[Path]
    report_user_action: Callable[[bool], None] = lambda _needed: None
    diagnostics_dir: Path | None = None
    subject_kwargs: dict[str, str] = field(default_factory=dict)

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
                # Playwright dispatches popup/navigation events while a sync API
                # call is running. Sleeping in Python can leave new tabs unseen.
                self.page.wait_for_timeout(250)
            self.check_stop()
        finally:
            if user_action:
                self.report_user_action(False)

    def render_pdf(self, page: Page | None = None, *, margin: dict[str, str] | None = None) -> bytes:
        self.check_stop()
        settings = self.pdf_settings.validated()
        pdf = (page or self.page).pdf(
            format="A4",
            landscape=settings.landscape,
            scale=settings.scale,
            display_header_footer=True,
            header_template=print_header(datetime.now()),
            footer_template=PRINT_FOOTER,
            print_background=settings.print_background,
            prefer_css_page_size=False,
            margin=margin or {side: f"{settings.margin_mm}mm" for side in ("top", "right", "bottom", "left")},
        )
        self.check_stop()
        return pdf

    def save_pdf(self, page: Page | None = None, *, margin: dict[str, str] | None = None) -> Path:
        pdf = self.render_pdf(page, margin=margin)
        saved = save_pdf_bytes(pdf, self.pdf_path)
        self.pdf_path = saved
        self.artifacts.append(saved)
        return saved

    def save_download(self, download: Download) -> Path:
        self.check_stop()
        saved = save_download(download, self.pdf_path)
        self.artifacts.append(saved)
        return saved

    def capture_diagnostic(self, label: str, page: Page | None = None) -> None:
        if self.diagnostics_dir is None:
            return
        import logging

        try:
            capture_page(page or self.page, self.diagnostics_dir, label)
        except Exception:
            logging.getLogger("kwtool").exception("无法保存诊断快照：%s", label)


class SiteAdapter(ABC):
    key: str
    display_name: str
    settings_schema: tuple[FieldSpec, ...] = ()
    default_pdf_settings = PdfSettings(print_background=False)
    required_kwargs: tuple[str, ...] = ()
    optional_kwargs: tuple[str, ...] = ()

    @abstractmethod
    def run_one(self, context: RunContext, subject: str) -> None:
        """Inspect a single subject and save its artifacts through context."""
