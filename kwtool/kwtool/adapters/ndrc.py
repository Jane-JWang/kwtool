"""Adapter for NdrcAdapter."""
from __future__ import annotations
from urllib.parse import parse_qs, urlencode, urlparse
from playwright.sync_api import Error as PlaywrightError
from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed
from kwtool.models import PdfSettings


class NdrcAdapter(SiteAdapter):
    key = "ndrc"
    display_name = "国家发展改革委"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        url = "https://so.ndrc.gov.cn/s?" + urlencode((("qt", subject), ("siteCode", "bm04000007")))
        context.report_status(f"正在检索国家发展改革委：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开国家发展改革委搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"国家发展改革委搜索返回 HTTP {response.status}")
        if not _allowed(context.page.url, ("ndrc.gov.cn",)):
            raise SiteUnavailable(f"国家发展改革委搜索跳转至非预期网站：{context.page.url}")
        def loaded() -> bool:
            body = context.page.locator("body").inner_text(timeout=5000)
            return subject in parse_qs(urlparse(context.page.url).query).get("qt", []) and any(
                word in body for word in ("相关结果", "暂无结果", "未找到相关", "未能搜到您想要的内容")
            )
        context.wait_until(loaded, "等待国家发展改革委检索结果", timeout_seconds=60)
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存国家发展改革委检索结果：{saved}")

