"""Search court judgments after interactive login."""
from __future__ import annotations
import re
from datetime import date

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter
from kwtool.models import FieldSpec


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


class WenshuAdapter(_NationalSearchAdapter):
    key = "wenshu"
    display_name = "中国裁判文书网"
    optional_kwargs = ("身份证",)
    settings_schema = (
        FieldSpec("bribery_investigation", "受贿调查", "bool", False),
        FieldSpec("include_birth_date", "加入出生日期筛选", "bool", False),
    )
    site = SearchSite("https://wenshu.court.gov.cn/", ("court.gov.cn",),
                      ("input[placeholder*='案由、关键词']",),
                      (".search-rightBtn.search-click",),
                      result_markers=("共检索到",), query_key="s21")

    def run_one(self, context: RunContext, subject: str) -> None:
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

