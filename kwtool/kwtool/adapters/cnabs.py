"""CNABS product search, with interactive login and search fallback."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


PRODUCT_URL = "https://www.cn-abs.com/product.html#/list/product"
SEARCH_INPUTS = (
    'input[placeholder*="产品名称"]:visible',
    'input[placeholder*="搜索"]:visible',
    'input[placeholder*="请输入"]:visible',
    'input[placeholder*="名称"]:visible',
)


def _is_product_page(page) -> bool:
    parsed = urlparse(page.url)
    return (parsed.hostname == "www.cn-abs.com" and parsed.path == "/product.html"
            and "/list/product" in parsed.fragment)


def _search_state(page, subject: str) -> str:
    if not _is_product_page(page):
        return "other-page"
    data = page.evaluate("""(subject) => {
      const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const body = document.body;
      if (!body || !visible(body)) return {matched: false, empty: false, query: false};
      const inputs = [...document.querySelectorAll('input')].filter(visible);
      const query = inputs.some(el => el.value?.trim() === subject);
      // Input values are excluded: typing a name alone must never count as a result.
      const text = body.innerText || '';
      const matches = [...body.querySelectorAll('tr, li, .el-table__row, [class*="product"], [class*="result"]')]
        .filter(visible).some(el => (el.innerText || '').includes(subject));
      return {matched: matches, empty: /暂无数据|暂无搜索结果|没有搜索结果|未找到相关|无匹配结果/.test(text), query};
    }""", subject)
    if data["matched"]:
        return "results"
    if data["empty"] and data["query"]:
        return "empty"
    return "loading"


def _try_search(page, subject: str) -> bool:
    for selector in SEARCH_INPUTS:
        field = page.locator(selector)
        if field.count() == 0:
            continue
        field.first.fill(subject)
        field.first.press("Enter")
        return True
    return False


class CnabsAdapter(SiteAdapter):
    key = "cnabs"
    display_name = "资产证券化分析网"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status(f"正在打开资产证券化分析网产品列表：{subject}")
        try:
            response = page.goto(PRODUCT_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开资产证券化分析网：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"资产证券化分析网返回 HTTP {response.status}")

        # The current site redirects unauthenticated users to account.cn-abs.com.
        if not _is_product_page(page):
            context.wait_until(
                lambda: _is_product_page(page),
                "资产证券化分析网需要登录：请在右侧 Chrome 登录并打开产品列表…",
                timeout_seconds=600, user_action=True,
            )

        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_timeout(1000)
            searched = _try_search(page, subject)
        except PlaywrightError:
            searched = False
        if searched:
            try:
                context.wait_until(
                    lambda: _search_state(page, subject) in {"results", "empty"},
                    f"等待资产证券化分析网搜索结果：{subject}",
                    timeout_seconds=20,
                )
            except TimeoutError:
                pass
        if _search_state(page, subject) not in {"results", "empty"}:
            context.wait_until(
                lambda: _search_state(page, subject) in {"results", "empty"},
                f"请在右侧 Chrome 的资产证券化分析网产品列表搜索“{subject}”，等待结果显示…",
                timeout_seconds=600, user_action=True,
            )

        if not _is_product_page(page):
            raise RuntimeError("资产证券化分析网离开了产品列表，无法保存有效搜索结果")
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("cnabs-ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存资产证券化分析网产品搜索结果：{saved}")
