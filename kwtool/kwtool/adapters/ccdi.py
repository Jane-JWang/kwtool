"""Adapter for CcdiAdapter."""
from __future__ import annotations
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter


class CcdiAdapter(_NationalSearchAdapter):
    key = "ccdi"
    display_name = "中央纪委国家监委"
    site = SearchSite("https://www.ccdi.gov.cn/", ("ccdi.gov.cn",),
                      ("#searchword",), ("form[name=searchFrom] input[type=submit]",))

