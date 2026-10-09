"""SAMR administrative penalty document search results."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


LIST_URL = "https://cfws.samr.gov.cn/list.html"
RESULT_WATCH = """() => {
  window.__kwtoolCfwsResultObserver?.disconnect();
  window.__kwtoolCfwsResultUpdated = false;
  const count = document.querySelector('#count');
  if (!count) return false;
  window.__kwtoolCfwsResultObserver = new MutationObserver(() => {
    window.__kwtoolCfwsResultUpdated = true;
  });
  window.__kwtoolCfwsResultObserver.observe(count, {childList: true, subtree: true, characterData: true});
  return true;
}"""


def _search_response(response, subject: str) -> bool:
    """Only accept the query made for this subject, not the preceding clear request."""
    if urlparse(response.url).path != "/queryDoc" or response.request.method != "POST":
        return False
    data = parse_qs(response.request.post_data or "")
    try:
        conditions = json.loads(data["queryCondition"][0])
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return (len(conditions) == 1 and conditions[0].get("key") == "30_s,36_s"
            and conditions[0].get("id") == subject)


class CfwsAdapter(SiteAdapter):
    key = "cfws"
    display_name = "市场监管行政处罚文书网"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        parsed = urlparse(page.url)
        if parsed.hostname != "cfws.samr.gov.cn" or parsed.path != "/list.html":
            try:
                response = page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightError as exc:
                raise SiteUnavailable(f"无法打开市场监管行政处罚文书网：{exc}") from exc
            if response is not None and response.status >= 400:
                raise SiteUnavailable(f"市场监管行政处罚文书网返回 HTTP {response.status}")
        if urlparse(page.url).hostname != "cfws.samr.gov.cn":
            raise SiteUnavailable(f"市场监管行政处罚文书网跳转到了其他网站：{page.url}")

        clear = page.locator("#clear-Btn")
        context.wait_until(
            lambda: clear.is_visible() and page.locator("#count").inner_text().startswith("共检索到"),
            "等待市场监管行政处罚文书网检索页加载…", timeout_seconds=60,
        )
        # The site stores selected filters in localStorage. Filling a new name
        # without clearing them intersects the two searches.
        context.report_status(f"正在清空上次检索条件：{subject}")
        if not page.evaluate(RESULT_WATCH):
            raise SiteUnavailable("市场监管行政处罚文书网未加载结果计数区域")
        with page.expect_response(lambda r: urlparse(r.url).path == "/queryDoc", timeout=30000):
            clear.click()
        context.wait_until(
            lambda: page.evaluate("window.__kwtoolCfwsResultUpdated === true")
            and page.locator(".LT_Filter_right p").count() == 0
            and page.evaluate("localStorage.getItem('listPageSearchItem')") in ("", "[]"),
            "等待旧检索条件清空…", timeout_seconds=30,
        )

        keyword = page.locator("#keyword")
        keyword.fill(f"当事人名称：{subject}")
        if not page.evaluate(RESULT_WATCH):
            raise SiteUnavailable("市场监管行政处罚文书网未加载结果计数区域")
        context.report_status(f"正在检索市场监管行政处罚文书：{subject}（第 1 页）")
        with page.expect_response(lambda r: _search_response(r, subject), timeout=30000) as pending:
            page.locator(".quickly-entry a").click()
        if pending.value.status >= 400:
            raise SiteUnavailable(f"市场监管行政处罚文书网检索返回 HTTP {pending.value.status}")
        context.wait_until(
            lambda: page.evaluate("window.__kwtoolCfwsResultUpdated === true")
            and page.locator("#count").inner_text().startswith("共检索到"),
            "等待市场监管行政处罚文书检索结果加载…", timeout_seconds=60,
        )
        page.evaluate("window.__kwtoolCfwsResultObserver?.disconnect()")
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存市场监管行政处罚文书检索结果：{saved}")
