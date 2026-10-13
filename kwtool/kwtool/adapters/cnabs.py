"""CNABS organization search with an interactive login gate."""

from __future__ import annotations

import re
from urllib.parse import quote, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


HOME_URL = "https://www.cn-abs.com/#/home"
SEARCH_URL = "https://www.cn-abs.com/index.html#/main/search?keyword={}"
ORGANIZATION_TAB = re.compile(r"^机构\s*\(\s*\d+\s*\)$")


def _is_cnabs_page(page) -> bool:
    return urlparse(page.url).hostname == "www.cn-abs.com"


def _read_during_navigation(read, fallback):
    try:
        return read()
    except PlaywrightError as exc:
        if "Execution context was destroyed" in str(exc):
            return fallback
        raise


def _is_logged_in(page) -> bool:
    if not _is_cnabs_page(page):
        return False
    return bool(_read_during_navigation(lambda: page.evaluate("""() => {
      const text = document.body?.innerText || '';
      return /上次登录|退出登录|账号升级/.test(text);
    }"""), False))


def _search_ready(page, subject: str) -> bool:
    if not _is_cnabs_page(page) or "/main/search" not in page.url:
        return False
    return bool(_read_during_navigation(lambda: page.evaluate("""(subject) => {
      const inputs = [...document.querySelectorAll('input')];
      const queryVisible = inputs.some(input =>
        (input.offsetWidth || input.offsetHeight || input.getClientRects().length) &&
        input.value.trim() === subject);
      const text = document.body?.innerText || '';
      return queryVisible && /机构\\s*\\(\\s*\\d+\\s*\\)/.test(text);
    }""", subject), False))


def _institution_count(page) -> int | None:
    labels = _read_during_navigation(
        lambda: page.locator("body").inner_text(timeout=5000), "")
    match = re.search(r"机构\s*\(\s*(\d+)\s*\)", labels)
    return int(match.group(1)) if match else None


def _wait_and_click_institution_tab(context: RunContext, page) -> int:
    context.wait_until(
        lambda: _institution_count(page) is not None,
        "等待资产证券化分析网搜索分类加载…",
        timeout_seconds=60,
    )
    count = _institution_count(page)
    assert count is not None
    tab = page.get_by_text(ORGANIZATION_TAB).first
    tab.click()
    return count


def _is_detail_page(page) -> bool:
    return (not page.is_closed() and _is_cnabs_page(page)
            and "/organization.html" in page.url and "/detail/info" in page.url)


def _find_detail_page(context: RunContext, search_page, prior_pages):
    if _is_detail_page(search_page):
        return search_page
    for candidate in context.browser.pages:
        if candidate not in prior_pages and _is_detail_page(candidate):
            return candidate
    return None


class CnabsAdapter(SiteAdapter):
    key = "cnabs"
    display_name = "资产证券化分析网"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status("请先登录资产证券化分析网；登录完成后将自动检索…")
        try:
            response = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开资产证券化分析网：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"资产证券化分析网返回 HTTP {response.status}")

        login_url = None
        login_samples = 0

        def login_ready() -> bool:
            nonlocal login_url, login_samples
            if not _is_logged_in(page):
                login_url, login_samples = None, 0
                return False
            current_url = page.url
            login_samples = login_samples + 1 if current_url == login_url else 1
            login_url = current_url
            return login_samples >= 3

        context.wait_until(
            login_ready,
            "资产证券化分析网需要登录：请在右侧 Chrome 完成登录…",
            timeout_seconds=600,
            user_action=True,
        )

        search_url = SEARCH_URL.format(quote(subject, safe=""))
        try:
            response = page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法搜索资产证券化分析网：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"资产证券化分析网搜索返回 HTTP {response.status}")

        try:
            context.wait_until(
                lambda: _search_ready(page, subject),
                f"等待资产证券化分析网搜索结果：{subject}",
                timeout_seconds=90,
            )
        except TimeoutError as exc:
            context.capture_diagnostic("cnabs-search-timeout", page)
            raise RuntimeError("资产证券化分析网未能确认搜索内容和机构分类，未保存 PDF") from exc

        institution_count = _wait_and_click_institution_tab(context, page)
        if institution_count:
            context.wait_until(
                lambda: page.locator("table tbody tr, .el-table__body tr").count() > 0,
                "等待资产证券化分析网机构列表…",
                timeout_seconds=60,
            )
            row = page.locator("table tbody tr, .el-table__body tr").first
            name_cell = row.locator("td").first
            name = name_cell.inner_text().strip()
            if not name:
                raise RuntimeError("资产证券化分析网机构列表缺少机构名称，未保存 PDF")
            prior_pages = set(context.browser.pages)
            name_cell.get_by_text(name, exact=True).first.click()

            detail = None

            def detail_opened() -> bool:
                nonlocal detail
                detail = _find_detail_page(context, page, prior_pages)
                return detail is not None

            try:
                context.wait_until(
                    detail_opened,
                    "等待资产证券化分析网机构详情页…",
                    timeout_seconds=60,
                )
                assert detail is not None
                context.wait_until(
                    lambda: _organization_detail_ready(detail),
                    "等待机构详情内容加载…",
                    timeout_seconds=60,
                )
                self._save_result(context, detail)
            finally:
                if detail is not None and detail is not page and not detail.is_closed():
                    detail.close()
            return
        else:
            # Keep the zero-count institution category visible in the export.
            context.wait_until(
                lambda: bool(page.locator("body").inner_text(timeout=5000)),
                "等待资产证券化分析网机构空结果页…",
                timeout_seconds=30,
            )

        self._save_result(context, page)

    def _save_result(self, context: RunContext, page) -> None:
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("cnabs-ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存资产证券化分析网机构检索结果：{saved}")


def _organization_detail_ready(page) -> bool:
    body = _read_during_navigation(
        lambda: page.locator("body").inner_text(timeout=5000), "")
    return len(body.strip()) > 100 and "机构简介" in body and "基本信息" in body
