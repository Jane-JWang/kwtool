"""Search and print the AMAC, Shenzhen and Beijing exchange result pages."""

from __future__ import annotations

import io
from urllib.parse import urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError, Page
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_pdf_bytes


AMAC_SEARCH = "https://www.amac.org.cn/index/qzss/"
SZSE_SEARCH = "https://www.szse.cn/application/search/index.html"
BSE_SEARCH = "https://www.bse.cn/select/index/searchInfo.do"


def _open(context: RunContext, url: str, hostname: str, label: str) -> None:
    try:
        response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except PlaywrightError as exc:
        raise SiteUnavailable(f"无法打开{label}：{exc}") from exc
    if urlparse(context.page.url).hostname != hostname or (response and response.status >= 400):
        raise SiteUnavailable(f"{label}页面不可用：{context.page.url}")


def _max_pages(context: RunContext) -> int:
    try:
        value = int(context.site_settings.get("max_pages", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("前 K 页必须是整数") from exc
    if not 1 <= value <= 500:
        raise ValueError("前 K 页必须在 1 到 500 之间")
    return value


def _print(context: RunContext, page: Page, label: str) -> bytes:
    page.evaluate("document.fonts.ready")
    page.emulate_media(media="screen")
    page.evaluate("window.scrollTo(0, 0)")
    context.capture_diagnostic(f"{label}-ready-to-print", page)
    return context.render_pdf(page)


def _save_pieces(context: RunContext, pieces: list[bytes], label: str) -> None:
    if len(pieces) == 1:
        result = pieces[0]
    else:
        writer = PdfWriter()
        for piece in pieces:
            writer.append(PdfReader(io.BytesIO(piece)))
        buffer = io.BytesIO()
        writer.write(buffer)
        result = buffer.getvalue()
    saved = save_pdf_bytes(result, context.pdf_path)
    context.artifacts.append(saved)
    context.report_status(f"已保存{label}搜索结果：{saved}")


class AmacSearchAdapter(SiteAdapter):
    key = "amac_search"
    display_name = "中国证券投资基金业协会【检索】"
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 1),)

    def run_one(self, context: RunContext, subject: str) -> None:
        maximum = _max_pages(context)
        _open(context, f"{AMAC_SEARCH}?{urlencode({'key': subject})}", "www.amac.org.cn", "基金业协会检索")
        page = context.page
        context.wait_until(lambda: page.locator(".menuTit1 a").inner_text().find("()") < 0
                           and (page.locator(".textList li").count() > 0
                                or page.locator(".listBox .empty:visible").count() > 0),
                           "等待基金业协会检索结果", timeout_seconds=60)
        pieces: list[bytes] = []
        for number in range(1, maximum + 1):
            context.check_stop()
            context.report_status(f"正在打印基金业协会检索第 {number} 页")
            pieces.append(_print(context, page, f"amac-search-{number}"))
            next_button = page.locator(".js-page-next:not(.ui-pager-disabled) a").first
            if number == maximum or not next_button.count():
                break
            before = page.locator(".textList").first.inner_text()
            next_button.click()
            context.wait_until(lambda: page.locator(".textList").first.inner_text() != before,
                               "等待基金业协会检索翻页", timeout_seconds=60)
        _save_pieces(context, pieces, "基金业协会")


class SzseAdapter(SiteAdapter):
    key = "szse"
    display_name = "深圳证券交易所"
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 1),)

    def run_one(self, context: RunContext, subject: str) -> None:
        maximum = _max_pages(context)
        _open(context, f"{SZSE_SEARCH}?{urlencode({'keyword': subject})}", "www.szse.cn", "深交所检索")
        page = context.page
        context.wait_until(lambda: page.locator(".search-result .result-title").count() > 0
                           and (page.locator(".article-item").count() > 0
                                or "0" in page.locator(".article-search-result").inner_text()),
                           "等待深交所检索结果", timeout_seconds=60)
        pieces: list[bytes] = []
        for number in range(1, maximum + 1):
            context.check_stop()
            context.report_status(f"正在打印深交所检索第 {number} 页")
            pieces.append(_print(context, page, f"szse-{number}"))
            next_button = page.locator(".article-search-result a[data-pi]").filter(has_text="下一页").first
            if number == maximum or not next_button.count():
                break
            before = page.locator(".article-search-result").inner_text()
            next_button.click()
            context.wait_until(lambda: page.locator(".article-search-result").inner_text() != before,
                               "等待深交所检索翻页", timeout_seconds=60)
        _save_pieces(context, pieces, "深交所")


class BseAdapter(SiteAdapter):
    key = "bse"
    display_name = "北京证券交易所"
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 1),)

    def run_one(self, context: RunContext, subject: str) -> None:
        maximum = _max_pages(context)
        _open(context, BSE_SEARCH, "www.bse.cn", "北交所搜索")
        page = context.page
        page.locator("#keyword").fill(subject)
        page.locator("#searchBtn").click()
        context.wait_until(lambda: subject in page.locator(".data-search .sub-title").inner_text()
                           and (page.locator("#quotationTable .main").count() > 0
                                or "0" in page.locator(".data-result").inner_text()),
                           "等待北交所搜索结果", timeout_seconds=60)
        pieces: list[bytes] = []
        for number in range(1, maximum + 1):
            context.check_stop()
            context.report_status(f"正在打印北交所搜索第 {number} 页")
            pieces.append(_print(context, page, f"bse-{number}"))
            next_button = page.locator("#Pagination a.next").first
            if number == maximum or not next_button.count():
                break
            before = page.locator("#quotationTable").inner_text()
            next_button.click()
            context.wait_until(lambda: page.locator("#quotationTable").inner_text() != before,
                               "等待北交所搜索翻页", timeout_seconds=60)
        _save_pieces(context, pieces, "北交所")
