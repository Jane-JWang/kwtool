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

        def loaded() -> bool:
            body = context.page.locator("body").inner_text(timeout=5000)
            query = parse_qs(urlparse(context.page.url).query).get("q", [])
            return subject in query and "查询范围" in body and "排序方式" in body

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

