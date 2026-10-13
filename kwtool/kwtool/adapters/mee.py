"""Adapter for MeeAdapter."""
from __future__ import annotations
import re
from urllib.parse import urlencode
from playwright.sync_api import Error as PlaywrightError
from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed
from kwtool.models import PdfSettings


class MeeAdapter(SiteAdapter):
    key = "mee"
    display_name = "生态环境部"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        url = "https://www.mee.gov.cn/searchnew/?" + urlencode({"searchword": subject})
        context.report_status(f"正在检索生态环境部：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开生态环境部搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"生态环境部搜索返回 HTTP {response.status}")
        if not _allowed(context.page.url, ("mee.gov.cn",)):
            raise SiteUnavailable(f"生态环境部搜索跳转至非预期网站：{context.page.url}")

        def loaded() -> bool:
            body = context.page.locator("body").inner_text(timeout=5000)
            return "搜索结果" in body and re.search(r"符合条件的有\s*[\d,，]+\s*条", body) is not None

        try:
            context.wait_until(loaded, "等待生态环境部检索结果", timeout_seconds=60)
        except TimeoutError as exc:
            context.capture_diagnostic("mee-search-timeout", context.page)
            raise RuntimeError("生态环境部未能确认检索结果，未保存 PDF") from exc
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", context.page)
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存生态环境部检索结果：{saved}")

