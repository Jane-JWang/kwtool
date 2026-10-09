"""Visible-browser workflow for the public Credit China company pages."""

from __future__ import annotations

import io
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Download, Error as PlaywrightError, Page, Request
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import available_path, save_pdf_bytes


HOME = "https://www.creditchina.gov.cn/"
CATEGORIES = ("行政管理", "诚实守信", "严重失信", "经营异常", "信用承诺", "信用评价", "司法判决", "其他")
CATEGORY_TABS = ("#permissionBtn", "#penaltyBtn", "#redBtn", "#blackBtn", "#focusBtn",
                 "#qualificationsBtn", "#riskBtn", "#otherkBtn")
SEARCH_INPUT = "input[placeholder*='主体名称'], input[placeholder*='企业名称'], input[placeholder*='信用代码']"
LOADING = ".loading:visible, .el-loading-mask:visible, .layui-layer-loading:visible, [class*='loading']:visible"


def _site_page(page: Page) -> bool:
    host = urlparse(page.url).hostname or ""
    return host == "creditchina.gov.cn" or host.endswith(".creditchina.gov.cn")


def _detail_page(page: Page) -> bool:
    if not _site_page(page) or "xyDetail.html" not in urlparse(page.url).path:
        return False
    text = page.locator("body").inner_text(timeout=5000)
    return all(marker in text for marker in ("统一社会信用代码", "行政管理", "下载信用信息报告"))


def _find_detail(context: RunContext) -> Page | None:
    for page in reversed(context.browser.pages):
        try:
            if not page.is_closed() and _detail_page(page):
                return page
        except PlaywrightError:
            pass
    return None


def _results_page(context: RunContext, search_page: Page, prior_pages: set[Page]) -> Page | None:
    for page in reversed(context.browser.pages):
        try:
            if (page is search_page or page not in prior_pages) and not page.is_closed() and _site_page(page) \
                    and not page.locator(".vcodepop:visible").count() \
                    and page.locator("#companylists .company-item:visible, .no-data:visible").count():
                return page
        except PlaywrightError:
            pass
    return None


def _search_candidates(page: Page) -> list[tuple[str, int]]:
    """Read clickable search rows without relying on one version of the site's CSS."""
    rows = page.locator("#companylists .company-item")
    if page.locator("#companylists").count():
        return [(rows.nth(index).locator(".company-name").inner_text().strip(), index)
                for index in range(rows.count())]
    return page.evaluate("""() => {
        const nodes = [...document.querySelectorAll('a, [onclick], [role="link"]')];
        const seen = new Set();
        return nodes.map((node, index) => {
            const raw = (node.innerText || '').trim();
            const heading = node.querySelector('h1, h2, h3, h4, [class*="name"]');
            const text = (heading?.innerText || raw.split('\\n')[0] || '').trim().replace(/\\s+/g, ' ');
            const href = node.getAttribute('href') || '';
            if (!text || text === '搜索' || text.length > 160 || seen.has(text) ||
                !(href.includes('xyDetail') || node.closest('[class*="search"], [class*="result"], [class*="list"]')))
                return null;
            seen.add(text);
            return [text, index];
        }).filter(Boolean);
    }""")


def _click_candidate(page: Page, index: int) -> None:
    rows = page.locator("#companylists .company-item")
    if rows.count():
        rows.nth(index).click()
    else:
        page.locator('a, [onclick], [role="link"]').nth(index).click()


def _page_signature(page: Page) -> str:
    return page.evaluate("""() => {
        const content = document.body?.innerText || '';
        const active = [...document.querySelectorAll('.active, .selected, .current, .cur, [aria-selected="true"]')]
            .map(e => e.innerText?.slice(0, 100)).join('|');
        return location.href + '|' + active + '|' + content.slice(-25000);
    }""")


def _challenge_visible(context: RunContext) -> bool:
    selectors = (".vcodepop:visible", "#vcode:visible", "input[placeholder*='验证码']:visible",
                 "iframe[src*='recaptcha']:visible", "iframe[title*='reCAPTCHA']:visible",
                 "[class*='geetest']:visible", "[class*='captcha']:visible")
    for tab in context.browser.pages:
        try:
            if not tab.is_closed() and any(tab.locator(selector).count() for selector in selectors):
                return True
        except PlaywrightError:
            pass
    return False


def _settle(context: RunContext, page: Page, message: str, *, timeout: float = 180,
            pending: set[Request] | None = None, changed_from: str | None = None) -> None:
    """Wait for AJAX completion, hidden loading masks and stable rendered text."""
    context.report_status(message)
    deadline = time.monotonic() + timeout
    last = None
    stable_since = time.monotonic()
    changed = changed_from is None
    challenge_reported = False
    try:
        while time.monotonic() < deadline:
            context.check_stop()
            try:
                challenged = _challenge_visible(context)
                if challenged and not challenge_reported:
                    context.report_status("信用中国出现验证码，请在右侧 Chrome 完成；完成后将自动继续…")
                    context.report_user_action(True)
                    context.capture_diagnostic("section-captcha", page)
                    challenge_reported = True
                elif not challenged and challenge_reported:
                    context.report_status(message)
                    context.report_user_action(False)
                    challenge_reported = False
                signature = _page_signature(page)
                if signature != changed_from:
                    changed = True
                if signature != last:
                    last = signature
                    stable_since = time.monotonic()
                busy = challenged or bool(pending) or page.locator(LOADING).count() > 0
                if changed and not busy and time.monotonic() - stable_since >= 2:
                    return
            except PlaywrightError:
                pass
            page.wait_for_timeout(300)
        raise TimeoutError(message + "超时，请检查右侧 Chrome 中页面是否完成加载")
    finally:
        context.report_user_action(False)


class _NetworkWatch:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.pending: set[Request] = set()

    def __enter__(self) -> set[Request]:
        def started(request: Request) -> None:
            if request.resource_type in {"xhr", "fetch"}:
                self.pending.add(request)

        def ended(request: Request) -> None:
            self.pending.discard(request)

        self.started = started
        self.ended = ended
        self.page.on("request", started)
        self.page.on("requestfinished", ended)
        self.page.on("requestfailed", ended)
        return self.pending

    def __exit__(self, *_exc: object) -> None:
        self.page.remove_listener("request", self.started)
        self.page.remove_listener("requestfinished", self.ended)
        self.page.remove_listener("requestfailed", self.ended)


def _next_button(page: Page):
    for selector in (".layui-laypage-next", ".el-pagination .btn-next", ".pagination .next", ".page .next"):
        item = page.locator(selector).last
        if item.count() and item.is_visible():
            return item
    for label in ("下一页", "下页"):
        item = page.get_by_text(label, exact=True).last
        if item.count() and item.is_visible():
            return item
    return None


def _next_enabled(button) -> bool:
    return (button is not None and button.is_visible()
            and button.evaluate("element => element.tagName.toLowerCase()") != "span"
            and button.get_attribute("disabled") is None
            and button.get_attribute("aria-disabled") != "true"
            and not re.search(r"disabled|disable|layui-disabled", button.get_attribute("class") or "", re.I)
            and not re.search(r"disabled|disable|layui-disabled",
                              button.locator("xpath=..").get_attribute("class") or "", re.I))


def _section_has_data(page: Page, index: int) -> bool:
    badge = page.locator(f"{CATEGORY_TABS[index]} em").first
    count = badge.inner_text().strip() if badge.count() else ""
    if count == "0":
        return True
    return page.locator(".result-table:visible, .no-data:visible").count() > 0


def _empty_section_ready(page: Page, index: int) -> bool:
    tile = page.locator(CATEGORY_TABS[index]).first
    if not tile.count() or "cur" not in (tile.get_attribute("class") or "").split():
        return False
    badge = tile.locator("em").first
    if not badge.count() or badge.inner_text().strip() != "0":
        return False
    if page.locator(".nodata:visible").count():
        return True
    body = page.locator("body").inner_text()
    return any(marker in body for marker in ("没有找到您搜索的数据", "暂无数据", "没有相关数据"))


def _wait_empty_section(context: RunContext, page: Page, index: int, name: str) -> None:
    message = f"等待{name}板块的无数据结果显示…"
    challenged = False
    started = time.monotonic()
    slow_snapshot = False

    def ready() -> bool:
        nonlocal challenged, slow_snapshot
        visible = _challenge_visible(context)
        if visible and not challenged:
            context.report_status(f"{name}板块触发验证码，请在右侧 Chrome 完成；完成后将自动继续…")
            context.report_user_action(True)
            context.capture_diagnostic(f"{name}-captcha", page)
            challenged = True
        elif not visible and challenged:
            context.report_status(message)
            context.report_user_action(False)
            challenged = False
        if not slow_snapshot and time.monotonic() - started >= 15:
            context.capture_diagnostic(f"{name}-waiting", page)
            slow_snapshot = True
        return not visible and _empty_section_ready(page, index)

    try:
        context.wait_until(ready, message, timeout_seconds=600)
        page.wait_for_timeout(500)
    finally:
        context.report_user_action(False)


class CreditChinaAdapter(SiteAdapter):
    key = "creditchina"
    display_name = "信用中国"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = tuple(FieldSpec(f"section_{index}", name, "bool", name in {"行政管理", "严重失信", "经营异常"})
                            for index, name in enumerate(CATEGORIES))

    def _search_page(self, context: RunContext) -> Page:
        page = context.page
        status = None
        try:
            if not _site_page(page) or _detail_page(page):
                response = page.goto(HOME, wait_until="commit", timeout=90000)
                status = response.status if response else None
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开信用中国：{exc}") from exc
        try:
            context.wait_until(lambda: page.locator(SEARCH_INPUT).first.is_visible(),
                               "等待信用中国搜索框加载…", timeout_seconds=180)
        except TimeoutError as exc:
            if status and status >= 400:
                raise SiteUnavailable(f"信用中国返回 HTTP {status}，等待后仍未显示搜索框") from exc
            raise
        return page

    def _find_company(self, context: RunContext, subject: str) -> tuple[Page, Page]:
        page = self._search_page(context)
        prior_pages = set(context.browser.pages)
        page.locator(SEARCH_INPUT).first.fill(subject)
        search = page.get_by_role("button", name="搜索").first
        if not search.count() or not search.is_visible():
            search = page.locator("button:has-text('搜索'), [class*='search'] button, input[type='submit']").first
        if not search.count() or not search.is_visible():
            raise ValueError("信用中国页面未找到搜索按钮")
        with _NetworkWatch(page) as pending:
            search.click()
            context.wait_until(lambda: _find_detail(context) is not None or
                               _results_page(context, page, prior_pages) is not None,
                               "请在 Chrome 中填写验证码；等待搜索结果…", timeout_seconds=600, user_action=True)
        detail = _find_detail(context)
        if detail:
            return detail, page
        results = _results_page(context, page, prior_pages)
        assert results is not None
        _settle(context, results, "等待信用中国搜索列表完整加载…", timeout=180)
        context.capture_diagnostic("search-results", results)
        candidates = _search_candidates(results)
        exact = [(name, index) for name, index in candidates if name == subject]
        if len(exact) == 1:
            context.report_status(f"找到完全匹配企业：{subject}；正在打开详情页…")
            _click_candidate(results, exact[0][1])
            user_action = False
        else:
            if not candidates:
                raise ValueError(f"信用中国未找到企业：{subject}")
            user_action = True
        wait_message = (f"{subject} 没有唯一完全匹配项，请在右侧 Chrome 的结果列表点击正确企业；选好后将自动继续…"
                        if user_action else "等待自动打开企业详情页…")
        try:
            context.wait_until(lambda: _find_detail(context) is not None,
                               wait_message, timeout_seconds=600 if user_action else 180,
                               user_action=user_action)
        except TimeoutError as exc:
            for tab_index, tab in enumerate(context.browser.pages, 1):
                if not tab.is_closed() and _site_page(tab):
                    context.capture_diagnostic(f"detail-wait-tab-{tab_index}", tab)
            raise TimeoutError("未等到企业详情板块和下载按钮显示；请检查右侧 Chrome 的页面或验证码") from exc
        detail = _find_detail(context)
        assert detail is not None
        _settle(context, detail, "等待企业详情完整加载…")
        if not _detail_page(detail):
            raise RuntimeError("企业详情页加载后跳转离开，请重新查询")
        context.capture_diagnostic("detail-ready", detail)
        return detail, results

    def _print_sections(self, context: RunContext, page: Page) -> Path:
        pieces: list[bytes] = []
        first_section = True
        for index, name in enumerate(CATEGORIES):
            if not context.site_settings.get(f"section_{index}", name in {"行政管理", "严重失信", "经营异常"}):
                continue
            context.check_stop()
            tile = page.locator(CATEGORY_TABS[index]).first
            if not tile.count() or not tile.is_visible():
                raise ValueError(f"详情页未找到板块：{name}")
            before = _page_signature(page)
            with _NetworkWatch(page) as pending:
                if "cur" not in (tile.get_attribute("class") or "").split():
                    tile.click()
                badge = tile.locator("em").first
                if badge.count() and badge.inner_text().strip() == "0":
                    _wait_empty_section(context, page, index, name)
                else:
                    _settle(context, page, f"等待{name}板块加载…", pending=pending,
                            changed_from=before if not first_section else None)
                    context.wait_until(lambda: _section_has_data(page, index),
                                       f"等待{name}板块的记录加载完成…", timeout_seconds=180)
                first_section = False
                seen: set[str] = set()
                for page_number in range(1, 1001):
                    context.check_stop()
                    signature = _page_signature(page)
                    if signature in seen:
                        raise RuntimeError(f"{name}翻页后内容未变化，已停止以避免重复导出")
                    seen.add(signature)
                    page.evaluate("window.scrollTo(0, 0)")
                    context.report_status(f"正在打印{name}第 {page_number} 页…")
                    pieces.append(context.render_pdf(page))
                    next_button = _next_button(page)
                    if not _next_enabled(next_button):
                        break
                    before = _page_signature(page)
                    next_button.click()
                    _settle(context, page, f"等待{name}第 {page_number + 1} 页加载…",
                            pending=pending, changed_from=before)
                else:
                    raise RuntimeError(f"{name}分页超过 1000 页，请检查网站分页状态")
        if not pieces:
            raise ValueError("请至少勾选一个要导出的网站板块")
        writer = PdfWriter()
        for data in pieces:
            writer.append(PdfReader(io.BytesIO(data)))
        output = io.BytesIO()
        writer.write(output)
        destination = context.pdf_path.with_name(f"{context.pdf_path.stem}_网站.pdf")
        saved = save_pdf_bytes(output.getvalue(), destination)
        context.artifacts.append(saved)
        return saved

    def _download_report(self, context: RunContext, page: Page) -> Path:
        downloads: list[Download] = []
        def on_download(download: Download) -> None:
            downloads.append(download)

        new_pages: list[Page] = []

        def on_page(new_page: Page) -> None:
            new_pages.append(new_page)
            new_page.on("download", on_download)

        page.on("download", on_download)
        context.browser.on("page", on_page)
        try:
            button = page.get_by_text("下载信用信息报告", exact=True).first
            if not button.count() or not button.is_visible():
                raise ValueError("详情页未找到‘下载信用信息报告’按钮")
            button.click()
            context.wait_until(lambda: bool(downloads), "等待信用信息报告下载；如有验证码，请在 Chrome 中完成…",
                               timeout_seconds=600, user_action=True)
            download = downloads[0]
            destination = available_path(context.pdf_path.with_name(f"{context.pdf_path.stem}_导出报告.pdf"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            download.save_as(destination)
            if download.failure():
                destination.unlink(missing_ok=True)
                raise RuntimeError(f"信用信息报告下载失败：{download.failure()}")
            if not destination.read_bytes().startswith(b"%PDF"):
                destination.unlink(missing_ok=True)
                raise ValueError("网站下载的信用信息报告不是 PDF")
            context.artifacts.append(destination)
            return destination
        finally:
            page.remove_listener("download", on_download)
            context.browser.remove_listener("page", on_page)
            for new_page in new_pages:
                if not new_page.is_closed():
                    new_page.remove_listener("download", on_download)

    def run_one(self, context: RunContext, subject: str) -> None:
        search_page = context.page
        detail: Page | None = None
        results: Page | None = None
        try:
            detail, results = self._find_company(context, subject)
            context.page = detail
            website_pdf = self._print_sections(context, detail)
            context.report_status(f"已保存网站 PDF：{website_pdf}；正在下载信用信息报告…")
            report_pdf = self._download_report(context, detail)
            context.report_status(f"已保存信用信息报告：{report_pdf}")
        finally:
            context.page = search_page
            if detail is not None and detail is not search_page and not detail.is_closed():
                detail.close()
            if results is not None and results is not search_page and results is not detail and not results.is_closed():
                results.close()
