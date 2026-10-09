"""Shanghai Stock Exchange website search result printing."""

from __future__ import annotations

import io
import re
import time
from urllib.parse import quote, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError, Page, Request
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_pdf_bytes


SEARCH_URL = "https://www.sse.com.cn/home/search/index.shtml"
SECTIONS = (("全部", "0"), ("公告", "1"), ("规则", "4"), ("监管", "5"), ("新闻", "3"))
MODES = ("精确搜索", "模糊搜索")
DEFAULT_KEYWORDS = "处罚 行政监管 责令改正 警示函"


def search_url(subject: str) -> str:
    # SSE treats '+' literally in webswd; encode spaces as %20.
    return f"{SEARCH_URL}?{urlencode({'webswd': subject}, quote_via=quote)}"


def parse_keywords(value: str) -> tuple[str, ...]:
    """Whitespace separates terms; repeated terms do not trigger duplicate exports."""
    return tuple(dict.fromkeys(value.split()))


def _mode_control(page: Page, mode: str):
    return page.locator(".tool_title:visible").filter(has_text=re.compile(rf"^\s*{re.escape(mode)}")).first


def _result_state(page: Page) -> str | None:
    """Return the visible result area's text once a result or empty state exists."""
    return page.evaluate("""() => {
      const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
      const rows = [...document.querySelectorAll('.list-group-item')].filter(
        e => visible(e) && e.querySelector('.bot_title_a'));
      if (rows.length) return rows.map(e => e.innerText).join('\\n');
      const area = [...document.querySelectorAll('.search_toFeedback, .search_dataNum')]
        .find(visible);
      return area ? area.closest('.tool_left')?.innerText || area.innerText : null;
    }""")


def _active_page_number(page: Page) -> int | None:
    active = page.locator(".pagination-box:visible li.active a").first
    if not active.count():
        return None
    value = active.inner_text().strip()
    return int(value) if value.isdigit() else None


def _wait_for_results(context: RunContext, page: Page, section: str,
                      pending: set[Request], expected_page: int | None = None) -> None:
    deadline = time.monotonic() + 90
    last_state: str | None = None
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        context.check_stop()
        state = _result_state(page)
        if state != last_state:
            last_state = state
            stable_since = time.monotonic()
        page_ready = expected_page is None or _active_page_number(page) == expected_page
        if state is not None and page_ready and not pending and time.monotonic() - stable_since >= 1.5:
            return
        page.wait_for_timeout(250)
    raise TimeoutError(f"等待上交所“{section}”搜索结果超时")


class SseAdapter(SiteAdapter):
    key = "sse"
    display_name = "上海证券交易所"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = (
        FieldSpec("search_mode", "搜索方式", "choice", "模糊搜索", MODES),
        FieldSpec("keywords", "关键词（空格分隔）", "text", DEFAULT_KEYWORDS),
        FieldSpec("max_pages", "前 K 页", "integer", 1),
        *(FieldSpec(f"section_{index}", name, "bool", index == 0)
          for index, (name, _tab) in enumerate(SECTIONS)),
    )

    def run_one(self, context: RunContext, subject: str) -> None:
        selected = [(name, tab) for index, (name, tab) in enumerate(SECTIONS)
                    if context.site_settings.get(f"section_{index}", index == 0)]
        if not selected:
            raise ValueError("请至少勾选一个上交所搜索栏目")
        mode = context.site_settings.get("search_mode", "模糊搜索")
        if mode not in MODES:
            raise ValueError("上交所搜索方式必须是精确搜索或模糊搜索")
        keywords = parse_keywords(str(context.site_settings.get("keywords", DEFAULT_KEYWORDS)))
        try:
            max_pages = int(context.site_settings.get("max_pages", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("上交所前 K 页必须是整数") from exc
        if not 1 <= max_pages <= 500:
            raise ValueError("上交所前 K 页必须在 1 到 500 之间")

        page = context.page
        pieces: list[bytes] = []
        for keyword in keywords or ("",):
            context.check_stop()
            query = f"{subject} {keyword}" if keyword else subject
            context.report_status(f"正在打开上交所搜索：{query}")
            pending: set[Request] = set()

            def started(request: Request) -> None:
                if (request.resource_type in {"xhr", "fetch"}
                        or "getESSearchDoc.do" in request.url):
                    pending.add(request)

            def ended(request: Request) -> None:
                pending.discard(request)

            page.on("request", started)
            page.on("requestfinished", ended)
            page.on("requestfailed", ended)
            try:
                try:
                    response = page.goto(search_url(query), wait_until="domcontentloaded", timeout=60000)
                except PlaywrightError as exc:
                    raise SiteUnavailable(f"无法打开上交所搜索页：{exc}") from exc
                parsed = urlparse(page.url)
                if parsed.hostname != "www.sse.com.cn" or parsed.path != "/home/search/index.shtml":
                    raise SiteUnavailable(f"上交所搜索页跳转到了其他页面：{page.url}")
                if response is not None and response.status >= 400:
                    raise SiteUnavailable(f"上交所搜索页返回 HTTP {response.status}")
                page.wait_for_load_state("load", timeout=60000)
                context.wait_until(lambda: page.locator("#tab-switch0 span[index='0']").count() > 0
                                   and page.locator("#searchword").input_value() == query
                                   and _result_state(page) is not None,
                                   "等待上交所搜索页面加载…", timeout_seconds=60)
                page.wait_for_timeout(1000)

                for name, tab in selected:
                    context.check_stop()
                    context.report_status(f"正在加载“{query}”的上交所“{name}”栏目（{mode}）…")
                    tile = page.locator(f'#tab-switch0 span[index="{tab}"]')
                    if tile.count() != 1 or not tile.is_visible():
                        raise ValueError(f"上交所搜索页未找到“{name}”栏目")
                    if "active" not in (tile.get_attribute("class") or "").split():
                        tile.click()
                    context.wait_until(lambda: "active" in (tile.get_attribute("class") or "").split(),
                                       f"等待上交所切换到“{name}”栏目…", timeout_seconds=30)
                    page.wait_for_timeout(750)
                    for _attempt in range(3):
                        context.check_stop()
                        if "active" not in (tile.get_attribute("class") or "").split():
                            tile.click()
                            page.wait_for_timeout(750)
                        mode_control = _mode_control(page, str(mode))
                        context.wait_until(lambda: mode_control.count() > 0,
                                           f"等待上交所“{name}”栏目搜索方式控件…", timeout_seconds=30)
                        if "titleSelected" not in (mode_control.get_attribute("class") or "").split():
                            mode_control.click()
                        page.wait_for_timeout(750)
                        if ("active" in (tile.get_attribute("class") or "").split()
                                and "titleSelected" in (mode_control.get_attribute("class") or "").split()):
                            break
                    else:
                        raise RuntimeError(f"上交所“{name}”栏目未能切换到“{mode}”")

                    seen: set[str] = set()
                    for page_number in range(1, max_pages + 1):
                        _wait_for_results(context, page, name, pending,
                                          expected_page=page_number if page_number > 1 else None)
                        state = _result_state(page)
                        assert state is not None
                        if state in seen:
                            raise RuntimeError(f"“{query}”的“{name}”栏目翻页后内容未变化")
                        seen.add(state)
                        page.evaluate("document.fonts.ready")
                        page.emulate_media(media="screen")
                        page.evaluate("window.scrollTo(0, 0)")
                        context.capture_diagnostic(f"{name}-{page_number}-ready-to-print", page)
                        context.report_status(f"正在打印“{query}”的“{name}”栏目第 {page_number} 页…")
                        pieces.append(context.render_pdf(page))
                        if page_number == max_pages:
                            break
                        next_link = page.locator(".pagination-box:visible li.next a").first
                        if (not next_link.count() or not next_link.is_visible()
                                or "getAllWordsResultNew" not in (next_link.get_attribute("href") or "")):
                            break
                        next_link.click()
            finally:
                page.remove_listener("request", started)
                page.remove_listener("requestfinished", ended)
                page.remove_listener("requestfailed", ended)

        context.check_stop()
        if len(pieces) == 1:
            result = pieces[0]
        else:
            writer = PdfWriter()
            for piece in pieces:
                writer.append(PdfReader(io.BytesIO(piece)))
            output = io.BytesIO()
            writer.write(output)
            result = output.getvalue()
        saved = save_pdf_bytes(result, context.pdf_path)
        context.artifacts.append(saved)
        context.report_status(f"已保存上交所搜索结果：{saved}")
