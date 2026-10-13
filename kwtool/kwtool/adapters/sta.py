"""Adapter for StaAdapter."""
from __future__ import annotations
from kwtool.adapters.national_search import SearchSite, _NationalSearchAdapter


class StaAdapter(_NationalSearchAdapter):
    key = "sta"
    display_name = "国家税务总局"
    site = SearchSite("https://www.chinatax.gov.cn/chinatax/index.html", ("chinatax.gov.cn",),
                      ("#header-search-txt",), ("#header-search-btn",),
                      query_key="searchWord", close_result_tab=True)

