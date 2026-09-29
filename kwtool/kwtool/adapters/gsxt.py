"""National Enterprise Credit Information Publicity System adapter.

The site requires the user to complete its own login and CAPTCHA challenges in
the visible Chrome window. This adapter never reads or solves those challenges.
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import FieldSpec, PdfSettings


HOME = "https://www.gsxt.gov.cn/"
SEARCH_INPUT = "#keyword, input[placeholder*='企业名称']"
SEARCH_BUTTON = "#btn_query, button:has-text('查询')"
RESULT_LINK = "a.search_list_item"
DETAIL_ITEM = ".result-menu__sub-btn"
SUCCESS_TEXT = "报告稍后发送，请注意查收"


def _site_page(page: Page) -> bool:
    host = urlparse(page.url).hostname or ""
    return host == "gsxt.gov.cn" or host.endswith(".gsxt.gov.cn")


def _visible(page: Page, selector: str) -> bool:
    return page.locator(selector).first.is_visible()


def _body_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=5000)


def _login_required(page: Page) -> bool:
    """Recognize an explicit sign-in entry; the search box alone is public."""
    try:
        if "login" in urlparse(page.url).path.lower() or "登录" in page.title():
            return True
        if page.get_by_text("退出登录", exact=True).first.is_visible():
            return False
        return any(link.is_visible() for link in page.locator("a").filter(has_text="登录").all()
                   if link.inner_text().strip() in {"登录", "实名注册/登录"})
    except PlaywrightError:
        return False


def _detail_page(context: RunContext, search_page: Page, prior_pages: set[Page]) -> Page | None:
    for page in reversed(context.browser.pages):
        if (page is search_page or page not in prior_pages) and not page.is_closed() and _site_page(page) and page.locator(DETAIL_ITEM).count():
            return page
    return None


class GsxtAdapter(SiteAdapter):
    key = "gsxt"
    display_name = "企信网"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.55, print_background=True)
    settings_schema = (FieldSpec("column_delay_ms", "栏目加载间隔（毫秒）", "integer", 350),)

    def _search_page(self, context: RunContext) -> Page:
        page = context.page
        status = None
        if not _site_page(page):
            try:
                response = page.goto(HOME, wait_until="commit", timeout=60000)
                status = response.status if response else None
            except PlaywrightError as exc:
                raise SiteUnavailable(f"无法打开企信网：{exc}") from exc
        context.report_status("等待企信网完成环境检查；如需登录，请在 Chrome 中操作…")
        deadline = time.monotonic() + 180
        last_login_notice = False
        while True:
            context.check_stop()
            try:
                ready = _site_page(page) and _visible(page, SEARCH_INPUT)
                login_needed = _login_required(page)
                if ready and not login_needed:
                    context.report_user_action(False)
                    return page
                if login_needed and not last_login_notice:
                    context.report_status("企信网尚未登录，请在 Chrome 中登录；完成后将自动继续…")
                    context.report_user_action(True)
                    last_login_notice = True
                    deadline = time.monotonic() + 600
            except PlaywrightError:
                pass  # The environment check may still be navigating.
            if time.monotonic() >= deadline:
                if status and status >= 400:
                    raise SiteUnavailable(f"企信网返回 HTTP {status}，等待环境检查后仍未加载")
                raise TimeoutError("等待企信网搜索框或登录完成超时，请检查 Chrome 中的页面")
            time.sleep(0.3)

    def _find_company(self, context: RunContext, subject: str) -> Page:
        page = self._search_page(context)
        prior_pages = set(context.browser.pages)
        page.locator(SEARCH_INPUT).first.fill(subject)
        context.report_status(f"正在搜索：{subject}；如出现验证码，请在 Chrome 中完成")
        page.locator(SEARCH_BUTTON).first.click()

        def results_ready() -> bool:
            if _visible(page, RESULT_LINK):
                return True
            if _detail_page(context, page, prior_pages):
                return True
            text = _body_text(page)
            return any(marker in text for marker in ("暂无查询结果", "未查询到相关企业", "没有找到相关企业"))

        context.wait_until(results_ready, "请完成搜索验证码；等待搜索结果…", timeout_seconds=600, user_action=True)
        detail = _detail_page(context, page, prior_pages)
        if detail:
            return detail
        links = page.locator(RESULT_LINK)
        candidates = []
        for index in range(links.count()):
            link = links.nth(index)
            name = link.locator("h1").first.inner_text().strip() if link.locator("h1").count() else link.inner_text().strip()
            candidates.append((name, link.get_attribute("href")))
        if not candidates:
            raise ValueError(f"未找到企业：{subject}")
        exact = [item for item in candidates if item[0] == subject]
        if len(exact) == 1:
            index = candidates.index(exact[0])
            links.nth(index).click()
        else:
            names = "、".join(name for name, _ in candidates[:8])
            context.report_status(f"{subject} 没有唯一完全匹配项，请在右侧网页选择企业。候选：{names}")
        context.wait_until(
            lambda: _detail_page(context, page, prior_pages) is not None,
            "等待你在右侧网页选择企业…",
            timeout_seconds=600,
            user_action=True,
        )
        detail = _detail_page(context, page, prior_pages)
        assert detail is not None
        return detail

    def _load_columns(self, context: RunContext, page: Page) -> None:
        toggle = page.locator('.result-menu__toggle[aria-expanded="false"]').first
        if toggle.is_visible():
            toggle.click()
        items = page.locator(DETAIL_ITEM)
        count = items.count()
        if count == 0:
            raise ValueError("详情页未找到左侧栏目")
        delay = max(150, min(1500, int(context.site_settings.get("column_delay_ms", 350))))
        for index in range(count):
            context.check_stop()
            items.nth(index).click()
            context.report_status(f"正在加载详情栏目 {index + 1}/{count}")
            time.sleep(delay / 1000)
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
        context.wait_until(
            lambda: not _visible(page, ".loading:visible, .el-loading-mask:visible"),
            "等待企业详情加载完毕…",
            timeout_seconds=30,
        )
        time.sleep(1.5)

    def _send_report(self, context: RunContext, page: Page) -> None:
        context.check_stop()
        success_dialogs: list[str] = []

        def handle_dialog(dialog) -> None:
            success_dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", handle_dialog)
        try:
            menu = page.locator("#moreActionsToggle").first
            if menu.is_visible():
                menu.click()
            start = page.locator("#btn_send_pdf").first
            if not start.is_visible():
                start = page.get_by_text("发送报告", exact=True).first
            if not start.is_visible():
                raise ValueError("详情页未找到‘发送报告’")
            start.click()
            context.wait_until(
                lambda: _visible(page, "#send_pdf_dialog") or SUCCESS_TEXT in _body_text(page),
                "请在 Chrome 中完成发送报告验证码…",
                timeout_seconds=600,
                user_action=True,
            )
            if SUCCESS_TEXT not in _body_text(page):
                send = page.locator("#send_pdf_dialog").get_by_text("发送", exact=True).first
                if not send.is_visible():
                    raise ValueError("发送报告弹窗未找到‘发送’按钮")
                send.click()

                def confirmed() -> bool:
                    if SUCCESS_TEXT in _body_text(page) or any(SUCCESS_TEXT in text for text in success_dialogs):
                        return True
                    for selector in ("[role='dialog']", ".layui-layer-dialog", ".el-message-box", ".modal:visible"):
                        for dialog in page.locator(selector).all():
                            if not dialog.is_visible():
                                continue
                            text = dialog.inner_text()
                            if "确认" in text or "确定" in text:
                                button = dialog.get_by_text("确认", exact=True).or_(dialog.get_by_text("确定", exact=True)).first
                                if button.is_visible():
                                    button.click()
                                    return False
                    return False

                context.wait_until(confirmed, "等待网站确认邮件报告已发送…", timeout_seconds=30)
            context.report_status("网站已确认发送邮件报告")
        finally:
            page.remove_listener("dialog", handle_dialog)

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = self._find_company(context, subject)
        context.page = page
        self._load_columns(context, page)
        saved = context.save_pdf(page, margin={"top": "10.16mm", "right": "6.35mm", "bottom": "10.16mm", "left": "6.35mm"})
        context.report_status(f"已保存 PDF：{saved}；正在发送邮件报告")
        self._send_report(context, page)
