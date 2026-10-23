"""Baidu search results, printed one result page at a time."""

from __future__ import annotations

import io
import logging
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from playwright.sync_api import Error as PlaywrightError, Page, TimeoutError as PlaywrightTimeoutError
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_pdf_bytes


DEFAULT_KEYWORDS = "立案 调查 监管 处罚"
PAGE_SELECTION_SETTLE_SECONDS = 0.75
RESULT_SETTLE_SECONDS = 0.75
LOGGER = logging.getLogger("kwtool")


def search_url(query: str, page_number: int) -> str:
    return "https://www.baidu.com/s?" + urlencode({"wd": query, "pn": (page_number - 1) * 10})


def _result_state(page: Page, query: str, page_number: int) -> str:
    parsed = urlparse(page.url)
    params = parse_qs(parsed.query)
    if parsed.hostname not in {"www.baidu.com", "baidu.com"} or parsed.path != "/s":
        return "challenge"
    if params.get("pn", ["0"])[0] != str((page_number - 1) * 10):
        return "challenge"
    if params.get("wd", [None])[0] != query:
        return "challenge"
    if page_number > 1 and _active_result_page(page) != page_number:
        return "loading"
    return page.evaluate("""() => {
      const organic = [...document.querySelectorAll('#content_left h3')]
        .some(node => node.getClientRects().length && !node.closest('.ai-entry'));
      if (organic) return 'results';
      const ai = document.querySelector('#content_left .ai-entry');
      if (ai && ai.getClientRects().length && (ai.innerText || '').trim().length > 80) return 'results';
      const loading = [...document.querySelectorAll('#content_left [class*="skeleton"]')]
        .some(node => node.getClientRects().length);
      if (loading || document.querySelector('#content_left')?.innerText.includes('正在搜索')) return 'loading';
      const results = document.querySelector('#content_left');
      if (results && results.querySelector('.result, .c-container, [data-click]')) return 'results';
      const text = document.body?.innerText || '';
      if (/没有找到|未找到相关结果|抱歉.*没有/.test(text)) return 'empty';
      return 'loading';
    }""")


def _open_search(page: Page, query: str) -> None:
    """Submit the search through Baidu's visible search box."""
    page.goto("https://www.baidu.com/", wait_until="domcontentloaded", timeout=60000)
    if page.locator("#chat-textarea").is_visible():
        page.locator("#chat-textarea").fill(query)
        page.locator("#chat-submit-button").click()
    else:
        page.locator("#kw:visible").fill(query)
        page.locator("#su:visible").click()
    page.wait_for_url(
        lambda url: urlparse(str(url)).path == "/s"
        and parse_qs(urlparse(str(url)).query).get("wd") == [query],
        timeout=60000,
    )


def _active_result_page(page: Page) -> int | None:
    return page.evaluate("""() => {
      const item = [...document.querySelectorAll('#page [class*="page-item"]')]
        .find(node => node.getClientRects().length && !node.closest('a'));
      const number = Number(item?.textContent?.trim());
      return Number.isInteger(number) && number > 0 ? number : null;
    }""")


def _click_result_page(page: Page, query: str, page_number: int, check_stop=lambda: None,
                       settle_seconds: float = PAGE_SELECTION_SETTLE_SECONDS) -> None:
    """Click the visible page number and wait for Baidu to keep that page selected."""
    for attempt in range(3):
        check_stop()
        try:
            page.locator("#page").get_by_role("link", name=str(page_number), exact=True).click()
        except PlaywrightTimeoutError:
            # Baidu may complete navigation while its temporary mask keeps
            # Playwright's click action waiting until it times out.
            parsed = urlparse(page.url)
            params = parse_qs(parsed.query)
            if (parsed.path != "/s" or params.get("wd") != [query]
                    or params.get("pn") != [str((page_number - 1) * 10)]
                    or _active_result_page(page) != page_number):
                raise
        page.wait_for_url(
            lambda url: urlparse(str(url)).path == "/s"
            and parse_qs(urlparse(str(url)).query).get("wd") == [query]
            and parse_qs(urlparse(str(url)).query).get("pn") == [str((page_number - 1) * 10)],
            timeout=60000,
        )
        deadline = time.monotonic() + max(5, settle_seconds + 3)
        selected_since = None
        while time.monotonic() < deadline:
            check_stop()
            if _active_result_page(page) == page_number:
                now = time.monotonic()
                if selected_since is None:
                    selected_since = now
                if now - selected_since >= settle_seconds:
                    return
            else:
                selected_since = None
            page.wait_for_timeout(250)
        if attempt < 2:
            LOGGER.warning("百度搜索第 %s 页未保持选中，重试点击：%s", page_number, query)
    raise RuntimeError(f"百度第 {page_number} 页的内容未能稳定显示：{query}")


def _result_snapshot(page: Page, query: str) -> tuple[tuple[str, ...], dict[int, str]]:
    """Read visible result titles and every pagination link currently offered."""
    data = page.evaluate("""() => ({
      results: [...document.querySelectorAll('#content_left h3')]
        .filter(node => node.getClientRects().length && !node.closest('.ai-entry'))
        .map(node => (node.innerText || '').replace(/\\s+/g, ' ').trim())
        .filter(Boolean),
      ai: (document.querySelector('#content_left .ai-entry')?.innerText || '')
        .replace(/\\s+/g, ' ').trim(),
      next: [...document.querySelectorAll('#page a[href]')]
        .map(node => ({href: node.getAttribute('href'), text: node.innerText || ''}))
    })""")
    results = tuple(dict.fromkeys(data["results"])) or ((data["ai"],) if data["ai"] else ())
    page_urls: dict[int, str] = {}
    for link in data["next"]:
        target = urljoin(page.url, link["href"])
        parsed = urlparse(target)
        if parsed.hostname not in {"www.baidu.com", "baidu.com"} or parsed.path != "/s":
            continue
        params = parse_qs(parsed.query)
        if params.get("wd", [query])[0] != query:
            continue
        label = link["text"].strip()
        if label.isdecimal():
            page_urls[int(label)] = target
            continue
        try:
            pn = int(params.get("pn", [""])[0])
        except ValueError:
            continue
        if pn >= 0 and pn % 10 == 0:
            page_urls[pn // 10 + 1] = target
    return results, page_urls


def _wait_for_complete_results(context: RunContext, query: str,
                               settle_seconds: float = RESULT_SETTLE_SECONDS) -> tuple[tuple[str, ...], dict[int, str]]:
    page = context.page
    deadline = time.monotonic() + 10
    previous: tuple[tuple[str, ...], dict[int, str]] | None = None
    stable_since = time.monotonic()
    while True:
        context.check_stop()
        snapshot = _result_snapshot(page, query)
        now = time.monotonic()
        if snapshot != previous:
            previous = snapshot
            stable_since = now
        elif now - stable_since >= settle_seconds:
            break
        if now >= deadline:
            break
        page.wait_for_timeout(250)
    return _result_snapshot(page, query)


def _settle_seconds(settings: dict[str, bool | str | int | float], key: str,
                    label: str, default: float) -> float:
    try:
        value = float(settings.get(key, default))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"百度{label}必须是秒数") from exc
    if not 0.25 <= value <= 5.0:
        raise ValueError(f"百度{label}必须在 0.25 到 5 秒之间")
    return value


def _wait_for_ready_state(context: RunContext, query: str, page_number: int) -> str:
    page = context.page
    while True:
        state = _result_state(page, query, page_number)
        if state == "loading":
            context.wait_until(
                lambda: _result_state(page, query, page_number) != "loading",
                f"正在等待百度搜索内容加载完成：{query}，第 {page_number} 页…",
                timeout_seconds=180,
            )
        elif state == "challenge":
            context.wait_until(
                lambda: _result_state(page, query, page_number) != "challenge",
                f"百度搜索可能需要验证，请在右侧 Chrome 完成；等待“{query}”第 {page_number} 页…",
                timeout_seconds=600, user_action=True,
            )
        else:
            return state


class BaiduAdapter(SiteAdapter):
    key = "baidu"
    display_name = "百度"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = (
        FieldSpec("common_keyword", "通用关键词", "text", ""),
        FieldSpec("keywords", "关键词（空格分隔）", "text", DEFAULT_KEYWORDS),
        FieldSpec("max_pages", "前 K 页", "integer", 2),
        FieldSpec("page_settle_seconds", "翻页稳定时间（秒）", "decimal",
                  PAGE_SELECTION_SETTLE_SECONDS, minimum=0.25, maximum=5.0),
        FieldSpec("result_settle_seconds", "结果稳定时间（秒）", "decimal",
                  RESULT_SETTLE_SECONDS, minimum=0.25, maximum=5.0),
    )

    def run_one(self, context: RunContext, subject: str) -> None:
        common_keyword = " ".join(str(context.site_settings.get("common_keyword", "")).split())
        keywords = tuple(dict.fromkeys(str(context.site_settings.get("keywords", DEFAULT_KEYWORDS)).split()))
        if not keywords:
            raise ValueError("请填写至少一个百度搜索关键词")
        try:
            max_pages = int(context.site_settings.get("max_pages", 2))
        except (TypeError, ValueError) as exc:
            raise ValueError("百度前 K 页必须是整数") from exc
        if not 1 <= max_pages <= 100:
            raise ValueError("百度前 K 页必须在 1 到 100 之间")
        page_settle = _settle_seconds(context.site_settings, "page_settle_seconds",
                                      "翻页稳定时间", PAGE_SELECTION_SETTLE_SECONDS)
        result_settle = _settle_seconds(context.site_settings, "result_settle_seconds",
                                        "结果稳定时间", RESULT_SETTLE_SECONDS)

        pieces: list[bytes] = []
        page = context.page
        for keyword in keywords:
            query = " ".join(part for part in (common_keyword, subject, keyword) if part)
            previous_results: tuple[str, ...] | None = None
            first_page_links: set[int] = set()
            current_links: set[int] = set()
            for page_number in range(1, max_pages + 1):
                context.check_stop()
                context.report_status(f"正在搜索百度：{query}，第 {page_number} 页")
                try:
                    if page_number == 1:
                        _open_search(page, query)
                        response = None
                    else:
                        if page_number not in current_links:
                            if page_number not in first_page_links:
                                break
                            for _ in range(page_number - 1):
                                parsed = urlparse(page.url)
                                params = parse_qs(parsed.query)
                                if (parsed.path == "/s" and params.get("wd") == [query]
                                        and params.get("pn", ["0"])[0] == "0"):
                                    break
                                page.go_back(wait_until="domcontentloaded", timeout=60000)
                            else:
                                raise RuntimeError(f"无法返回百度搜索首页：{query}")
                        try:
                            _click_result_page(page, query, page_number, context.check_stop, page_settle)
                        except RuntimeError:
                            context.capture_diagnostic(f"baidu-{keyword}-{page_number}-pagination-failed", page)
                            raise
                        response = None
                except PlaywrightError as exc:
                    raise SiteUnavailable(f"无法打开百度搜索页：{exc}") from exc
                if response is not None and response.status >= 500:
                    raise SiteUnavailable(f"百度搜索页返回 HTTP {response.status}")

                state = _wait_for_ready_state(context, query, page_number)
                if state == "empty" and page_number > 1:
                    break
                results, links = _wait_for_complete_results(context, query, result_settle)
                if _result_state(page, query, page_number) == "loading":
                    if page_number > 1 and _active_result_page(page) != page_number:
                        try:
                            _click_result_page(page, query, page_number, context.check_stop, page_settle)
                        except RuntimeError:
                            context.capture_diagnostic(f"baidu-{keyword}-{page_number}-pagination-failed", page)
                            raise
                    state = _wait_for_ready_state(context, query, page_number)
                    results, links = _wait_for_complete_results(context, query, result_settle)
                if state == "results":
                    if not results:
                        raise RuntimeError(f"无法识别百度搜索结果：{query}，第 {page_number} 页")
                    if results == previous_results:
                        break
                    previous_results = results
                current_links = set(links)
                if page_number == 1:
                    first_page_links = current_links.copy()
                page.evaluate("document.fonts.ready")
                page.emulate_media(media="screen")
                page.evaluate("window.scrollTo(0, 0)")
                context.capture_diagnostic(f"baidu-{keyword}-{page_number}-ready-to-print", page)
                pieces.append(context.render_pdf(page))
                if page_number + 1 not in current_links and page_number + 1 not in first_page_links:
                    break

        context.check_stop()
        writer = PdfWriter()
        for piece in pieces:
            writer.append(PdfReader(io.BytesIO(piece)))
        output = io.BytesIO()
        writer.write(output)
        saved = save_pdf_bytes(output.getvalue(), context.pdf_path)
        context.artifacts.append(saved)
        context.report_status(f"已保存百度搜索结果：{saved}")
