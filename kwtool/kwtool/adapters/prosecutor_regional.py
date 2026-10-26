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
            ("sh.jcy.gov.cn",), ("#searchText1",), ("input[onclick*='searchAll']",),
            r"/shjcysearch\.jspx", ("q",)),
    }
    pagers = {
        '广东省': Pager(".js_basic_result_left .item", ".listPager"),
        '浙江省': Pager("a[href*='/art/']", ".digg"),
        '北京市': Pager("#searchList li", "#kkpager"),
        # This template has neither a count nor a message for empty results.
        # Locate article blocks by their native metadata and pagination by its
        # record summary, rather than assuming a list CSS class.
        '上海市': Pager(
            "xpath=//*[contains(.,'栏目名称') and contains(.,'更新日期') "
            "and not(*[contains(.,'栏目名称') and contains(.,'更新日期')])]/parent::*",
            "xpath=//*[text()[contains(.,'条记录')]]/ancestor-or-self::*[descendant::a][1]",
            numbered_only=True),
    }

    def _results_complete(self, scope, province, subject, body):
        ready = super()._results_complete(scope, province, subject, body)
        if province != "上海市" or ready:
            return ready
        # Confirm the complete native result document, including the echoed
        # query and footer. A blank page/loading shell alone is insufficient.
        compact = "".join(body.split()).replace(":", "：")
        if ("搜索结果" not in compact
                or "当前查询条件：" + "".join(subject.split()) not in compact
                or "上海市人民检察院版权所有" not in compact):
            return False
        return (scope.evaluate("document.readyState") == "complete"
                and subject in scope.locator("input").evaluate_all("els => els.map(e => e.value)"))

    def _before_print(self, page, province, subject):
        if province == "广东省":
            # The search service replaces the homepage form and clears its
            # field after every navigation. Restore it without resubmitting.
            search = page.locator(".js_search_input")
            search.fill(subject)
            if search.input_value() != subject:
                raise RuntimeError("广东省人民检察院搜索栏未能恢复查询内容，未保存 PDF")
