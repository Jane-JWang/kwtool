"""Adapter for MemAdapter."""
from __future__ import annotations
from kwtool.adapters.base import RunContext
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter


class MemAdapter(_NationalSearchAdapter):
    key = "mem"
    display_name = "应急管理部"
    site = SearchSite("https://www.mem.gov.cn/was5/web/sousuo/index.html?stype=0", ("mem.gov.cn",),
                      ("#key",), ("#query_btn",),
                      result_markers=("网页结果共", "搜索结果", "暂无结果"), query_key="sw")

    def run_one(self, context: RunContext, subject: str) -> None:
        self._subject = subject
        super().run_one(context, subject)

    def _handle_results(self, context: RunContext, page) -> None:
        context.wait_until(lambda: page.locator("#key").is_visible(),
                           "等待应急管理部结果页检索框", timeout_seconds=15)
        search = page.locator("#key")
        search.fill(self._subject)
        if search.input_value() != self._subject:
            raise RuntimeError("应急管理部结果页未能回填检索词，未保存 PDF")
        super()._handle_results(context, page)

