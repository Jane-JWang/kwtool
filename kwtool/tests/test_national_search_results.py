from pathlib import Path
from threading import Event
from urllib.parse import urlencode

import pytest

from kwtool.adapters.base import RunContext, SiteUnavailable
from kwtool.adapters.ccdi import CcdiAdapter
from kwtool.adapters.ndrc import NdrcAdapter
from kwtool.adapters.prosecutor_12309 import Prosecutor12309Adapter
from kwtool.adapters.sta import StaAdapter


SUBJECT = "测试公司"


class Locator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def count(self):
        return len(self.all())

    def all(self):
        if self.selector == "body":
            return [self]
        if self.selector in self.page.controls:
            return [self]
        if self.selector == "input" and self.page.result_input is not None:
            return [self]
        return []

    def nth(self, index):
        assert index == 0
        return self

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def fill(self, value):
        self.page.query = value

    def input_value(self):
        return self.page.result_input

    def click(self):
        self.page.browser.submit(self.page.query)

    def inner_text(self, **kwargs):
        return self.page.body


class Page:
    def __init__(self, browser, url, body="", controls=(), result_input=None):
        self.browser = browser
        self.url = url
        self.body = body
        self.controls = set(controls)
        self.result_input = result_input
        self.query = ""

    def goto(self, url, **kwargs):
        self.url = url
        return type("Response", (), {"status": 200})()

    def locator(self, selector):
        return Locator(self, selector)

    def evaluate(self, script):
        return None

    def emulate_media(self, **kwargs):
        pass


class Browser:
    def __init__(self, popup, submitted_field=None):
        self.popup = popup
        self.submitted_field = submitted_field
        self.pages = []
        self.listeners = []

    def on(self, event, listener):
        assert event == "request"
        self.listeners.append(listener)

    def remove_listener(self, event, listener):
        self.listeners.remove(listener)

    def submit(self, query):
        if self.submitted_field:
            request = type("Request", (), {
                "url": self.popup.url, "method": "POST",
                "post_data": urlencode({self.submitted_field: query}),
            })()
            for listener in self.listeners:
                listener(request)
        self.pages.append(self.popup)


@pytest.mark.parametrize("adapter,popup_url,body,result_input,submitted_field,controls", [
    (CcdiAdapter(), "https://www.ccdi.gov.cn/was5/web/search", "首页 > 搜索结果", None,
     "searchword", ("#searchword", "form[name=searchFrom] input[type=submit]")),
    (Prosecutor12309Adapter(), "https://www.12309.gov.cn/searchui/?siteCode=zgrmjc#/12309/search",
     "共找到 0 条结果", SUBJECT, None, ("input[name=text]", "#btn1")),
    (StaAdapter(), "https://www.chinatax.gov.cn/search5/html/searchResult.html?" + urlencode({"searchWord": SUBJECT}),
     "相关结果0个", None, None, ("#header-search-txt", "#header-search-btn")),
])
def test_result_page_confirms_search_without_query_in_body(
    adapter, popup_url, body, result_input, submitted_field, controls, tmp_path: Path,
):
    popup = Page(None, popup_url, body, result_input=result_input)
    browser = Browser(popup, submitted_field)
    popup.browser = browser
    home = Page(browser, adapter.site.url, controls=controls)
    browser.pages = [home]
    context = RunContext(
        browser=browser, page=home, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    saved_pages = []
    context.save_pdf = lambda page: saved_pages.append(page) or context.pdf_path
    adapter.run_one(context, SUBJECT)
    assert saved_pages == [popup]
    assert browser.listeners == []


def test_ndrc_recognizes_its_empty_result_message(tmp_path: Path):
    adapter = NdrcAdapter()
    page = Page(None, "", "很抱歉！未能搜到您想要的内容")
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    saved_pages = []
    context.save_pdf = lambda current: saved_pages.append(current) or context.pdf_path
    adapter.run_one(context, SUBJECT)
    assert saved_pages == [page]


def test_blocked_result_page_fails_without_printing(tmp_path: Path):
    adapter = CcdiAdapter()
    popup = Page(None, "https://www.ccdi.gov.cn/was5/web/search", "您当前的访问行为存在异常")
    browser = Browser(popup, "searchword")
    popup.browser = browser
    home = Page(browser, adapter.site.url,
                controls=("#searchword", "form[name=searchFrom] input[type=submit]"))
    browser.pages = [home]
    context = RunContext(
        browser=browser, page=home, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    with pytest.raises(SiteUnavailable, match="拦截了检索请求"):
        adapter.run_one(context, SUBJECT)
    assert browser.listeners == []
    assert not context.artifacts
