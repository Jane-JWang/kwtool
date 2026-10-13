"""Shared browser search helpers for national websites."""
from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from playwright.sync_api import Error as PlaywrightError
from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


@dataclass(frozen=True)
class SearchSite:
    url: str
    hosts: tuple[str, ...]
    inputs: tuple[str, ...]
    buttons: tuple[str, ...] = ("button:has-text('搜索')", "button:has-text('检索')", "input[type=submit]")
    result_markers: tuple[str, ...] = ("搜索结果", "检索结果", "查询结果", "相关结果", "没有找到", "暂无结果", "无相关结果")
    query_key: str | None = None



def _allowed(url: str, hosts: tuple[str, ...]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == root or host.endswith("." + root) for root in hosts)



def _first_visible(page, selectors: tuple[str, ...]):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            for index in range(min(locator.count(), 8)):
                candidate = locator.nth(index)
                if candidate.is_visible() and candidate.is_enabled():
                    return candidate
        except PlaywrightError:
            continue
    return None



class _NationalSearchAdapter(SiteAdapter):
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    site: SearchSite

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status(f"正在检索{self.display_name}：{subject}")
        try:
            response = page.goto(self.site.url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开{self.display_name}：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"{self.display_name}返回 HTTP {response.status}")
        if not _allowed(page.url, self.site.hosts):
            raise SiteUnavailable(f"{self.display_name}跳转至非预期网站：{page.url}")

        search = _first_visible(page, self.site.inputs)
        if search is None:
            context.capture_diagnostic("search-box-missing", page)
            raise RuntimeError(f"{self.display_name}未找到可用的检索框，请检查网站页面")
        initial_url = page.url
        search.fill(subject)
        existing_pages = set(context.browser.pages)
        submit = _first_visible(page, self.site.buttons)
        if submit is not None:
            submit.click()
        else:
            search.press("Enter")

        def has_results() -> bool:
            nonlocal page
            for candidate in context.browser.pages:
                if candidate not in existing_pages and _allowed(candidate.url, self.site.hosts):
                    page = candidate
            if not _allowed(page.url, self.site.hosts):
                return False
            body = page.locator("body").inner_text(timeout=5000)
            # Some sites show a challenge before results.  Wait for a person
            # to solve it, rather than printing the challenge as a result.
            if any(word in body for word in ("请输入验证码", "请完成验证", "请先登录")):
                return False
            query_matches = (self.site.query_key is not None and
                             subject in parse_qs(urlparse(page.url).query).get(self.site.query_key, []))
            return (subject in body or query_matches) and (
                page.url != initial_url or any(marker in body for marker in self.site.result_markers)
            ) and any(marker in body for marker in self.site.result_markers)

        try:
            context.wait_until(has_results, f"等待{self.display_name}检索结果；如出现验证，请在浏览器中完成", timeout_seconds=180, user_action=True)
        except TimeoutError:
            context.capture_diagnostic("search-results-timeout", page)
            raise RuntimeError(f"{self.display_name}未能确认检索结果，未保存 PDF")
        self._handle_results(context, page)

    def _handle_results(self, context: RunContext, page) -> None:
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存{self.display_name}检索结果：{saved}")

