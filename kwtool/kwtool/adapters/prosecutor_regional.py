"""Provincial profiles for regional_procuratorate."""
from kwtool.adapters.search_regional import RegionalSite, _RegionalAdapter
from kwtool.adapters.paging_regional import Pager


class RegionalProcuratorateAdapter(_RegionalAdapter):
    key = "regional_procuratorate"
    display_name = "注册地省级人民检察院"
    sites = {
        "广东省": RegionalSite("广东省人民检察院", "http://www.gd.jcy.gov.cn/",
            ("www.gd.jcy.gov.cn", "jcysearch.jcrb.com"), ("input[name=searchWord]",),
            ("form[name=searchform] input[type=submit]",), r"/search/search\.shtml", ("searchWord",)),
        "浙江省": RegionalSite("浙江省人民检察院", "https://www.zjjcy.gov.cn/",
            ("www.zjjcy.gov.cn",), ("#hotwords",), ("form[name=sitesearch] input[type=submit]",),
            r"/(?:jcms/jcms_files/jcms1/web1/site/)?module/sitesearch/index\.jsp", ("keyvalue",)),
        "北京市": RegionalSite("北京市人民检察院", "https://www.bjjc.gov.cn/",
            ("www.bjjc.gov.cn",), ("#keyword",), (".fd-top-search span[onclick]",),
            r"/search\.htm", ("keyword",)),
        "上海市": RegionalSite("上海市人民检察院", "https://www.sh.jcy.gov.cn/",
            ("www.sh.jcy.gov.cn",), ("#searchText1",), ("input[onclick*='searchAll']",),
            r"/shjcysearch\.jspx", ("q",)),
    }
    pagers = {
        '广东省': Pager(".js_basic_result_left .item", ".listPager"),
        '浙江省': Pager("a[href*='/art/']", ".digg"),
        '北京市': Pager("#searchList li", "#kkpager"),
        '上海市': Pager(".searchList li, .search-list li, .search_list li, .searchlist li", ".pagination, .page, .pages, .pager"),
    }
