"""Ministry of Finance full-text search results."""

from __future__ import annotations

from urllib.parse import urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


SEARCH_URL = "https://search.mof.gov.cn/was5/web/search"
NO_RESULTS = "没有找到和您的查询相匹配的结果"


def search_url(subject: str) -> str:
    # These values match the site's "全部" search scope and "按时间排序"
    # control. The site's result pages are numbered from 1.
    params = [
        ("channelid", "295890"),
        ("andsen", subject),
        ("searchscope", ""),
        ("orderby", "-DOCRELTIME"),
        ("page", "1"),
        ("perpage", "10"),
    ]
    return f"{SEARCH_URL}?{urlencode(params)}"


class MofAdapter(SiteAdapter):
    key = "mof"
    display_name = "财政部"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status(f"正在全文检索财政部：{subject}（按时间倒序，第 1 页）")
        try:
            response = page.goto(search_url(subject), wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开财政部全文检索页：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"财政部全文检索页返回 HTTP {response.status}")
        parsed = urlparse(page.url)
        if parsed.hostname != "search.mof.gov.cn" or parsed.path.rstrip("/") != "/was5/web/search":
            raise SiteUnavailable(f"财政部全文检索页跳转到了其他页面：{page.url}")
        if page.locator("form[name='searchform']").count() == 0:
            raise SiteUnavailable("财政部全文检索页未返回搜索表单")

        def results_ready() -> bool:
            selected_order = page.locator(".paixubox a.on")
            if selected_order.count() == 0 or selected_order.first.inner_text() != "按时间排序":
                return False
            return page.locator(".list_search dl").count() > 0 or NO_RESULTS in page.locator("body").inner_text()

        context.wait_until(results_ready, "等待财政部全文检索结果加载完成…", timeout_seconds=60)
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存财政部全文检索结果第 1 页：{saved}")
