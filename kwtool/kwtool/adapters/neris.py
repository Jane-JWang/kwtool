"""CSRC securities and futures market discredit record search."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError, Page

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.diagnostics import browser_pages, browser_snapshot
from kwtool.models import PdfSettings


HOME = "https://neris.csrc.gov.cn/shixinchaxun/"
NAME_INPUT = "input[placeholder*='必须填写姓名'], input[placeholder*='姓名或名称']"
SEARCH_BUTTON = "button:has-text('搜索'), input[type='submit'], img[alt='搜索'], [role='button']:has-text('搜索')"
LOGGER = logging.getLogger("kwtool")
RESULT_STATE = """() => {
  const queue = [document.querySelector('#app')?.__vue__];
  let view = null;
  for (let i = 0; i < queue.length && i < 100; i++) {
    const item = queue[i];
    if (!item) continue;
    if (item.data1 && Array.isArray(item.data1.rows) && typeof item.loading === 'boolean') {
      view = item;
      break;
    }
    queue.push(...(item.$children || []));
  }
  const noData = Array.from(document.querySelectorAll('.noData')).some(el =>
    el.getClientRects().length && el.textContent.includes('无符合条件记录'));
  const renderedRows = document.querySelectorAll('.resultBox .ivu-table-row').length;
  const rows = view ? view.data1.rows.length : renderedRows;
  const loading = view ? view.loading : null;
  return {vueFound: !!view, loading, rows, total: view ? view.data1.total : null,
    noData, renderedRows};
}"""


def _result_page(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.hostname == "neris.csrc.gov.cn" and parsed.path.rstrip("/") == "/shixinchaxun/searchResult"


def _find_result(context: RunContext, subject: str) -> Page | None:
    for page in reversed(browser_pages(context.browser)):
        if page.is_closed() or not _result_page(page.url):
            continue
        if subject in parse_qs(urlparse(page.url).query).get("obj_name", []):
            return page
    return None


def _page_snapshot(context: RunContext, subject: str) -> str:
    return browser_snapshot(context.browser, query_match=("obj_name", subject))


def _result_state(page: Page) -> dict:
    return page.evaluate(RESULT_STATE)


def _data_ready(state: dict) -> bool:
    if state["vueFound"]:
        return state["loading"] is False and (state["rows"] > 0 or state["noData"])
    return state["renderedRows"] > 0 or state["noData"]


class NerisAdapter(SiteAdapter):
    key = "neris"
    display_name = "证监会失信查询"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        if context.diagnostics_dir:
            LOGGER.info("NERIS 查询开始：%s；标签页：%s", subject, _page_snapshot(context, subject))
        # A previous attempt can leave a result tab open. Remove it before
        # submitting this subject so it cannot be mistaken for the new result.
        for other in browser_pages(context.browser):
            if other is not page and not other.is_closed() and _result_page(other.url):
                other.close()
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

        last_snapshot = None

        def result_ready() -> bool:
            nonlocal last_snapshot
            if context.diagnostics_dir:
                snapshot = _page_snapshot(context, subject)
                if snapshot != last_snapshot:
                    LOGGER.info("NERIS 等待结果：%s；标签页：%s", subject, snapshot)
                    last_snapshot = snapshot
            return _find_result(context, subject) is not None

        context.wait_until(
            result_ready,
            "请在 Chrome 中完成本次滑块验证；如页面仍未提交，请点击搜索。完成后将自动保存结果。",
            timeout_seconds=600,
            user_action=True,
        )
        result = _find_result(context, subject)
        assert result is not None
        if context.diagnostics_dir:
            LOGGER.info("NERIS 已识别结果页：%s；标签页：%s", subject, _page_snapshot(context, subject))
        context.capture_diagnostic("result-detected", result)
        try:
            result.wait_for_load_state("domcontentloaded", timeout=30000)
            last_state = None

            def data_ready() -> bool:
                nonlocal last_state
                state = _result_state(result)
                if context.diagnostics_dir and state != last_state:
                    LOGGER.info("NERIS 结果数据状态：%s；%s", subject, state)
                    last_state = state
                return _data_ready(state)

            try:
                context.wait_until(data_ready, "等待失信查询数据加载完成…", timeout_seconds=60)
            except TimeoutError:
                context.capture_diagnostic("result-timeout", result)
                raise
            result.wait_for_timeout(100)
            result.evaluate("document.fonts.ready")
            result.emulate_media(media="screen")
            result.evaluate("window.scrollTo(0, 0)")
            context.capture_diagnostic("ready-to-print", result)
            saved = context.save_pdf(result)
            context.report_status(f"已保存证监会失信查询结果：{saved}")
        finally:
            if result is not page:
                try:
                    result.close()
                    page.bring_to_front()
                except PlaywrightError:
                    pass  # Chrome may have been closed by the user.
