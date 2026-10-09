"""CSRC securities and futures market discredit record search."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


HOME = "https://neris.csrc.gov.cn/shixinchaxun/"
NAME_INPUT = "input[placeholder*='必须填写姓名'], input[placeholder*='姓名或名称']"
SEARCH_BUTTON = "button:has-text('搜索'), input[type='submit'], img[alt='搜索'], [role='button']:has-text('搜索')"


def _result_page(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "neris.csrc.gov.cn" and parsed.path.rstrip("/") == "/shixinchaxun/searchResult"


class NerisAdapter(SiteAdapter):
    key = "neris"
    display_name = "证监会失信查询"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        try:
            response = page.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开证监会失信查询平台：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"证监会失信查询平台返回 HTTP {response.status}")
        if urlparse(page.url).hostname != "neris.csrc.gov.cn":
            raise SiteUnavailable(f"证监会失信查询平台跳转到了其他网站：{page.url}")

        name = page.locator(NAME_INPUT).first
        context.wait_until(name.is_visible, "等待失信查询输入框加载…", timeout_seconds=60)
        name.fill(subject)
        search = page.locator(SEARCH_BUTTON).first
        if not search.is_visible():
            search = page.get_by_text("搜索", exact=True).first
        if not search.is_visible():
            raise ValueError("未找到失信查询的搜索按钮")
        context.report_status(f"正在查询证监会失信记录：{subject}")
        search.click()

        context.wait_until(
            lambda: _result_page(page.url) and page.get_by_text("查询结果", exact=True).first.is_visible(),
            "请在 Chrome 中完成本次滑块验证；如页面仍未提交，请点击搜索。完成后将自动保存结果。",
            timeout_seconds=600,
            user_action=True,
        )
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        saved = context.save_pdf(page)
        context.report_status(f"已保存证监会失信查询结果：{saved}")
