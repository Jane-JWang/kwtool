"""China Enforcement Information Disclosure searches with a human CAPTCHA step."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, Page

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


HOST = "zxgk.court.gov.cn"
HOME = f"https://{HOST}/"
FIELD_SCRIPT = """({terms, value}) => {
  const inputs = [...document.querySelectorAll('input:not([type=hidden])')];
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const matches = inputs.filter(el => visible(el) && terms.some(term => {
    const label = el.labels ? [...el.labels].map(x => x.textContent).join(' ') : '';
    const near = el.parentElement?.parentElement?.textContent?.slice(0, 120) || '';
    return [el.placeholder, el.name, el.id, label, near].some(x => x?.includes(term));
  }));
  if (matches.length !== 1) return matches.length;
  const native = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  native.call(matches[0], value);
  matches[0].dispatchEvent(new Event('input', {bubbles: true}));
  matches[0].dispatchEvent(new Event('change', {bubbles: true}));
  return -1;
}"""


def _fill(page: Page, terms: list[str], value: str) -> None:
    count = page.evaluate(FIELD_SCRIPT, {"terms": terms, "value": value})
    if count != -1:
        raise ValueError(f"无法唯一定位查询字段（{terms[0]}，匹配 {count} 个），请检查网站页面")


class _CourtAdapter(SiteAdapter):
    optional_kwargs = ("身份证",)
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)
    path: str

    def _search_ready(self, page: Page) -> bool:
        try:
            return (urlparse(page.url).hostname == HOST
                    and urlparse(page.url).path == self.path
                    and page.get_by_text("查询", exact=True).last.is_visible())
        except PlaywrightError:
            return False  # The challenge can navigate while the page is inspected.

    def _open_search_page(self, context: RunContext) -> None:
        page = context.page
        target = f"https://{HOST}{self.path}"
        if self._search_ready(page):
            return

        def navigate(url: str) -> int | None:
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightError as exc:
                raise SiteUnavailable(f"无法打开{self.display_name}：{exc}") from exc
            status = response.status if response else None
            if status is not None and status >= 400 and status != 412:
                raise SiteUnavailable(f"{self.display_name}返回 HTTP {status}")
            return status

        status = navigate(target)
        if status == 412:
            context.report_status("网站返回 HTTP 412，等待浏览器完成访问校验…")
            try:
                context.wait_until(lambda: self._search_ready(page), "等待查询页面加载…", timeout_seconds=20)
                return
            except TimeoutError:
                context.check_stop()
                context.report_status("访问校验尚未完成，正在从网站首页重新进入查询页…")
                navigate(HOME)
                page.wait_for_timeout(1500)
                context.check_stop()
                navigate(target)

        try:
            context.wait_until(lambda: self._search_ready(page), "等待查询页面加载…", timeout_seconds=60)
        except TimeoutError as exc:
            raise SiteUnavailable(f"{self.display_name}未加载查询表单，请检查 Chrome 页面") from exc

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        self._open_search_page(context)
        _fill(page, ["姓名/名称", "姓名", "名称"], subject)
        _fill(page, ["身份证", "证件号码", "组织机构代码"], context.subject_kwargs.get("身份证", ""))
        results = []

        def on_response(response) -> None:
            parsed = urlparse(response.url)
            path = parsed.path.lower()
            if parsed.hostname == HOST and ("search" in path or "query" in path):
                results.append(response)

        page.on("response", on_response)
        try:
            context.report_status(f"正在查询{self.display_name}：{subject}；请在 Chrome 中填写验证码")
            page.get_by_text("查询", exact=True).last.click()
            try:
                context.wait_until(
                    lambda: any(response.status != 412 for response in results),
                    "请在 Chrome 中填写验证码并完成查询…", timeout_seconds=600, user_action=True,
                )
            except TimeoutError as exc:
                if results and results[-1].status == 412:
                    raise SiteUnavailable("查询持续返回 HTTP 412，请检查 Chrome 中的网站访问校验") from exc
                raise
            final_response = next(response for response in reversed(results) if response.status != 412)
            if final_response.status >= 400:
                raise ValueError(f"查询返回 HTTP {final_response.status}，请检查页面")
            page.wait_for_timeout(1000)
        finally:
            page.remove_listener("response", on_response)
        context.check_stop()
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        context.capture_diagnostic("ready-to-print", page)
        saved = context.save_pdf(page)
        context.report_status(f"已保存{self.display_name}查询结果：{saved}")


class ShixinAdapter(_CourtAdapter):
    key = "zxgk_shixin"
    display_name = "中国执行信息公开网-失信被执行人"
    path = "/gkw/html/shixin/index.html"


class ZhixingAdapter(_CourtAdapter):
    key = "zxgk_zhixing"
    display_name = "中国执行信息公开网-被执行人信息"
    path = "/gkw/html/zhixing/index.html"
