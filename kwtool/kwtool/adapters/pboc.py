"""People's Bank of China published credit rating institution roster."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings


RATING_TITLE = "评级结果可以在银行间债券市场使用的评级机构名单"
RATING_URL = "https://www.pbc.gov.cn/jinrongshichangsi/147160/147358/147406/2806822/index.html"


class PbocAdapter(SiteAdapter):
    key = "pboc"
    display_name = "中国人民银行"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = (FieldSpec("rating_usable", RATING_TITLE, "bool", False),)

    def run_one(self, context: RunContext, subject: str) -> None:
        if not context.site_settings.get("rating_usable", False):
            raise ValueError("请至少勾选一个中国人民银行名单")
        context.check_stop()
        context.report_status(f"正在打开中国人民银行名单：{RATING_TITLE}")
        page = context.page
        try:
            response = page.goto(RATING_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开中国人民银行名单：{exc}") from exc
        if urlparse(page.url).hostname != "www.pbc.gov.cn" or (response is not None and response.status >= 400):
            raise SiteUnavailable(f"中国人民银行名单页面不可用：{page.url}")
        context.wait_until(
            lambda: RATING_TITLE in page.locator("body").inner_text(timeout=5000),
            f"等待中国人民银行名单加载：{RATING_TITLE}", timeout_seconds=60,
        )
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        saved = context.save_pdf(page)
        context.report_status(f"已保存中国人民银行名单页面：{saved}")
