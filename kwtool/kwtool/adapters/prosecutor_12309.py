"""Adapter for Prosecutor12309Adapter."""
from __future__ import annotations
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter


class Prosecutor12309Adapter(_NationalSearchAdapter):
    key = "prosecutor_12309"
    display_name = "12309中国检察网"
    site = SearchSite("https://www.12309.gov.cn/", ("12309.gov.cn",),
                      ("input[name=text]",), ("#btn1",), result_markers=("共找到",),
                      result_inputs=("input",))

