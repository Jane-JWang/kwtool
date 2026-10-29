"""Province-level official website searches selected by subject registration."""
from __future__ import annotations

from dataclasses import dataclass
import io
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.adapters.national_search import _allowed, _first_visible
from kwtool.adapters.paging_regional import Pager, next_control, result_signature
from kwtool.models import FieldSpec, PdfSettings
from kwtool.output import save_pdf_bytes


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
    empty_markers: tuple[str, ...] = ()

    def start_url(self, subject: str) -> str:
        if not self.direct_query:
            return self.url
        return self.url + "?" + urlencode((*self.params, (self.direct_query, subject)), encoding=self.encoding)


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
    sites: dict[str, RegionalSite]
    pagers: dict[str, Pager]
    required_kwargs = ("注册地",)
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 3, minimum=1, maximum=1000),)
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)

    def _prepare_search(self, context, page, frame, province):
        pass

    def _submit_search(self, context, page, frame, search, button, province):
        if button is not None:
            button.click()
        else:
            search.press("Enter")

    def _result_scope(self, page, province):
        return page

    def _query_proven(self, scope, province, subject, proven):
        return proven

    def _results_complete(self, scope, province, subject, body):
        site = self.sites[province]
        return bool(RESULT_COUNT.search(body)
                    or any(marker in body for marker in site.empty_markers)
                    or result_signature(scope, self.pagers[province]))

    def _before_print(self, page, province, subject):
        pass

    def _turn_page(self, context, page, scope, province, number, signature):
        control = next_control(scope, self.pagers[province], number)
        if control is None:
            return False
        if not signature:
            raise RuntimeError("无法识别文章列表，未保存不完整 PDF")
        control.click()
        return True

    def run_one(self, context: RunContext, subject: str) -> None:
        raw_pages = context.site_settings.get("max_pages", 3)
        if isinstance(raw_pages, bool) or not re.fullmatch(r"[0-9]+", str(raw_pages)) or not 1 <= int(raw_pages) <= 1000:
            raise ValueError("前 K 页必须是 1 到 1000 之间的整数")
        max_pages = int(raw_pages)
        province = resolve_province(context.subject_kwargs.get("注册地", ""))
        if province not in self.sites:
            raise ValueError(f"{self.display_name}目前仅支持：{'、'.join(self.sites)}")
        site = self.sites[province]
        pager = self.pagers[province]
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
                self._prepare_search(context, page, frame, province)
                search.fill(subject)
                button = _first_visible(frame, site.buttons)
                self._submit_search(context, page, frame, search, button, province)

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
                        scope = self._result_scope(candidate, province)
                        if scope is None:
                            continue
                        body = scope.locator("body").inner_text(timeout=2000)
                    except PlaywrightError:
                        # Popups may be between navigation and document creation.
                        continue
                    if any(message in body for message in BLOCKED):
                        raise SiteUnavailable(f"{site.name}拦截了检索请求，请在浏览器中检查站点提示")
                    if any(message in body for message in CHALLENGES):
                        continue
                    proven = (query_matches(candidate.url, site.query_keys, subject, site.encoding)
                              or candidate.url.split("?", 1)[0] in submitted_urls)
                    proven = self._query_proven(scope, province, subject, proven)
                    if proven and self._results_complete(scope, province, subject, body):
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
            pieces: list[bytes] = []
            seen: set[tuple[str, ...]] = set()
            for number in range(1, max_pages + 1):
                context.check_stop()
                scope = self._result_scope(page, province)
                signature = result_signature(scope, pager)
                if number > 1 and (not signature or signature in seen):
                    raise RuntimeError(f"{site.name}第 {number} 页结果重复或未加载，未保存 PDF")
                seen.add(signature)
                self._before_print(page, province, subject)
                page.evaluate("document.fonts.ready")
                page.emulate_media(media="screen")
                page.evaluate("window.scrollTo(0, 0)")
                context.capture_diagnostic(f"ready-to-print-{number}", page)
                pieces.append(context.render_pdf(page))
                context.report_status(f"已打印{site.name}第 {number} 页")
                if number == max_pages:
                    break
                stable_results.clear()
                if not self._turn_page(context, page, scope, province, number + 1, signature):
                    break

                def advanced() -> bool:
                    if not results_ready():
                        return False
                    current = self._result_scope(page, province)
                    updated = result_signature(current, pager)
                    return bool(updated) and updated != signature

                context.wait_until(advanced, f"等待{site.name}第 {number + 1} 页结果加载完成",
                                   timeout_seconds=180, user_action=True)

            context.check_stop()
            if len(pieces) == 1:
                data = pieces[0]
            else:
                writer = PdfWriter()
                for piece in pieces:
                    writer.append(PdfReader(io.BytesIO(piece)))
                output = io.BytesIO()
                writer.write(output)
                data = output.getvalue()
            saved = save_pdf_bytes(data, context.pdf_path)
            context.pdf_path = saved
            context.artifacts.append(saved)
            context.report_status(f"已保存{site.name}前 {len(pieces)} 页检索结果：{saved}")
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
