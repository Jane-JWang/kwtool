"""Adapter for WenshuAdapter."""
from __future__ import annotations
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter


class WenshuAdapter(_NationalSearchAdapter):
    key = "wenshu"
    display_name = "中国裁判文书网"
    site = SearchSite("https://wenshu.court.gov.cn/", ("court.gov.cn",),
                      ("input[placeholder*='案由、关键词']",),
                      (".search-rightBtn.search-click",), query_key="s21")

