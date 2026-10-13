"""Adapter for Prosecutor12309Adapter."""
from __future__ import annotations
import io
import re

from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter
from kwtool.models import FieldSpec
from kwtool.output import save_pdf_bytes


def _cards(page) -> tuple[str, ...]:
    return tuple(text for card in page.locator(".text-left-list").all()
                 if (text := card.inner_text().strip()))


def _empty(page) -> bool:
    total = page.locator(".pagination-container .el-pagination__total")
    return (total.count() > 0
            and re.search(r"共\s*0\s*条", total.inner_text()) is not None
            and not _cards(page))


class Prosecutor12309Adapter(_NationalSearchAdapter):
    key = "prosecutor_12309"
    display_name = "12309中国检察网"
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 1, minimum=1, maximum=1000),)
    site = SearchSite("https://www.12309.gov.cn/", ("12309.gov.cn",),
                      ("input[name=text]",), ("#btn1",), result_markers=("共找到",),
                      result_inputs=("input",))

    def run_one(self, context: RunContext, subject: str) -> None:
        try:
            self._max_pages = int(context.site_settings.get("max_pages", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("12309 前 K 页必须是整数") from exc
        if not 1 <= self._max_pages <= 1000:
            raise ValueError("12309 前 K 页必须在 1 到 1000 之间")
        super().run_one(context, subject)

    def _handle_results(self, context: RunContext, page) -> None:
        pieces: list[bytes] = []
        previous: tuple[str, ...] = ()
        for number in range(1, self._max_pages + 1):
            context.check_stop()
            try:
                context.wait_until(lambda: bool(_cards(page)) or _empty(page),
                                   f"等待 12309 第 {number} 页文章列表", timeout_seconds=30)
            except TimeoutError as exc:
                context.capture_diagnostic(f"12309-page-{number}-timeout", page)
                raise RuntimeError(f"12309 第 {number} 页文章列表未加载，未保存 PDF") from exc
            if _empty(page) and not _cards(page):
                # The header's zero count appears before the asynchronous list.
                page.wait_for_timeout(2500)
            cards = _cards(page)
            if number > 1 and not cards:
                raise RuntimeError(f"12309 第 {number} 页没有加载出文章列表")
            if cards and cards == previous:
                raise RuntimeError(f"12309 第 {number} 页内容未变化，未保存重复页面")
            previous = cards
            page.evaluate("document.fonts.ready")
            page.emulate_media(media="screen")
            page.evaluate("window.scrollTo(0, 0)")
            context.report_status(f"正在打印 12309 第 {number} 页")
            pieces.append(context.render_pdf(page))
            if number == self._max_pages or not cards:
                break
            next_button = page.locator(".pagination-container button.btn-next")
            if next_button.count() == 0 or next_button.is_disabled():
                break
            next_button.click()
            context.wait_until(
                lambda: (page.locator(".el-pager li.number.active").inner_text().strip() == str(number + 1)
                         and bool(_cards(page)) and _cards(page) != previous),
                f"等待 12309 第 {number + 1} 页完成翻页", timeout_seconds=60,
            )

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
        context.report_status(f"已保存 12309 前 {len(pieces)} 页检索结果：{saved}")

