"""AMAC's public mutual-fund manager directory."""

from __future__ import annotations

from kwtool.adapters.base import RunContext, SiteAdapter
from kwtool.adapters.securities_search import _max_pages, _open, _print, _save_pieces
from kwtool.models import FieldSpec, PdfSettings


ROSTER_URL = "https://www.amac.org.cn/fwdt/wyc/jgcprycx/jgcx/gmjjglrml/"


class AmacRosterAdapter(SiteAdapter):
    key = "amac_roster"
    display_name = "中国证券投资基金业协会【名录】"
    default_pdf_settings = PdfSettings(scale=0.35, print_background=True)
    settings_schema = (FieldSpec("max_pages", "前 K 页", "integer", 1),)

    def run_one(self, context: RunContext, subject: str) -> None:
        maximum = _max_pages(context)
        _open(context, ROSTER_URL, "www.amac.org.cn", "基金业协会公募基金管理人名录")
        page = context.page
        context.wait_until(lambda: page.locator("#tbody tr").count() > 0,
                           "等待基金业协会名录加载", timeout_seconds=60)
        page.locator("#keyWordGS").fill(subject)
        with page.expect_response(lambda response: "/findMutualFundHousePage" in response.url,
                                  timeout=60000):
            page.locator("#searchBtn").click()
        context.wait_until(lambda: (page.locator("#tbody tr").count() > 0
                                    and subject in page.locator("#tbody").inner_text())
                           or page.locator("#noData:visible").count() > 0,
                           "等待基金业协会名录查询结果", timeout_seconds=60)
        pieces: list[bytes] = []
        for number in range(1, maximum + 1):
            context.check_stop()
            context.report_status(f"正在打印基金业协会名录第 {number} 页")
            pieces.append(_print(context, page, f"amac-roster-{number}"))
            next_button = page.locator("#pageToolbar .js-page-next:not(.ui-pager-disabled) a").first
            if number == maximum or page.locator("#noData:visible").count() or not next_button.count():
                break
            before = page.locator("#tbody").inner_text()
            next_button.click()
            context.wait_until(lambda: page.locator("#tbody").inner_text() != before,
                               "等待基金业协会名录翻页", timeout_seconds=60)
        _save_pieces(context, pieces, "基金业协会名录")
