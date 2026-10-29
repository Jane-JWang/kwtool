"""Official full-site searches for additional national agencies."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed
from kwtool.models import PdfSettings


class _DirectMinistrySearch(SiteAdapter):
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)
    host: str
    path: str
    query_key: str
    parameters: tuple[tuple[str, str], ...] = ()
    result_markers: tuple[str, ...]

    def results_visible(self, context: RunContext, subject: str, body: str) -> bool:
        return any(marker in body for marker in self.result_markers)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        query = urlencode((*self.parameters, (self.query_key, subject)))
        url = f"https://{self.host}{self.path}?{query}"
        context.report_status(f"正在检索{self.display_name}：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开{self.display_name}检索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"{self.display_name}检索返回 HTTP {response.status}")
        if not _allowed(context.page.url, (self.host.split(".", 1)[-1],)):
            raise SiteUnavailable(f"{self.display_name}检索跳转至非预期网站：{context.page.url}")

        def loaded() -> bool:
            page_url = urlparse(context.page.url)
            if subject not in parse_qs(page_url.query).get(self.query_key, []):
                return False
            body = context.page.locator("body").inner_text(timeout=5000)
            return self.results_visible(context, subject, body)

        try:
            context.wait_until(loaded, f"等待{self.display_name}检索结果", timeout_seconds=60)
        except TimeoutError as exc:
            context.capture_diagnostic("search-results-timeout", context.page)
            raise RuntimeError(f"{self.display_name}未能确认检索结果，未保存 PDF") from exc
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", context.page)
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存{self.display_name}检索结果：{saved}")


class MnrAdapter(_DirectMinistrySearch):
    key = "mnr"
    display_name = "自然资源部"
    host = "search.mnr.gov.cn"
    path = "/was5/web/search"
    query_key = "searchword"
    parameters = (("channelid", "227454"),)
    result_markers = ("相关结果约", "没有找到和您的查询相匹配的结果")


class MohurdAdapter(_DirectMinistrySearch):
    key = "mohurd"
    display_name = "住房和城乡建设部"
    host = "www.mohurd.gov.cn"
    path = "/api-gateway/jpaas-jsearch-web-server/search"
    query_key = "q"
    parameters = (("serviceId", "e2f3058e2a3b4f8abc93eb76e739e3e7"),
                  ("websiteid", ""), ("cateid", "6ca0f12c0f0642ab8b1dc17028e12ea1"))
    result_markers = ("查询范围：",)

    def results_visible(self, context: RunContext, subject: str, body: str) -> bool:
        # The search page initially displays a cached example query. Wait for
        # its JavaScript to replace that example and render result items or
        # the site's no-results illustration.
        # Result snippets split matches into individual <em> characters, so
        # the complete subject often does not occur in the body text.
        return (super().results_visible(context, subject, body)
                and context.page.locator("#q").input_value() == subject
                and (context.page.locator(".bszn_list .comprehensiveItem").count() > 0
                     or context.page.locator(".no_results").is_visible()))


class NhcAdapter(_DirectMinistrySearch):
    key = "nhc"
    display_name = "国家卫生健康委员会"
    # The search form on nhc.gov.cn submits to this official search service.
    host = "zs.kaipuyun.cn"
    path = "/s"
    query_key = "searchWord"
    parameters = (("siteCode", "bm24000006"),)
    result_markers = ("相关结果",)

    def results_visible(self, context: RunContext, subject: str, body: str) -> bool:
        return (super().results_visible(context, subject, body)
                and parse_qs(urlparse(context.page.url).query).get("siteCode") == ["bm24000006"]
                and context.page.locator("#allSearchWord").input_value() == subject)


class SafeAdapter(_DirectMinistrySearch):
    key = "safe"
    display_name = "国家外汇管理局"
    host = "www.safe.gov.cn"
    path = "/safe/search/index.html"
    query_key = "q"
    parameters = (("siteid", "safe"), ("order", "releasetime"))
    result_markers = ("条结果",)

    def results_visible(self, context: RunContext, subject: str, body: str) -> bool:
        # The outer page contains no results. The actual search, including
        # zero matches, is rendered in its named iframe.
        frame = context.page.frame(name="iframepage")
        if frame is None:
            return False
        frame_url = urlparse(frame.url)
        if (frame_url.path != "/internet_app/search"
                or parse_qs(frame_url.query).get("siteid") != ["safe"]
                or parse_qs(frame_url.query).get("q") != [subject]):
            return False
        if frame.locator("#q_search").input_value() != subject:
            return False
        result = frame.locator("body").inner_text(timeout=5000)
        return re.search(r"共有\s*\d+\s*条结果", result) is not None
