"""Search court judgments after interactive login."""
from __future__ import annotations
import io
import re
from datetime import date

from playwright.sync_api import Error as PlaywrightError
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter
from kwtool.models import FieldSpec
from kwtool.output import save_pdf_bytes


def _birth_date(identity: str) -> str:
    number = identity.strip().upper()
    if re.fullmatch(r"\d{17}[\dX]", number):
        year, month, day = int(number[6:10]), int(number[10:12]), int(number[12:14])
    elif re.fullmatch(r"\d{15}", number):
        year, month, day = 1900 + int(number[6:8]), int(number[8:10]), int(number[10:12])
    else:
        raise ValueError("身份证应为 18 位或 15 位号码，无法提取出生日期")
    try:
        birthday = date(year, month, day)
    except ValueError as exc:
        raise ValueError("身份证中的出生日期无效") from exc
    return f"{birthday.year}年{birthday.month}月{birthday.day}日"


def _logged_in(page) -> bool:
    if not page.url.startswith("https://wenshu.court.gov.cn/"):
        return False
    if not page.locator("input[placeholder*='案由、关键词']").is_visible():
        return False
    # The site's anonymous header exposes a 登录 link.  Do not search while it
    # is still visible, even if the search box itself is available.
    links = page.locator("a, button").filter(has_text=re.compile(r"^\s*登录\s*$"))
    return not any(links.nth(index).is_visible() for index in range(links.count()))


def _result_signature(page) -> str:
    body = page.locator("body").inner_text(timeout=5000)
    if "共检索到" not in body or any(word in body for word in ("请输入验证码", "请完成验证", "请先登录")):
        return ""
    # The first result is near the count.  Ignore the pagination footer so a
    # changed active page number alone cannot count as fresh search results.
    return body.split("共检索到", 1)[1][:1500].strip()


def _next_page_button(page):
    selectors = (
        "a[title='下一页'], button[title='下一页'], li[title='下一页']",
        "a:has-text('下一页'), button:has-text('下一页')",
        "a:has-text('下页'), button:has-text('下页')",
        "li:has-text('下一页'), li:has-text('下页')",
        ".pagination .next, .page .next",
    )
    for selector in selectors:
        candidates = page.locator(selector)
        for index in range(min(candidates.count(), 10)):
            candidate = candidates.nth(index)
            if not candidate.is_visible():
                continue
            disabled = candidate.evaluate("""el => {
              const parent = el.closest('li, button');
              return [el, parent].filter(Boolean).some(node =>
                node.hasAttribute('disabled') || node.getAttribute('aria-disabled') === 'true' ||
                /(^|\\s)(disabled|layui-disabled|is-disabled)(\\s|$)/.test(node.className || ''));
            }""")
            if disabled:
                return None
            return candidate
    return None


class WenshuAdapter(_NationalSearchAdapter):
    key = "wenshu"
    display_name = "中国裁判文书网"
    optional_kwargs = ("身份证",)
    settings_schema = (
        FieldSpec("bribery_investigation", "受贿调查", "bool", False),
        FieldSpec("include_birth_date", "加入出生日期筛选", "bool", False),
        FieldSpec("max_pages", "前 K 页", "integer", 1, minimum=1, maximum=1000),
    )
    site = SearchSite("https://wenshu.court.gov.cn/", ("court.gov.cn",),
                      ("input[placeholder*='案由、关键词']",),
                      (".search-rightBtn.search-click",),
                      result_markers=("共检索到",), query_key="s21")

    def run_one(self, context: RunContext, subject: str) -> None:
        try:
            max_pages = int(context.site_settings.get("max_pages", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("裁判文书网前 K 页必须是整数") from exc
        if not 1 <= max_pages <= 1000:
            raise ValueError("裁判文书网前 K 页必须在 1 到 1000 之间")
        self._max_pages = max_pages
        identity = context.subject_kwargs.get("身份证", "")
        terms = [subject]
        if context.site_settings.get("bribery_investigation", False):
            terms.append("贿")
        if context.site_settings.get("include_birth_date", False) and identity:
            terms.append(_birth_date(identity))

        context.report_status("请先在右侧 Chrome 登录中国裁判文书网")
        try:
            context.page.goto(self.site.url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise RuntimeError(f"无法打开中国裁判文书网：{exc}") from exc
        context.wait_until(
            lambda: _logged_in(context.page),
            "请先在右侧 Chrome 登录中国裁判文书网，登录完成后将自动开始检索…",
            timeout_seconds=600,
            user_action=True,
        )
        super().run_one(context, " ".join(terms))

    def _handle_results(self, context: RunContext, page) -> None:
        pieces: list[bytes] = []
        for page_number in range(1, self._max_pages + 1):
            context.check_stop()
            signature = _result_signature(page)
            if not signature:
                raise RuntimeError(f"裁判文书网第 {page_number} 页的检索结果尚未加载")
            context.report_status(f"正在打印裁判文书网检索结果第 {page_number} 页")
            page.evaluate("document.fonts.ready")
            page.emulate_media(media="screen")
            page.evaluate("window.scrollTo(0, 0)")
            context.capture_diagnostic(f"wenshu-page-{page_number}-ready-to-print", page)
            pieces.append(context.render_pdf(page))
            if page_number == self._max_pages:
                break
            next_button = _next_page_button(page)
            if next_button is None:
                break
            next_button.click()
            context.wait_until(
                lambda: bool(_result_signature(page)) and _result_signature(page) != signature,
                f"等待裁判文书网第 {page_number + 1} 页检索结果；如出现验证，请在浏览器中完成",
                timeout_seconds=180, user_action=True,
            )

        context.check_stop()
        if len(pieces) == 1:
            combined = pieces[0]
        else:
            writer = PdfWriter()
            for piece in pieces:
                writer.append(PdfReader(io.BytesIO(piece)))
            output = io.BytesIO()
            writer.write(output)
            combined = output.getvalue()
        saved = save_pdf_bytes(combined, context.pdf_path)
        context.pdf_path = saved
        context.artifacts.append(saved)
        context.report_status(f"已保存裁判文书网前 {len(pieces)} 页检索结果：{saved}")

