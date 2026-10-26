"""Provincial profiles for regional_samr."""
from kwtool.adapters.search_regional import RegionalSite, _RegionalAdapter
from kwtool.adapters.paging_regional import Pager


class RegionalSamrAdapter(_RegionalAdapter):
    key = "regional_samr"
    display_name = "注册地省级市场监督管理局"
    sites = {
        "广东省": RegionalSite("广东省市场监督管理局", "https://search.gd.gov.cn/search/local_msg/144",
            ("search.gd.gov.cn",), ("input[name=keywords]",), ("button[type=submit]",),
            r"/search/(?:local_msg|all)/144", ("keywords",), direct_query="keywords"),
        "浙江省": RegionalSite("浙江省市场监督管理局", "https://zjamr.zj.gov.cn/",
            ("zjamr.zj.gov.cn", "search.zj.gov.cn"), ("#allkeyword",),
            ("input[onclick*='zjamrDoSearch']",), r"/api-gateway/jpaas-jsearch-web-server/search", ("q",)),
        "北京市": RegionalSite("北京市市场监督管理局", "https://scjgj.beijing.gov.cn/",
            ("scjgj.beijing.gov.cn", "www.beijing.gov.cn"), ("#qt",), ("#form1 input[type=submit]",),
            r"/so/s", ("qt",), empty_markers=("未能搜到您想要的内容",)),
        "上海市": RegionalSite("上海市市场监督管理局", "https://scjgj.sh.gov.cn/",
            ("scjgj.sh.gov.cn",), ("#searchword",), ("input.search_submit",),
            r"/kbym/", ("query",)),
    }
    pagers = {
        '广东省': Pager("#list-body .list-item, .list-body .list-item", "#page-list, .page-list"),
        '浙江省': Pager(".search-results-list__item", "[data-module=pagination]"),
        # .M-box11 belongs to the similar-information panel; .M-box pages
        # the actual search results at the bottom of the main list.
        # Its last-page "next" link may remain enabled until clicked. Rely on
        # the numbered links, which reflect the actual available result pages.
        '北京市': Pager(".search-result-header", ".M-box", numbered_only=True),
        '上海市': Pager(".maya-result-item", ".pagination"),
    }
