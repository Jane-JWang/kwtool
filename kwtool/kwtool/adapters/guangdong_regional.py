"""Search profiles for Guangdong's provincial authorities."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from kwtool.adapters.paging_regional import Pager, result_signature
from kwtool.adapters.search_regional import RegionalSite, _RegionalAdapter


class _GuangdongAdapter(_RegionalAdapter):
    """A single-province router; other registration values fail before browsing."""

    site: RegionalSite
    pager: Pager

    def _result_scope(self, page, province):
        if self.key == "regional_pboc" and parse_qs(urlparse(page.url).query).get("q"):
            category = page.locator('a[appname="广东省分行"]')
            if category.count() and category.is_visible():
                category.click()
        if self.key != "regional_safe":
            return page
        frame = page.frame(name="iframepage")
        if frame is None:
            return None
        parsed = urlparse(frame.url)
        params = parse_qs(parsed.query)
        if parsed.path != "/internet_app/search" or params.get("siteid") != ["guangdong"]:
            return None
        return frame

    def _results_complete(self, scope, province, subject, body):
        if self.key in ("regional_tax", "regional_csrc"):
            count = re.search(r"相关结果\s*(\d+)\s*个", body)
            return count is not None and (int(count[1]) == 0 or bool(result_signature(scope, self.pager)))
        if self.key == "regional_pboc":
            return (scope.locator("#q").input_value() == subject
                    and (bool(result_signature(scope, self.pager)) or "无搜索结果" in body))
        if self.key == "regional_safe":
            parsed = urlparse(scope.url)
            return (parse_qs(parsed.query).get("q") == [subject]
                    and re.search(r"共有\s*\d+\s*条结果", body) is not None)
        if self.key == "regional_nfra":
            return ("无搜索结果" in body or
                    (re.search(r"共检索到\s*[\d,，]+\s*篇文章", body) is not None
                     and bool(result_signature(scope, self.pager))))
        return super()._results_complete(scope, province, subject, body)

    def _query_proven(self, scope, province, subject, proven):
        if self.key == "regional_pboc":
            links = scope.locator(".news-style1:visible h3 a[href]").evaluate_all(
                "es => es.map(e => e.href)")
            return (scope.locator("#q").input_value() == subject
                    and not parse_qs(urlparse(scope.url).query).get("q")
                    and scope.locator('a[appname="广东省分行"].active').count() > 0
                    and all(urlparse(link).hostname == "guangzhou.pbc.gov.cn" for link in links))
        return proven


def _site(name, url, hosts, path, query, *, params=(), inputs=(), buttons=(), direct=True):
    return RegionalSite(name, url, hosts, inputs, buttons, path, (query,),
                        params=params, direct_query=query if direct else "")


class RegionalDevelopmentAdapter(_GuangdongAdapter):
    key = "regional_development"
    display_name = "注册地省级发展改革部门"
    site = _site("广东省发展和改革委员会", "https://search.gd.gov.cn/search/all/135",
                 ("search.gd.gov.cn",), r"/search/(?:all|local)/135", "keywords",
                 params=(("c", "index"), ("a", "local")))
    pager = Pager("#list-body .list-item, .list-body .list-item", "#page-list, .page-list")


class RegionalNaturalResourcesAdapter(_GuangdongAdapter):
    key = "regional_natural_resources"
    display_name = "注册地省级自然资源部门"
    site = _site("广东省自然资源厅", "https://search.gd.gov.cn/search/all/153",
                 ("search.gd.gov.cn",), r"/search/(?:all|local)/153", "keywords")
    pager = RegionalDevelopmentAdapter.pager


class RegionalHousingAdapter(_GuangdongAdapter):
    key = "regional_housing"
    display_name = "注册地省级住房城乡建设部门"
    site = _site("广东省住房和城乡建设厅", "https://search.gd.gov.cn/search/all/233",
                 ("search.gd.gov.cn",), r"/search/(?:all|local)/233", "keywords")
    pager = RegionalDevelopmentAdapter.pager


class RegionalEnvironmentAdapter(_GuangdongAdapter):
    key = "regional_environment"
    display_name = "注册地省级生态环境部门"
    site = _site("广东省生态环境厅", "https://search.gd.gov.cn/search/all/160",
                 ("search.gd.gov.cn",), r"/search/(?:all|local)/160", "keywords",
                 params=(("filterType", "localSite"),))
    pager = RegionalDevelopmentAdapter.pager


class RegionalTaxAdapter(_GuangdongAdapter):
    key = "regional_tax"
    display_name = "注册地省级税务局"
    site = _site("国家税务总局广东省税务局", "https://guangdong.chinatax.gov.cn/search5/html/searchResult.html",
                 ("guangdong.chinatax.gov.cn",), r"/search5/html/searchResult\.html", "searchWord",
                 params=(("siteCode", "bm29190001"),))
    pager = Pager(".wordGuide", ".pagination")


class RegionalCsrcAdapter(_GuangdongAdapter):
    key = "regional_csrc"
    display_name = "注册地省级证监局"
    site = _site("广东证监局", "https://www.csrc.gov.cn/guangdong/",
                 ("www.csrc.gov.cn",), r"/guestweb4/s", "searchWord",
                 inputs=("#form1 #searchWord",), buttons=("#form1 #button",), direct=False)
    pager = Pager(".wordGuide", ".pagination")


class RegionalNfraAdapter(_GuangdongAdapter):
    key = "regional_nfra"
    display_name = "注册地省级金融监管局"
    site = _site("国家金融监督管理总局广东监管局",
                 "https://www.nfra.gov.cn/branch/guangdong/view/pages/index/jiansuo.html",
                 ("www.nfra.gov.cn",), r"/branch/guangdong/view/pages/index/jiansuo\.html", "keyWords")
    pager = Pager(".jiansuo-right-result-list:visible", "[pager2], .pagination")


class RegionalPbocAdapter(_GuangdongAdapter):
    key = "regional_pboc"
    display_name = "注册地省级人民银行分行"
    site = _site("中国人民银行广东省分行", "https://wzdig.pbc.gov.cn/search/pcRender",
                 ("wzdig.pbc.gov.cn",), r"/search/pcRender", "q",
                 params=(("pageId", "c177a85bd02b4114bebebd210809f691"), ("dr", "true")))
    pager = Pager(".news-style1", ".page, .pagination")


class RegionalSafeAdapter(_GuangdongAdapter):
    key = "regional_safe"
    display_name = "注册地省级外汇局分局"
    site = _site("国家外汇管理局广东省分局", "https://www.safe.gov.cn/guangdong/search/index.html",
                 ("www.safe.gov.cn",), r"/guangdong/search/index\.html", "q",
                 params=(("siteid", "guangdong"), ("order", "releasetime")))
    pager = Pager(".result_list li, .search-result li", ".page, .pagination")


GUANGDONG_ADAPTERS = (
    RegionalDevelopmentAdapter, RegionalNaturalResourcesAdapter, RegionalHousingAdapter,
    RegionalEnvironmentAdapter, RegionalTaxAdapter, RegionalCsrcAdapter,
    RegionalNfraAdapter, RegionalPbocAdapter, RegionalSafeAdapter,
)

for _adapter in GUANGDONG_ADAPTERS:
    _adapter.sites = {"广东省": _adapter.site}
    _adapter.pagers = {"广东省": _adapter.pager}
