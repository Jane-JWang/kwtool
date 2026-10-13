"""Adapter for SamrAdapter."""
from __future__ import annotations
from urllib.parse import parse_qs, urlencode, urlparse
from playwright.sync_api import Error as PlaywrightError
from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed
from kwtool.models import PdfSettings


class SamrAdapter(SiteAdapter):
    key = "samr"
    display_name = "国家市场监督管理总局"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        url = "https://www.samr.gov.cn/api-gateway/jpaas-jsearch-web-server/search?" + urlencode((
            ("cateid", "165ddc6c5a0f42d4aeda15dd916f2e6d"),
            ("q", subject),
            ("serviceId", "9c455a05a4ca4052bd5490ddcead7b42"),
        ))
        context.report_status(f"正在检索国家市场监督管理总局：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开国家市场监督管理总局搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"国家市场监督管理总局搜索返回 HTTP {response.status}")
        if not _allowed(context.page.url, ("samr.gov.cn",)):
            raise SiteUnavailable(f"国家市场监督管理总局搜索跳转至非预期网站：{context.page.url}")

        last_results: tuple[str, ...] | None = None
        stable_polls = 0

        def loaded() -> bool:
            nonlocal last_results, stable_polls
            query = parse_qs(urlparse(context.page.url).query).get("q", [])
            if subject not in query:
                last_results = None
                stable_polls = 0
                return False

            body = context.page.locator("body").inner_text(timeout=5000)
            cards = context.page.locator(".comprehensiveItem.clearfix.ptlist_style")
            card_count = cards.count()
            if card_count:
                # Search controls render before the API results. Wait until
                # result cards stop changing to avoid printing a partial page.
                current_results = tuple(card.inner_text() for card in cards.all())
            elif "\u62b1\u6b49\uff0c\u5728\u5f53\u524d\u5206\u7c7b\u4e0b\u5747\u672a\u627e\u5230" in body:
                current_results = ("__no_results__",)
            else:
                last_results = None
                stable_polls = 0
                return False

            if current_results == last_results:
                stable_polls += 1
            else:
                last_results = current_results
                stable_polls = 1
            return stable_polls >= 4

        try:
            context.wait_until(loaded, "等待国家市场监督管理总局检索结果", timeout_seconds=60)
        except TimeoutError as exc:
            context.capture_diagnostic("samr-search-timeout", context.page)
            raise RuntimeError("国家市场监督管理总局未能确认检索结果，未保存 PDF") from exc
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", context.page)
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存国家市场监督管理总局检索结果：{saved}")

