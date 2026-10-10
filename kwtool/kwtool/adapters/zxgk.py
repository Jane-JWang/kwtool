"""China Enforcement Information Disclosure searches with a human CAPTCHA step."""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import Error as PlaywrightError, Page

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings


HOST = "zxgk.court.gov.cn"
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

    def run_one(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        target = f"https://{HOST}{self.path}"
        initial_status = None
        if page.url.split("?", 1)[0] != target:
            try:
                response = page.goto(target, wait_until="domcontentloaded", timeout=60000)
            except PlaywrightError as exc:
                raise SiteUnavailable(f"无法打开{self.display_name}：{exc}") from exc
            initial_status = response.status if response else None
            if initial_status and initial_status >= 400 and initial_status != 412:
                raise SiteUnavailable(f"{self.display_name}返回 HTTP {initial_status}")
            if initial_status == 412:
                context.report_status("网站首次访问返回 HTTP 412，正在重试查询页面…")
                page.wait_for_timeout(1500)
                context.check_stop()
                try:
                    retry = page.goto(target, wait_until="domcontentloaded", timeout=60000)
                except PlaywrightError:
                    retry = None  # The site may navigate itself during the challenge.
                if retry and retry.status >= 400 and retry.status != 412:
                    raise SiteUnavailable(f"{self.display_name}重试返回 HTTP {retry.status}")

        def search_ready() -> bool:
            try:
                return (urlparse(page.url).hostname == HOST
                        and page.get_by_text("查询", exact=True).count() > 0)
            except PlaywrightError:
                return False  # The 412 challenge can navigate while we inspect the page.

        try:
            context.wait_until(search_ready, "等待查询页面加载…", timeout_seconds=60)
        except TimeoutError as exc:
            detail = f"HTTP {initial_status} 后" if initial_status else ""
            raise SiteUnavailable(f"{self.display_name}{detail}未加载查询表单，请检查 Chrome 页面") from exc
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
            context.wait_until(
                lambda: bool(results),
                "请在 Chrome 中填写验证码并完成查询…", timeout_seconds=600, user_action=True,
            )
            if results[-1].status >= 400:
                raise ValueError(f"查询返回 HTTP {results[-1].status}，请检查页面")
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
