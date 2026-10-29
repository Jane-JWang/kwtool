"""China Securities Regulatory Commission public search results."""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode, urlparse

from playwright.sync_api import Error as PlaywrightError

from kwtool.adapters.base import RunContext, SiteAdapter, SiteUnavailable
from kwtool.models import PdfSettings
from kwtool.models import FieldSpec
from kwtool.output import safe_filename
from kwtool.adapters.csrc_filing_lists import (
    ACCOUNTING_TITLE, RATING_TITLE, CsrcAccountingFilingAdapter, CsrcRatingFilingAdapter,
)


SEARCH_URL = "https://www.csrc.gov.cn/guestweb4/s"
SITE_CODE = "bm56000001"
RESULT_DATE = re.compile(r"20\d{2}[-年./]\d{1,2}[-月./]\d{1,2}")


def search_url(subject: str) -> str:
    # Match the URL produced by the site's own search and "按时间" controls.
    # Its first page is zero-indexed and its Chinese values are encoded twice.
    word = quote(subject, safe="")
    params = [
        ("searchWord", word), ("column", quote("全部", safe="")),
        ("pageSize", "10"), ("pageNum", "0"), ("siteCode", SITE_CODE),
        ("sonSiteCode", ""), ("checkHandle", "1"), ("searchSource", "0"),
        ("govWorkBean", quote("{}", safe="")), ("areaSearchFlag", "-1"),
        ("secondSearchWords", ""), ("topical", ""), ("docName", ""),
        ("label", ""), ("countKey", "0"), ("uc", "0"),
        ("left_right_index", "0"), ("searchBoxSettingsIndex", ""),
        ("manualWord", word), ("orderBy", "1"), ("startTime", ""),
        ("endTime", ""), ("timeStamp", "0"), ("strFileType", ""),
        ("wordPlace", "0"),
    ]
    return f"{SEARCH_URL}?{urlencode(params)}"


class CsrcAdapter(SiteAdapter):
    key = "csrc"
    display_name = "中国证监会"
    default_pdf_settings = PdfSettings(landscape=False, scale=0.35, print_background=True)
    settings_schema = (
        FieldSpec("search", "搜索结果", "bool", True),
        FieldSpec("rating_filing", RATING_TITLE, "bool", False),
        FieldSpec("accounting_filing", ACCOUNTING_TITLE, "bool", False),
    )

    def run_one(self, context: RunContext, subject: str) -> None:
        selected = [
            (spec.key, adapter) for spec, adapter in (
                (self.settings_schema[1], CsrcRatingFilingAdapter()),
                (self.settings_schema[2], CsrcAccountingFilingAdapter()),
            ) if context.site_settings.get(spec.key, spec.default)
        ]
        if not context.site_settings.get("search", True) and not selected:
            raise ValueError("请至少勾选一个中国证监会查询项")
        original_path = context.pdf_path
        if context.site_settings.get("search", True):
            context.pdf_path = original_path.with_name(f"{original_path.stem}_搜索结果.pdf") if selected else original_path
            self._search(context, subject)
        for _, adapter in selected:
            context.check_stop()
            context.pdf_path = original_path.with_name(
                f"{original_path.stem}_{safe_filename(adapter.display_name)}.pdf"
            )
            adapter.run_one(context, subject)

    def _search(self, context: RunContext, subject: str) -> None:
        context.check_stop()
        page = context.page
        context.report_status(f"正在搜索中国证监会：{subject}（按时间倒序，第 1 页）")
        try:
            response = page.goto(search_url(subject), wait_until="domcontentloaded", timeout=60000)
        except PlaywrightError as exc:
            raise SiteUnavailable(f"无法打开中国证监会搜索页：{exc}") from exc
        host = urlparse(page.url).hostname or ""
        if host != "csrc.gov.cn" and not host.endswith(".csrc.gov.cn"):
            raise SiteUnavailable(f"中国证监会搜索页跳转到了其他网站：{page.url}")
        if response is not None and response.status >= 400:
            raise SiteUnavailable(f"中国证监会搜索页返回 HTTP {response.status}")

        def results_ready() -> bool:
            if not page.locator("body").is_visible():
                return False
            body = page.locator("body").inner_text(timeout=5000)
            return (bool(RESULT_DATE.search(body)) and "相关结果" in body) or any(
                marker in body for marker in ("暂无结果", "未找到相关", "没有找到相关")
            )

        context.wait_until(
            results_ready,
            "等待中国证监会搜索结果加载完成…",
            timeout_seconds=60,
        )
        page.evaluate("document.fonts.ready")
        page.emulate_media(media="screen")
        page.evaluate("window.scrollTo(0, 0)")
        saved = context.save_pdf(page)
        context.report_status(f"已保存中国证监会搜索结果第 1 页：{saved}")
