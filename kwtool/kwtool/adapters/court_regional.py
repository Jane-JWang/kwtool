"""Provincial profiles for regional_court."""
from kwtool.adapters.search_regional import RegionalSite, _RegionalAdapter
from kwtool.adapters.paging_regional import Pager, next_control
from kwtool.adapters.national_search import _first_visible


class RegionalCourtAdapter(_RegionalAdapter):
    key = "regional_court"
    display_name = "注册地省级人民法院"
    sites = {
        "广东省": RegionalSite("广东省高级人民法院", "https://search.gdcourts.gov.cn/search/local/208001",
            ("gdcourts.gov.cn",), ("#input-keywords",), ("#search-button",),
            r"/search/local/208001", ("keywords",), direct_query="keywords"),
        "浙江省": RegionalSite("浙江省高级人民法院", "https://zjsfgkw.gov.cn/",
            ("zjsfgkw.gov.cn",), ("#search-form input[name=q]",), ("#search-form button",),
            r"/jrobot/search\.do", ("q",)),
        "北京市": RegionalSite("北京市高级人民法院", "https://bjgy.bjcourt.gov.cn/",
            ("bjgy.bjcourt.gov.cn",), ("#keyword",), ("#article_content input[type=submit]",),
            r"/article/essearch\.shtml", ("keyword",)),
        "上海市": RegionalSite("上海市高级人民法院", "https://www.hshfy.sh.cn/shfy/web/search_zh.jsp",
            ("www.hshfy.sh.cn",), ("input[name=nr]",), ("button[onclick*='do_search']",),
            r"/shfy/web/search_zh\.jsp", ("nr",), encoding="gbk"),
    }
    pagers = {
        '广东省': Pager("#list-body .list-item", "#page-list"),
        '浙江省': Pager("#jsearch-result-items .jsearch-result-box", "#pagination"),
        '北京市': Pager(".search_content > ul", ".paginationControl"),
        '上海市': Pager("a.esty_a[href*='xxnr.jsp']", "form[name=pageForm]"),
    }

    def _prepare_search(self, context, page, frame, province):
        if province != "上海市":
            return
        page.evaluate("""() => {
            for (const name of ['fbrqks', 'fbrqjs']) mini.getbyName(name).setValue('');
        }""")
        context.wait_until(lambda: page.frame(name="search") is not None
            and page.frame(name="search").locator("form[name=pageForm]").count() > 0,
            "等待上海高院内嵌检索页加载", timeout_seconds=60)

    def _submit_search(self, context, page, frame, search, button, province):
        if province == "上海市":
            # The legacy button has a second, default form submission. Invoke
            # its native handler once so only the iframe's POST is submitted.
            page.evaluate("do_search()")
        else:
            super()._submit_search(context, page, frame, search, button, province)

    def _result_scope(self, page, province):
        return page.frame(name="search") if province == "上海市" else page

    def _query_proven(self, scope, province, subject, proven):
        if province == "上海市":
            field = scope.locator("input[name=nr]")
            return field.count() > 0 and field.input_value() == subject
        return proven

    def _before_print(self, page, province, subject):
        if province == "北京市":
            # This POST response clears the visible query field.
            search = _first_visible(page, ("input[name=keyword]",))
            if search is not None:
                search.fill(subject)

    def _turn_page(self, context, page, scope, province, number, signature):
        if province != "北京市":
            return super()._turn_page(context, page, scope, province, number, signature)
        if next_control(scope, self.pagers[province], number) is None:
            return False
        if not signature:
            raise RuntimeError("北京法院未能识别文章列表，未保存不完整 PDF")
        # The site's page-link handler creates a POST containing only page=,
        # dropping keyword=. Include the original subject in that same POST.
        subject = page.locator("input[name=keyword]").input_value()
        page.evaluate("""({number, subject}) => {
            const form = document.createElement('form');
            form.method = 'post'; form.action = location.pathname;
            for (const [name, value] of Object.entries({page: number, keyword: subject})) {
                const input = document.createElement('input');
                input.type = 'hidden'; input.name = name; input.value = value;
                form.appendChild(input);
            }
            document.body.appendChild(form); form.submit();
        }""", {"number": number, "subject": subject})
        return True
