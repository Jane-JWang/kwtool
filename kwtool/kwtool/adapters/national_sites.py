"""Search public information on national government and judicial sites.

The sites do not share a search API.  Keep their entry points and search box
selectors here, and fail visibly when a site changes its search UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse

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
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存{self.display_name}检索结果：{saved}")


class MeeAdapter(SiteAdapter):
    key = "mee"
    display_name = "生态环境部"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        url = "https://www.mee.gov.cn/searchnew/?" + urlencode({"searchword": subject})
        context.report_status(f"正在检索生态环境部：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开生态环境部搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"生态环境部搜索返回 HTTP {response.status}")
        if not _allowed(context.page.url, ("mee.gov.cn",)):
            raise SiteUnavailable(f"生态环境部搜索跳转至非预期网站：{context.page.url}")

        def loaded() -> bool:
            body = context.page.locator("body").inner_text(timeout=5000)
            return "搜索结果" in body and "符合条件的有" in body

        try:
            context.wait_until(loaded, "等待生态环境部检索结果", timeout_seconds=60)
        except TimeoutError as exc:
            context.capture_diagnostic("mee-search-timeout", context.page)
            raise RuntimeError("生态环境部未能确认检索结果，未保存 PDF") from exc
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", context.page)
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存生态环境部检索结果：{saved}")


class MemAdapter(_NationalSearchAdapter):
    key = "mem"
    display_name = "应急管理部"
    site = SearchSite("https://www.mem.gov.cn/was5/web/sousuo/index.html?stype=0", ("mem.gov.cn",),
                      ("#key",), ("#query_btn",),
                      result_markers=("网页结果共", "搜索结果", "暂无结果"), query_key="sw")


class SamrAdapter(SiteAdapter):
    key = "samr"
    display_name = "国家市场监督管理总局"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        url = "https://www.samr.gov.cn/api-gateway/jpaas-jsearch-web-server/search?" + urlencode((
            ("cateid", "165ddc6c5a0f42d4aeda15dd916f2e6d"),
            ("q", subject),
            ("serviceId", "9c455a05a4ca4052bd5490ddcead7b42"),
        ))
        context.report_status(f"正在检索国家市场监督管理总局：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开国家市场监督管理总局搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"国家市场监督管理总局搜索返回 HTTP {response.status}")
        if not _allowed(context.page.url, ("samr.gov.cn",)):
            raise SiteUnavailable(f"国家市场监督管理总局搜索跳转至非预期网站：{context.page.url}")

        def loaded() -> bool:
            body = context.page.locator("body").inner_text(timeout=5000)
            query = parse_qs(urlparse(context.page.url).query).get("q", [])
            return subject in query and "查询范围" in body and "排序方式" in body

        try:
            context.wait_until(loaded, "等待国家市场监督管理总局检索结果", timeout_seconds=60)
        except TimeoutError as exc:
            context.capture_diagnostic("samr-search-timeout", context.page)
            raise RuntimeError("国家市场监督管理总局未能确认检索结果，未保存 PDF") from exc
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", context.page)
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存国家市场监督管理总局检索结果：{saved}")


class NdrcAdapter(SiteAdapter):
    key = "ndrc"
    display_name = "国家发展改革委"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        url = "https://so.ndrc.gov.cn/s?" + urlencode((("qt", subject), ("siteCode", "bm04000007")))
        context.report_status(f"正在检索国家发展改革委：{subject}")
        try:
            response = context.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开国家发展改革委搜索：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"国家发展改革委搜索返回 HTTP {response.status}")
        if not _allowed(context.page.url, ("ndrc.gov.cn",)):
            raise SiteUnavailable(f"国家发展改革委搜索跳转至非预期网站：{context.page.url}")
        def loaded() -> bool:
            body = context.page.locator("body").inner_text(timeout=5000)
            return subject in parse_qs(urlparse(context.page.url).query).get("qt", []) and any(
                word in body for word in ("相关结果", "暂无结果", "未找到相关")
            )
        context.wait_until(loaded, "等待国家发展改革委检索结果", timeout_seconds=60)
        context.page.evaluate("document.fonts.ready")
        context.page.emulate_media(media="screen")
        context.page.evaluate("window.scrollTo(0, 0)")
        saved = context.save_pdf(context.page)
        context.report_status(f"已保存国家发展改革委检索结果：{saved}")


class StaAdapter(_NationalSearchAdapter):
    key = "sta"
    display_name = "国家税务总局"
    site = SearchSite("https://www.chinatax.gov.cn/chinatax/index.html", ("chinatax.gov.cn",),
                      ("#header-search-txt",), ("#header-search-btn",))


class CcdiAdapter(_NationalSearchAdapter):
    key = "ccdi"
    display_name = "中央纪委国家监委"
    site = SearchSite("https://www.ccdi.gov.cn/", ("ccdi.gov.cn",),
                      ("#searchword",), ("form[name=searchFrom] input[type=submit]",))


class Prosecutor12309Adapter(_NationalSearchAdapter):
    key = "prosecutor_12309"
    display_name = "12309中国检察网"
    site = SearchSite("https://www.12309.gov.cn/", ("12309.gov.cn",),
                      ("input[name=text]",), ("#btn1",))


class WenshuAdapter(_NationalSearchAdapter):
    key = "wenshu"
    display_name = "中国裁判文书网"
    site = SearchSite("https://wenshu.court.gov.cn/", ("court.gov.cn",),
                      ("input[placeholder*='案由、关键词']",),
                      (".search-rightBtn.search-click",), query_key="s21")
