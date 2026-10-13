"""Adapter for MemAdapter."""
from __future__ import annotations
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter


class MemAdapter(_NationalSearchAdapter):
    key = "mem"
    display_name = "应急管理部"
    site = SearchSite("https://www.mem.gov.cn/was5/web/sousuo/index.html?stype=0", ("mem.gov.cn",),
                      ("#key",), ("#query_btn",),
                      result_markers=("网页结果共", "搜索结果", "暂无结果"), query_key="sw")

