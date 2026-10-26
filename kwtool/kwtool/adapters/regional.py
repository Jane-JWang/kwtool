"""Province-level official website searches selected by subject registration."""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed, _first_visible
from kwtool.models import PdfSettings


PROVINCES = ("广东省", "浙江省", "北京市", "上海市")
ALIASES = dict(zip(("广东", "浙江", "北京", "上海"), PROVINCES))


@dataclass(frozen=True)
class RegionalSite:
    name: str
    url: str
    hosts: tuple[str, ...]
    inputs: tuple[str, ...]
    buttons: tuple[str, ...]
    result_path: str
    query_keys: tuple[str, ...]
    # Only endpoints copied from an official search form may be used here.
    params: tuple[tuple[str, str], ...] = ()
    direct_query: str = ""
    encoding: str = "utf-8"

    def start_url(self, subject: str) -> str:
        if not self.direct_query:
            return self.url
        return self.url + "?" + urlencode((*self.params, (self.direct_query, subject)), encoding=self.encoding)


SITES: dict[str, dict[str, RegionalSite]] = {
    "regional_court": {
        "广东省": RegionalSite("广东省高级人民法院", "https://search.gdcourts.gov.cn/search/local/208001",
            ("gdcourts.gov.cn",), ("#input-keywords",), ("#search-button",),
            r"/search/local/208001", ("keywords",), direct_query="keywords"),
        "浙江省": RegionalSite("浙江省高级人民法院", "https://zjsfgkw.gov.cn/",
            ("zjsfgkw.gov.cn",), ("#search-form input[name=q]",), ("#search-form button",),
            r"/jrobot/search\.do", ("q",)),
        "北京市": RegionalSite("北京市高级人民法院", "https://bjgy.bjcourt.gov.cn/",
            ("bjgy.bjcourt.gov.cn",), ("#keyword",), ("#article_content input[type=submit]",),
            r"/article/essearch\.shtml", ("keyword",)),
        "上海市": RegionalSite("上海市高级人民法院", "https://www.hshfy.sh.cn/shfy/web/search_re.jsp",
            ("www.hshfy.sh.cn",), (), (), r"/shfy/web/search_re\.jsp", ("nr",),
            params=(("fbrqks", ""), ("fbrqjs", ""), ("bt", "")), direct_query="nr", encoding="gbk"),
    },
    "regional_ccdi": {
        "广东省": RegionalSite("广东省纪委监委（南粤清风网）", "https://www.gdjct.gd.gov.cn/",
            ("www.gdjct.gd.gov.cn", "gddqsearch.southcn.com"), ("#txtKey",),
            ("#searchForm button[type=submit]",), r"/search/all/204001", ("keywords",)),
        "浙江省": RegionalSite("浙江省纪委监委", "https://www.zjsjw.gov.cn/",
            ("www.zjsjw.gov.cn", "i.tmuyun.com"), ("#anyWord",), ("#searchForm button",),
            r"/allsearch", ("keyword",)),
        "北京市": RegionalSite("北京市纪委监委", "https://www.bjsupervision.gov.cn/",
            ("www.bjsupervision.gov.cn",), ("#textfield",), ("form[name=searchform] input[type=submit]",),
            r"/was5/web/search", ("searchword",)),
        "上海市": RegionalSite("上海市纪委监委", "https://www.shjjjc.gov.cn/",
            ("www.shjjjc.gov.cn",), ("#query",), ("#searchForm a.btn-main",),
            r"/TrueCMS/searchController/getResult\.do", ("query",)),
    },
    "regional_procuratorate": {
        "广东省": RegionalSite("广东省人民检察院", "http://www.gd.jcy.gov.cn/",
            ("www.gd.jcy.gov.cn", "jcysearch.jcrb.com"), ("input[name=searchWord]",),
            ("form[name=searchform] input[type=submit]",), r"/search/search\.shtml", ("searchWord",)),
        "浙江省": RegionalSite("浙江省人民检察院", "https://www.zjjcy.gov.cn/",
            ("www.zjjcy.gov.cn",), ("#hotwords",), ("form[name=sitesearch] input[type=submit]",),
            r"/(?:jcms/jcms_files/jcms1/web1/site/)?module/sitesearch/index\.jsp", ("keyvalue",)),
        "北京市": RegionalSite("北京市人民检察院", "https://www.bjjc.gov.cn/",
            ("www.bjjc.gov.cn",), ("#keyword",), (".fd-top-search span[onclick]",),
            r"/search\.htm", ("keyword",)),
        "上海市": RegionalSite("上海市人民检察院", "https://www.sh.jcy.gov.cn/",
            ("www.sh.jcy.gov.cn",), ("#searchText1",), ("input[onclick*='searchAll']",),
            r"/shjcysearch\.jspx", ("q",)),
    },
    "regional_samr": {
        "广东省": RegionalSite("广东省市场监督管理局", "https://search.gd.gov.cn/search/local_msg/144",
            ("search.gd.gov.cn",), ("input[name=keywords]",), ("button[type=submit]",),
            r"/search/(?:local_msg|all)/144", ("keywords",), direct_query="keywords"),
        "浙江省": RegionalSite("浙江省市场监督管理局", "https://zjamr.zj.gov.cn/",
            ("zjamr.zj.gov.cn", "search.zj.gov.cn"), ("#allkeyword",),
            ("input[onclick*='zjamrDoSearch']",), r"/api-gateway/jpaas-jsearch-web-server/search", ("q",)),
        "北京市": RegionalSite("北京市市场监督管理局", "https://scjgj.beijing.gov.cn/",
            ("scjgj.beijing.gov.cn", "www.beijing.gov.cn"), ("#qt",), ("#form1 input[type=submit]",),
            r"/so/s", ("qt",)),
        "上海市": RegionalSite("上海市市场监督管理局", "https://scjgj.sh.gov.cn/",
            ("scjgj.sh.gov.cn",), ("#searchword",), ("input.search_submit",),
            r"/kbym/", ("query",)),
    },
}

RESULT_COUNT = re.compile(r"(?:共|找到|检索到|搜索到|约)[\s\S]{0,60}?\d[\d,，\s]*\s*(?:条|个|篇)|"
                          r"(?:没有找到|未找到|未检索到|未搜索到|暂无结果|无相关结果|暂无数据|没有相关|"
                          r"暂时没有信息|搜索结果不存在|找不到和您的查询)")
CHALLENGES = ("请输入验证码", "请完成验证", "请先登录", "安全验证", "访问验证")
BLOCKED = ("Access Denied", "访问行为存在异常", "502 Bad Gateway", "403 Forbidden", "405- Method Not Allowed")


def resolve_province(value: str) -> str:
    value = value.strip()
    province = ALIASES.get(value, value)
    if province not in PROVINCES:
        raise ValueError(f"不支持的注册地：{value or '未填写'}；目前支持：{'、'.join(PROVINCES)}")
    return province


def query_matches(url: str, keys: tuple[str, ...], subject: str, encoding: str = "utf-8") -> bool:
    parsed = urlparse(url)
    # Shanghai market regulation embeds query=... in its #search/ route.
    values = parse_qs(parsed.query, encoding=encoding)
    values.update(parse_qs(parsed.fragment.removeprefix("search/")))
    return any(subject in values.get(key, []) for key in keys)


class _RegionalAdapter(SiteAdapter):
    required_kwargs = ("注册地",)
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        province = resolve_province(context.subject_kwargs.get("注册地", ""))
        site = SITES[self.key][province]
        context.check_stop()
        context.report_status(f"正在检索{site.name}：{subject}（第 1 页）")
        page = context.page
        original_pages = set(context.browser.pages)
        submitted_urls: set[str] = set()
        pending_requests: set = set()

        def on_request(request) -> None:
            if not _allowed(request.url, site.hosts):
                return
            matches = query_matches(request.url, site.query_keys, subject, site.encoding)
            # Ignore background counters and optional recommendation requests.
            # Track the search APIs and requests carrying this subject instead.
            if request.resource_type in ("xhr", "fetch") and (matches or "/api/search" in urlparse(request.url).path):
                pending_requests.add(request)
            if matches:
                submitted_urls.add(request.url.split("?", 1)[0])
            if request.method == "POST" and request.post_data:
                values = parse_qs(request.post_data)
                if any(subject in values.get(key, []) for key in site.query_keys):
                    submitted_urls.add(request.url.split("?", 1)[0])

        def on_request_done(request) -> None:
            pending_requests.discard(request)

        context.browser.on("request", on_request)
        context.browser.on("requestfinished", on_request_done)
        context.browser.on("requestfailed", on_request_done)
        try:
            try:
                response = page.goto(site.start_url(subject), wait_until="domcontentloaded", timeout=60000)
            except PlaywrightError as exc:
                raise SiteUnavailable(f"无法打开{site.name}：{exc}") from exc
            if response is not None and response.status >= 400:
                raise SiteUnavailable(f"{site.name}返回 HTTP {response.status}")
            if not _allowed(page.url, site.hosts):
                raise SiteUnavailable(f"{site.name}跳转至非预期网站：{page.url}")

            if not site.direct_query:
                controls = []

                def search_ready() -> bool:
                    controls.clear()
                    for frame in page.frames:
                        if not _allowed(frame.url, site.hosts):
                            continue
                        search = _first_visible(frame, site.inputs)
                        if search is not None:
                            controls.extend((frame, search))
                            return True
                    return False

                context.wait_until(search_ready, f"等待{site.name}搜索框加载；如有验证请手动完成",
                                   timeout_seconds=60, user_action=True)
                frame, search = controls
                # The Guangdong disciplinary site defaults to title-only search.
                if self.key == "regional_ccdi" and province == "广东省":
                    frame.locator("#sxt").select_option("content")
                search.fill(subject)
                button = _first_visible(frame, site.buttons)
                if button is not None:
                    button.click()
                else:
                    search.press("Enter")

            stable_results: dict[str, tuple[str, float]] = {}

            def results_ready() -> bool:
                nonlocal page
                if pending_requests:
                    stable_results.clear()
                    return False
                candidates = [page] + [p for p in context.browser.pages if p not in original_pages]
                for candidate in candidates:
                    if candidate.is_closed() or not _allowed(candidate.url, site.hosts):
                        continue
                    parsed = urlparse(candidate.url)
                    if not re.fullmatch(site.result_path + r"/?", parsed.path):
                        continue
                    try:
                        body = candidate.locator("body").inner_text(timeout=2000)
                    except PlaywrightError:
                        # Popups may be between navigation and document creation.
                        continue
                    if any(message in body for message in BLOCKED):
                        raise SiteUnavailable(f"{site.name}拦截了检索请求，请在浏览器中检查站点提示")
                    if any(message in body for message in CHALLENGES):
                        continue
                    proven = (query_matches(candidate.url, site.query_keys, subject, site.encoding)
                              or candidate.url.split("?", 1)[0] in submitted_urls)
                    if proven and RESULT_COUNT.search(body):
                        signature, since = stable_results.get(candidate.url, ("", 0.0))
                        if signature != body:
                            stable_results[candidate.url] = (body, time.monotonic())
                            continue
                        # Let asynchronous lists settle before printing a zero
                        # count or the first partially rendered batch of cards.
                        if time.monotonic() - since >= 0.75:
                            page = candidate
                            return True
                return False

            context.wait_until(results_ready, f"等待{site.name}检索结果；如出现验证，请在浏览器中完成",
                               timeout_seconds=180, user_action=True)
            if self.key == "regional_court" and province == "北京市":
                # The POST response loses the visible keyword; preserve the
                # submitted term in the PDF, as with the national search sites.
                search = _first_visible(page, ("input[name=keyword]",))
                if search is not None:
                    search.fill(subject)
            page.evaluate("document.fonts.ready")
            page.emulate_media(media="screen")
            page.evaluate("window.scrollTo(0, 0)")
            context.capture_diagnostic("ready-to-print", page)
            saved = context.save_pdf(page)
            context.report_status(f"已保存{site.name}检索结果第 1 页：{saved}")
        except TimeoutError as exc:
            context.capture_diagnostic("regional-search-timeout", page)
            raise RuntimeError(f"{site.name}未能确认检索结果，未保存 PDF") from exc
        except SiteUnavailable as exc:
            # Other rows may use a different province's independent website.
            # A provincial outage must not abort the entire multi-province batch.
            context.capture_diagnostic("regional-site-unavailable", page)
            raise RuntimeError(str(exc)) from exc
        finally:
            context.browser.remove_listener("request", on_request)
            context.browser.remove_listener("requestfinished", on_request_done)
            context.browser.remove_listener("requestfailed", on_request_done)
            for opened in list(context.browser.pages):
                if opened not in original_pages and _allowed(opened.url, site.hosts) and not opened.is_closed():
                    opened.close()


class RegionalCourtAdapter(_RegionalAdapter):
    key = "regional_court"
    display_name = "注册地省级人民法院"


class RegionalCcdiAdapter(_RegionalAdapter):
    key = "regional_ccdi"
    display_name = "注册地省级纪委监委"


class RegionalProcuratorateAdapter(_RegionalAdapter):
    key = "regional_procuratorate"
    display_name = "注册地省级人民检察院"


class RegionalSamrAdapter(_RegionalAdapter):
    key = "regional_samr"
    display_name = "注册地省级市场监督管理局"
