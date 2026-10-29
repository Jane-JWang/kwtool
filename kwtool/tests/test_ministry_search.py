from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext, SiteUnavailable
from kwtool.adapters.ministry_search import MnrAdapter, MohurdAdapter, NhcAdapter, SafeAdapter


SUBJECT = "测试公司"


class Page:
    def __init__(self, body: str, input_value: str = SUBJECT, status: int = 200,
                 result_count: int = 0, no_results_visible: bool = False,
                 frame_body: str = "", frame_query: str = SUBJECT):
        self.url = ""
        self.body = body
        self.value = input_value
        self.status = status
        self.result_count = result_count
        self.no_results_visible = no_results_visible
        self.frame_body = frame_body
        self.frame_query = frame_query

    def goto(self, url, **kwargs):
        self.url = url
        return type("Response", (), {"status": self.status})()

    def locator(self, selector):
        if selector == "body":
            return type("Body", (), {"inner_text": lambda self, **kwargs: self.page.body,
                                      "page": self})()
        if selector in ("#q", "#allSearchWord"):
            return type("Input", (), {"input_value": lambda self: self.page.value,
                                       "page": self})()
        if selector == ".bszn_list .comprehensiveItem":
            return type("Results", (), {"count": lambda self: self.page.result_count,
                                         "page": self})()
        if selector == ".no_results":
            return type("NoResults", (), {"is_visible": lambda self: self.page.no_results_visible,
                                           "page": self})()
        raise AssertionError(selector)

    def frame(self, name):
        assert name == "iframepage"
        if not self.frame_body:
            return None
        page = self

        class Frame:
            url = "https://www.safe.gov.cn/internet_app/search?siteid=safe&q=" + page.frame_query

            def locator(self, selector):
                if selector == "body":
                    return type("Body", (), {"inner_text": lambda self, **kwargs: page.frame_body})()
                if selector == "#q_search":
                    return type("Input", (), {"input_value": lambda self: page.frame_query})()
                raise AssertionError(selector)

        return Frame()

    def evaluate(self, script):
        pass

    def emulate_media(self, **kwargs):
        pass


def context_for(page: Page, tmp_path: Path) -> RunContext:
    context = RunContext(None, page, tmp_path / "result.pdf", MnrAdapter.default_pdf_settings,
                         {}, Event(), lambda _: None, [])
    context.save_pdf = lambda current: context.artifacts.append(context.pdf_path) or context.pdf_path
    context.wait_until = lambda ready, *args, **kwargs: ready() or (_ for _ in ()).throw(TimeoutError())
    return context


@pytest.mark.parametrize("adapter,body,result_count,frame_body", [
    (MnrAdapter(), "相关结果约0条", 0, ""),
    (MohurdAdapter(), "查询范围：默认\n摘要中的公司名称被拆分", 2, ""),
    (NhcAdapter(), "相关结果0 个\n很抱歉，未找到相关的内容", 0, ""),
    (SafeAdapter(), "全文检索", 0, "搜索：测试公司\n共有 0 条结果"),
])
def test_direct_ministry_search_prints_confirmed_results(adapter, body, result_count, frame_body, tmp_path):
    page = Page(body, result_count=result_count, frame_body=frame_body)
    context = context_for(page, tmp_path)
    adapter.run_one(context, SUBJECT)
    assert parse_qs(urlparse(page.url).query)[adapter.query_key] == [SUBJECT]
    assert context.artifacts == [context.pdf_path]
    assert adapter.default_pdf_settings.scale == 0.35


def test_mohurd_waits_for_results_after_query_box_updates(tmp_path):
    adapter = MohurdAdapter()
    page = Page("查询范围：默认\n热门搜索")
    context = context_for(page, tmp_path)
    with pytest.raises(RuntimeError, match="未能确认检索结果"):
        adapter.run_one(context, SUBJECT)
    assert not context.artifacts


def test_mohurd_prints_confirmed_empty_result_page(tmp_path):
    adapter = MohurdAdapter()
    page = Page("查询范围：默认\n热门搜索", no_results_visible=True)
    context = context_for(page, tmp_path)
    adapter.run_one(context, SUBJECT)
    assert context.artifacts == [context.pdf_path]


def test_safe_waits_for_current_query_in_result_frame(tmp_path):
    adapter = SafeAdapter()
    page = Page("全文检索", frame_body="共有 2 条结果", frame_query="上一家公司")
    context = context_for(page, tmp_path)
    with pytest.raises(RuntimeError, match="未能确认检索结果"):
        adapter.run_one(context, SUBJECT)
    assert not context.artifacts


@pytest.mark.parametrize("adapter,body", [
    (MnrAdapter(), "自然资源部首页"),
    (MohurdAdapter(), "查询范围：默认\n热门搜索"),
    (NhcAdapter(), "国家卫生健康委员会首页"),
    (SafeAdapter(), "全文检索\n返回首页",),
])
def test_direct_ministry_search_rejects_unconfirmed_results(adapter, body, tmp_path):
    context = context_for(Page(body, input_value="托尔斯泰"), tmp_path)
    with pytest.raises(RuntimeError, match="未能确认检索结果"):
        adapter.run_one(context, SUBJECT)
    assert not context.artifacts


def test_ministry_sites_are_registered_and_http_errors_do_not_print(tmp_path):
    sites = registered_adapters()
    for key in ("mnr", "mohurd", "nhc", "safe"):
        assert key in sites
    assert isinstance(sites["nhc"], NhcAdapter)
    context = context_for(Page("", status=412), tmp_path)
    with pytest.raises(SiteUnavailable, match="HTTP 412"):
        sites["safe"].run_one(context, SUBJECT)
    assert not context.artifacts
