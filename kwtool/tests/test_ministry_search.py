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
                 result_count: int = 0):
        self.url = ""
        self.body = body
        self.value = input_value
        self.status = status
        self.result_count = result_count

    def goto(self, url, **kwargs):
        self.url = url
        return type("Response", (), {"status": self.status})()

    def locator(self, selector):
        if selector == "body":
            return type("Body", (), {"inner_text": lambda self, **kwargs: self.page.body,
                                      "page": self})()
        if selector == "#q":
            return type("Input", (), {"input_value": lambda self: self.page.value,
                                       "page": self})()
        if selector == ".bszn_list .comprehensiveItem":
            return type("Results", (), {"count": lambda self: self.page.result_count,
                                         "page": self})()
        raise AssertionError(selector)

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


@pytest.mark.parametrize("adapter,body,result_count", [
    (MnrAdapter(), "相关结果约0条", 0),
    (MohurdAdapter(), "查询范围：默认\n摘要中的公司名称被拆分", 2),
    (SafeAdapter(), "搜索结果\n测试公司", 0),
])
def test_direct_ministry_search_prints_confirmed_results(adapter, body, result_count, tmp_path):
    page = Page(body, result_count=result_count)
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


@pytest.mark.parametrize("adapter,body", [
    (MnrAdapter(), "自然资源部首页"),
    (MohurdAdapter(), "查询范围：默认\n热门搜索"),
    (SafeAdapter(), "全文检索\n返回首页"),
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
