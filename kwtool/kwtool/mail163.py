"""Download reports from the visible 163 webmail session.

The user signs in on the 163 page. No mailbox credential or IMAP token is read.
Only a message whose visible metadata identifies the company and this batch is
eligible for download. Unknown layouts fail closed rather than saving old mail.
"""

from __future__ import annotations

import re
import time
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

from playwright.sync_api import BrowserContext, Error as PlaywrightError, Frame, Page
from pypdf import PdfReader

from kwtool.adapters.base import RunCancelled
from kwtool.output import save_pdf_bytes


HOME = "https://mail.163.com/"
SENDER = "gsxt@samr.gov.cn"
MAIL_TITLE = "国家企业信用信息公示系统-企业报告"
SEARCH = "[role='search'] input[aria-label='支持邮件全文搜索']"
ROW = ".nl0"
INBOX = "[role='treeitem']"
DATE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})(?:日)?(?:\s+(\d{1,2}):(\d{2}))?")


@dataclass(frozen=True)
class SentReport:
    subject: str
    sent_at: datetime
    pdf_path: Path


def _check_stop(stop: Event) -> None:
    if stop.is_set():
        raise RunCancelled()


def _search_frame(page: Page) -> Frame | None:
    for frame in page.frames:
        try:
            if frame.locator(SEARCH).first.is_visible():
                return frame
        except PlaywrightError:
            continue
    return None


def _open_inbox(page: Page) -> None:
    for frame in page.frames:
        try:
            inbox = frame.locator(INBOX).filter(has_text="收件箱").first
            if inbox.is_visible():
                inbox.click()
                return
        except PlaywrightError:
            continue
    raise RuntimeError("163 邮箱中未找到收件箱入口")


def _message_date(text: str, now: datetime) -> datetime | None:
    relative = re.search(r"(今天|昨天)\s*(\d{1,2}):(\d{2})", text)
    if relative:
        day = now.date() - timedelta(days=1 if relative.group(1) == "昨天" else 0)
        return datetime.combine(day, datetime.min.time()).replace(
            hour=int(relative.group(2)), minute=int(relative.group(3)))
    match = DATE.search(text)
    if match:
        year, month, day, hour, minute = match.groups()
        try:
            return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0))
        except ValueError:
            return None
    # A date without a year is too ambiguous for historical mail.
    match = re.search(r"(?<!\d)(\d{1,2})[-/月](\d{1,2})(?:日)?\s+(\d{1,2}):(\d{2})", text)
    if match:
        month, day, hour, minute = map(int, match.groups())
        for year in (now.year, now.year - 1):
            try:
                candidate = datetime(year, month, day, hour, minute)
                if candidate <= now + timedelta(days=1):
                    return candidate
            except ValueError:
                pass
    return None


def _not_before_click(received: datetime, clicked: datetime) -> bool:
    """Compare at the one-minute precision exposed by 163 webmail."""
    return received >= clicked.replace(second=0, microsecond=0)


def _pdf_not_before_click(data: bytes, clicked: datetime, received: datetime) -> bool:
    """Use the report's second-level creation time when the mailbox minute is ambiguous."""
    try:
        metadata = PdfReader(io.BytesIO(data)).metadata
        created = metadata.creation_date if metadata else None
    except Exception:
        created = None
    if created is not None:
        if created.tzinfo is not None:
            created = created.astimezone().replace(tzinfo=None)
        return created >= clicked
    return received > clicked.replace(second=0, microsecond=0)


def _refresh_seconds(value: object) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        seconds = 15
    return max(5, min(300, seconds))


def _current_message_rows(frame: Frame, report: SentReport):
    now = datetime.now()
    matches = []
    for row in frame.locator(ROW).all():
        try:
            if not row.is_visible():
                continue
            text = row.inner_text(timeout=1500)
            if MAIL_TITLE not in text or SENDER not in text:
                continue
            received = _message_date(row.locator(".eO0[title]").first.get_attribute("title") or "", now)
            if received and _not_before_click(received, report.sent_at) and received <= now + timedelta(minutes=5):
                matches.append(row)
        except PlaywrightError:
            continue
    return matches


def _matching_message(page: Page, report: SentReport) -> bool:
    filename = f"{report.subject}报告.pdf"
    if _attachment_download_link(page, filename) is None:
        return False
    try:
        body = page.locator("body").inner_text(timeout=3000)
        return all(value in body for value in (SENDER, MAIL_TITLE, filename, report.subject))
    except PlaywrightError:
        return False


def _attachment_download_link(page: Page, filename: str):
    for frame in page.frames:
        try:
            for link in frame.locator("a[href*='action=download_attach'][href*='mode=download']").all():
                container = link.locator("xpath=..").first
                if filename in container.inner_text(timeout=1000):
                    return link
        except PlaywrightError:
            continue
    return None


def collect_reports(browser: BrowserContext, reports: list[SentReport], stop: Event,
                    progress, user_action, *, refresh_seconds: int = 15) -> tuple[list[Path], list[str]]:
    """Visit 163 Mail after a batch; return saved files and unresolved companies."""
    if not reports:
        return [], []
    refresh_seconds = _refresh_seconds(refresh_seconds)
    existing = next((tab for tab in browser.pages if not tab.is_closed()
                     and tab.url.startswith("https://mail.163.com/js6/")), None)
    page = existing or browser.new_page()
    saved: list[Path] = []
    pending = list(reports)
    try:
        if existing is None:
            page.goto(HOME, wait_until="commit", timeout=60000)
        progress("请在右侧 Chrome 登录 163 邮箱；登录后将自动查找本批次报告…")
        user_action(True)
        deadline = time.monotonic() + 600
        frame = _search_frame(page)
        while frame is None:
            _check_stop(stop)
            if time.monotonic() >= deadline:
                raise TimeoutError("等待 163 邮箱登录超时")
            page.wait_for_timeout(500)
            frame = _search_frame(page)
        user_action(False)
        _open_inbox(page)
        deadline = time.monotonic() + 600
        last_notice = 0.0
        while pending and time.monotonic() < deadline:
            _check_stop(stop)
            frame = _search_frame(page)
            if frame is None:
                raise RuntimeError("163 邮箱页面已退出或搜索框不可用")
            for report in list(pending):
                _check_stop(stop)
                _open_inbox(page)
                page.wait_for_timeout(350)
                rows = _current_message_rows(frame, report)
                for row in rows[:30]:
                    try:
                        received = _message_date(row.locator(".eO0[title]").first.get_attribute("title") or "", datetime.now())
                        if received is None:
                            continue
                        _open_inbox(page)
                        row.click()
                        page.wait_for_timeout(350)
                        if not _matching_message(page, report):
                            continue
                        link = _attachment_download_link(page, f"{report.subject}报告.pdf")
                        if link is None:
                            continue
                        href = link.get_attribute("href")
                        if not href:
                            continue
                        response = page.request.get(href, timeout=30000)
                        data = response.body()
                        if (not response.ok or not data.startswith(b"%PDF")
                                or not _pdf_not_before_click(data, report.sent_at, received)):
                            continue
                        destination = save_pdf_bytes(data, report.pdf_path.with_name(f"{report.pdf_path.stem}_导出报告.pdf"))
                        saved.append(destination)
                        pending.remove(report)
                        progress(f"已保存企信网邮件报告：{destination}")
                        break
                    except PlaywrightError:
                        continue
            if not pending:
                break
            if time.monotonic() - last_notice >= 60:
                progress(f"等待企信网邮件到达：已保存 {len(saved)}/{len(reports)}，仍待 {len(pending)} 份；邮箱将自动刷新…")
                last_notice = time.monotonic()
            for _ in range(refresh_seconds * 2):
                _check_stop(stop)
                page.wait_for_timeout(500)
            page.reload(wait_until="domcontentloaded", timeout=30000)
            _open_inbox(page)
        return saved, [report.subject for report in pending]
    except (PlaywrightError, RuntimeError, TimeoutError) as exc:
        progress(f"163 邮箱收取中断：{exc}；已保存 {len(saved)}/{len(reports)}")
        return saved, [report.subject for report in pending]
    finally:
        user_action(False)
        try:
            if existing is None:
                page.close()
        except PlaywrightError:
            pass
