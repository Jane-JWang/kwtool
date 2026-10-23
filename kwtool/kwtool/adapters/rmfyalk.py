"""People's Court Case Library search after interactive sign-in."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


HOME_URL = "https://rmfyalk.court.gov.cn/home.html"
SEARCH_PATH = "/cpws_al_api/api/cpwsAl/search"


def _on_site(page) -> bool:
    return urlparse(page.url).hostname == "rmfyalk.court.gov.cn"


def _logged_in(page) -> bool:
    if not _on_site(page):
        return False
    try:
        name = page.locator("#alk-header .user .user-name")
        return name.is_visible() and bool(name.inner_text().strip())
    except PlaywrightError:
        return False


def _results_ready(page) -> bool:
    if not _on_site(page) or urlparse(page.url).path != "/view/list.html":
        return False
    try:
        items = page.locator(".al-list > li")
        return items.count() > 0 and "list-loading" not in (items.first.get_attribute("class") or "")
    except PlaywrightError:
        return False


def _is_subject_response(response, subject: str) -> bool:
    request = response.request
    if urlparse(response.url).path != SEARCH_PATH or request.method != "POST":
        return False
    payload = request.post_data_json
    if not isinstance(payload, dict):
        return False
    terms = payload.get("searchParams", {}).get("keyTitle", [])
    return subject in terms if isinstance(terms, list) else subject == terms


class RmfyalkAdapter(SiteAdapter):
    key = "rmfyalk"
    display_name = "人民法院案例库"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status("请先在右侧 Chrome 登录人民法院案例库")
        try:
            response = page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开人民法院案例库：{exc}") from exc
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"人民法院案例库返回 HTTP {response.status}")
        if not _on_site(page):
            raise SiteUnavailable(f"人民法院案例库跳转至非预期网站：{page.url}")

        context.wait_until(
            lambda: _logged_in(page),
            "请先在右侧 Chrome 登录人民法院案例库；登录完成后将自动检索…",
            timeout_seconds=600,
            user_action=True,
        )
        context.check_stop()
        search = page.locator("#alk-search .general-search-text .keyword")
        search.fill(subject)
        search_response = None

        def on_response(result) -> None:
            nonlocal search_response
            if _is_subject_response(result, subject):
                search_response = result

        context.browser.on("response", on_response)
        result_page = None
        try:
            with page.expect_popup(timeout=15000) as popup:
                page.locator("#alk-search .general-search-submit").click()
            result_page = popup.value
            context.wait_until(
                lambda: _on_site(result_page) and
                urlparse(result_page.url).path == "/view/list.html",
                "等待人民法院案例库结果页…",
                timeout_seconds=30,
            )
            context.wait_until(
                lambda: search_response is not None,
                "等待人民法院案例库检索响应…",
                timeout_seconds=90,
            )
            if search_response.status >= 400:
                raise RuntimeError(f"人民法院案例库检索返回 HTTP {search_response.status}，未保存 PDF")
            payload = search_response.json()
            if str(payload.get("code")) != "0":
                raise RuntimeError("人民法院案例库检索未成功，请确认登录状态；未保存 PDF")
            context.wait_until(
                lambda: _results_ready(result_page),
                "等待人民法院案例库结果列表…",
                timeout_seconds=30,
            )
            if result_page.locator(".al-list > .list-not-login").count():
                raise RuntimeError("人民法院案例库登录已失效，未保存 PDF")
            result_page.evaluate("document.fonts.ready")
            result_page.emulate_media(media="screen")
            result_page.evaluate("window.scrollTo(0, 0)")
            context.capture_diagnostic("rmfyalk-ready-to-print", result_page)
            saved = context.save_pdf(result_page)
            context.report_status(f"已保存人民法院案例库检索结果：{saved}")
        finally:
            context.browser.remove_listener("response", on_response)
            if result_page is not None and not result_page.is_closed():
                result_page.close()
