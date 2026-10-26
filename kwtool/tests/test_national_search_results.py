import io
from pathlib import Path
from threading import Event
from urllib.parse import urlencode

import pytest
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunCancelled, RunContext, SiteUnavailable
from kwtool.adapters.ccdi import CcdiAdapter
from kwtool.adapters.mem import MemAdapter
from kwtool.adapters.ndrc import NdrcAdapter
from kwtool.adapters.national_search import _NationalSearchAdapter
from kwtool.adapters.prosecutor_12309 import Prosecutor12309Adapter, _empty
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
        if self.selector in ("input", "#searchword") and self.page.result_input is not None:
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
        if self.selector == "#searchword" and self.page.result_input is not None:
            self.page.result_input = value
        else:
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
        self.closed = False

    def close(self):
        self.closed = True
        self.browser.pages.remove(self)

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
    (CcdiAdapter(), "https://www.ccdi.gov.cn/was5/web/search", "首页 > 搜索结果", "",
     "searchword", ("#searchword", "form[name=searchFrom] input[type=submit]")),
    (Prosecutor12309Adapter(), "https://www.12309.gov.cn/searchui/?siteCode=zgrmjc#/12309/search",
     "共找到 0 条结果", SUBJECT, None, ("input[name=text]", "#btn1")),
    (StaAdapter(), "https://www.chinatax.gov.cn/search5/html/searchResult.html?" + urlencode({"searchWord": SUBJECT}),
     "相关结果0个", None, None, ("#header-search-txt", "#header-search-btn")),
])
def test_result_page_confirms_search_without_query_in_body(
    adapter, popup_url, body, result_input, submitted_field, controls, tmp_path: Path, monkeypatch,
):
    if isinstance(adapter, Prosecutor12309Adapter):
        monkeypatch.setattr(adapter, "_handle_results", lambda context, page: context.save_pdf(page))
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
    if isinstance(adapter, CcdiAdapter):
        assert popup.result_input == SUBJECT
    assert popup.closed is adapter.site.close_result_tab
    assert browser.listeners == []


@pytest.mark.parametrize("outcome", ["success", "timeout", "blocked", "cancelled", "pdf-error"])
def test_12309_repeated_searches_clean_up_result_tabs(outcome, tmp_path: Path, monkeypatch):
    adapter = Prosecutor12309Adapter()
    browser = Browser(None)
    home = Page(browser, adapter.site.url, controls=("input[name=text]", "#btn1"))
    unrelated = Page(browser, "https://example.com/")
    browser.pages = [home, unrelated]
    context = RunContext(
        browser=browser, page=home, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    printed = []

    def handle_results(run, page):
        assert not page.closed
        if outcome == "pdf-error":
            raise RuntimeError("PDF failed")
        printed.append(page)

    monkeypatch.setattr(adapter, "_handle_results", handle_results)
    if outcome in ("timeout", "cancelled"):
        def wait_until(ready, *args, **kwargs):
            assert not ready()
            if outcome == "cancelled":
                raise RunCancelled()
            raise TimeoutError()
        context.wait_until = wait_until

    for subject in (SUBJECT, "另一家公司", "第三家公司"):
        body = {"timeout": "加载中", "cancelled": "加载中",
                "blocked": "您当前的访问行为存在异常"}.get(outcome, "共找到 0 条结果")
        popup = Page(browser, "https://www.12309.gov.cn/searchui/", body, result_input=subject)
        browser.popup = popup
        if outcome == "success":
            adapter.run_one(context, subject)
            assert printed[-1] is popup
        else:
            error = {"timeout": RuntimeError, "blocked": SiteUnavailable,
                     "cancelled": RunCancelled, "pdf-error": RuntimeError}[outcome]
            with pytest.raises(error):
                adapter.run_one(context, subject)
        assert popup.closed
        assert browser.pages == [home, unrelated]
        assert not home.closed
        assert not unrelated.closed


def test_12309_same_tab_search_keeps_main_page(tmp_path: Path, monkeypatch):
    adapter = Prosecutor12309Adapter()
    browser = Browser(None)
    home = Page(browser, adapter.site.url, controls=("input[name=text]", "#btn1"))
    browser.pages = [home]

    def submit(query):
        home.body = "共找到 0 条结果"
        home.result_input = query

    browser.submit = submit
    context = RunContext(
        browser=browser, page=home, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    printed = []
    monkeypatch.setattr(adapter, "_handle_results", lambda run, page: printed.append(page))
    adapter.run_one(context, SUBJECT)
    assert printed == [home]
    assert browser.pages == [home]
    assert not home.closed


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


def test_12309_combines_available_pages(tmp_path: Path):
    class ResultLocator:
        def __init__(self, page, selector):
            self.page, self.selector = page, selector

        def all(self):
            return [self] if self.selector == ".text-left-list" else []

        def count(self):
            return 1

        def inner_text(self):
            if self.selector == ".text-left-list":
                return f"第 {self.page.number} 页文章"
            if self.selector == ".el-pager li.number.active":
                return str(self.page.number)
            return "共 20 条"

        def is_disabled(self):
            return self.page.number == 2

        def click(self):
            self.page.number += 1

    class ResultPage:
        number = 1

        def locator(self, selector):
            return ResultLocator(self, selector)

        def evaluate(self, script):
            pass

        def emulate_media(self, **kwargs):
            pass

    page = ResultPage()
    adapter = Prosecutor12309Adapter()
    adapter._max_pages = 3
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={"max_pages": 3},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )

    def render(current):
        writer = PdfWriter()
        writer.add_blank_page(width=100 + current.number, height=100)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    context.render_pdf = render
    adapter._handle_results(context, page)
    assert len(context.artifacts) == 1
    assert [float(item.mediabox.width) for item in PdfReader(context.artifacts[0]).pages] == [101, 102]


def test_mem_restores_search_box_before_print(monkeypatch, tmp_path: Path):
    class SearchBox:
        value = ""

        def is_visible(self):
            return True

        def fill(self, value):
            self.value = value

        def input_value(self):
            return self.value

    box = SearchBox()
    page = type("Page", (), {"locator": lambda self, selector: box})()
    adapter = MemAdapter()
    adapter._subject = SUBJECT
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    printed = []
    monkeypatch.setattr(_NationalSearchAdapter, "_handle_results",
                        lambda self, run, current: printed.append(box.value))
    adapter._handle_results(context, page)
    assert printed == [SUBJECT]


def test_12309_empty_page_detected_from_pagination_total():
    class Total:
        def count(self):
            return 1

        def inner_text(self):
            return "共 0 条"

    class Cards:
        def all(self):
            return []

    page = type("Page", (), {"locator": lambda self, selector: Total()
                if selector == ".pagination-container .el-pagination__total" else Cards()})()
    assert _empty(page)
