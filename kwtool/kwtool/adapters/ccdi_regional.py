"""Provincial profiles for regional_ccdi."""
from kwtool.adapters.search_regional import RegionalSite, _RegionalAdapter
from kwtool.adapters.paging_regional import Pager
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class RegionalCcdiAdapter(_RegionalAdapter):
    key = "regional_ccdi"
    display_name = "注册地省级纪委监委"
    sites = {
        "广东省": RegionalSite("广东省纪委监委（南粤清风网）", "https://www.gdjct.gd.gov.cn/",
            ("www.gdjct.gd.gov.cn", "gddqsearch.southcn.com"), ("#txtKey",),
            ("#searchForm button[type=submit]",), r"/search/(?:all|local)/204001", ("keywords",)),
        "浙江省": RegionalSite("浙江省纪委监委", "https://www.zjsjw.gov.cn/",
            ("www.zjsjw.gov.cn", "i.tmuyun.com"), ("#anyWord",), ("#searchForm button",),
            r"/allsearch", ("keyword",)),
        "北京市": RegionalSite("北京市纪委监委", "https://www.bjsupervision.gov.cn/",
            ("www.bjsupervision.gov.cn",), ("#textfield",), ("form[name=searchform] input[type=submit]",),
            r"/was5/web/search", ("searchword",)),
        "上海市": RegionalSite("上海市纪委监委", "https://www.shjjjc.gov.cn/",
            ("www.shjjjc.gov.cn",), ("#query",), ("#searchForm a.btn-main",),
            r"/TrueCMS/searchController/getResult\.do", ("query",)),
    }
    pagers = {
        '广东省': Pager("#list-body .list-item", "#page-list"),
        '浙江省': Pager(".news-item", "#page"),
        '北京市': Pager(".news_list li", ".page"),
        '上海市': Pager(".search-list li", ".pagelist"),
    }

    def _prepare_search(self, context, page, frame, province):
        if province == "广东省":
            frame.locator("#sxt").select_option("content")

    def _before_print(self, page, province, subject):
        if province == "北京市":
            # Each result navigation reloads the header iframe with an empty
            # search box. Restore it on every page without submitting again.
            search = page.frame_locator("iframe[src*='index_314.html']").locator("input[name=searchword]")
            search.fill(subject)
            if search.input_value() != subject:
                raise RuntimeError("北京市纪委监委搜索栏未能恢复查询内容，未保存 PDF")

    def _turn_page(self, context, page, scope, province, number, signature):
        if super()._turn_page(context, page, scope, province, number, signature):
            return True
        if province != "上海市":
            return False
        # The official template sometimes fails to build its .pagelist. Its
        # embedded pagination script specifies page= and server-rendered totals.
        html = page.content()
        total = re.search(r"var totalCount\s*=\s*['\"](\d+)['\"]", html)
        size = re.search(r"var pageSize\s*=\s*['\"](\d+)['\"]", html)
        if not total or not size or (number - 1) * int(size[1]) >= int(total[1]):
            return False
        if not signature:
            raise RuntimeError("上海纪委监委未能识别文章列表，未保存不完整 PDF")
        parsed = urlsplit(page.url)
        params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "page"]
        params.append(("page", str(number)))
        page.goto(urlunsplit(parsed._replace(query=urlencode(params))), wait_until="domcontentloaded", timeout=60000)
        return True
