import io
from pathlib import Path
from threading import Event
from urllib.parse import urlencode

import pytest
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext
from kwtool.adapters.nfra import NfraAdapter, SEARCH_URL


class Results:
    def __init__(self, page):
        self.page = page

    def inner_text(self, **_kwargs):
        return self.page.result_text

    def locator(self, _selector):
        return self

    def count(self):
        return self.page.visible_items

    def all_inner_texts(self):
        return [f"第 {self.page.number} 页结果"] if self.page.visible_items else []

    def get_by_text(self, _text, **_kwargs):
        return self

    def is_visible(self):
        return self.page.empty_visible


class Page:
    def __init__(self, subject):
        self.subject = subject
        self.url = ""
        self.result_text = "共检索到 {{datatotal}} 篇文章 当前第 {{pager.currentPageIndex}}页"
        self.visible_items = 0
        self.empty_visible = False
        self.number = 1

    def goto(self, url, **_kwargs):
        self.url = url
        return type("Response", (), {"status": 200})()

    def locator(self, selector):
        if selector == "#keywords":
            return type("Input", (), {"input_value": lambda _self: self.subject})()
        if selector.startswith("[pager2]"):
            return type("Next", (), {
                "count": lambda _self: 1,
                "click": lambda _self: self.next_page(),
            })()
        return Results(self)

    def next_page(self):
        self.number += 1
        self.result_text = f"共检索到 20 篇文章 当前第 {self.number}页"

    def evaluate(self, _script):
        pass

    def emulate_media(self, **_kwargs):
        pass


@pytest.mark.parametrize("count", [0, 5])
def test_nfra_waits_for_complete_results_before_printing(tmp_path: Path, count: int):
    subject = "测试公司"
    page = Page(subject)
    adapter = NfraAdapter()
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda _message: None, artifacts=[],
    )
    printed = []
    context.save_pdf = lambda current: printed.append(current) or context.pdf_path

    def wait_until(ready, *_args, **_kwargs):
        assert not ready()  # Angular template has not resolved yet.
        page.result_text = (
            f"共检索到 {count} 篇文章 当前第 1页" if count else "无搜索结果"
        )
        assert not ready()  # Count alone does not prove that results rendered.
        if count:
            page.visible_items = 5
        else:
            page.empty_visible = True
        assert ready()

    context.wait_until = wait_until
    adapter.run_one(context, subject)
    assert page.url == SEARCH_URL + "?" + urlencode({"keyWords": subject})
    assert printed == [page]
    assert adapter.default_pdf_settings.scale == 0.35


def test_nfra_combines_first_two_pages(tmp_path: Path):
    subject = "测试公司"
    page = Page(subject)
    page.result_text = "共检索到 20 篇文章 当前第 1页"
    page.visible_items = 10
    adapter = NfraAdapter()
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=adapter.default_pdf_settings,
        site_settings={"max_pages": 2}, stop_event=Event(),
        report_status=lambda _message: None, artifacts=[],
    )
    context.wait_until = lambda ready, *_args, **_kwargs: (ready() or pytest.fail("结果未加载"))

    def render_pdf(_current):
        writer = PdfWriter()
        writer.add_blank_page(width=100 + page.number, height=100)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    context.render_pdf = render_pdf
    adapter.run_one(context, subject)
    assert page.number == 2
    assert [float(item.mediabox.width) for item in PdfReader(context.artifacts[0]).pages] == [101, 102]
