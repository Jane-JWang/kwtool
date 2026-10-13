import io
from pathlib import Path
from threading import Event

import pytest
from pypdf import PdfReader, PdfWriter

from kwtool.adapters.base import RunContext
from kwtool.adapters.wenshu import WenshuAdapter, _birth_date
from kwtool.adapters.national_search import _NationalSearchAdapter


def test_birth_date_and_settings():
    adapter = WenshuAdapter()
    assert _birth_date("350100198005060012") == "1980年5月6日"
    assert _birth_date("350100800506001") == "1980年5月6日"
    assert adapter.optional_kwargs == ("身份证",)
    assert adapter.site.result_markers == ("共检索到",)
    assert [field.label for field in adapter.settings_schema] == ["受贿调查", "加入出生日期筛选", "前 K 页"]
    with pytest.raises(ValueError, match="出生日期无效"):
        _birth_date("350100198013060012")


def test_wait_for_login_before_combined_search(monkeypatch, tmp_path: Path):
    searched = []
    monkeypatch.setattr("kwtool.adapters.wenshu._logged_in", lambda page: True)
    monkeypatch.setattr(_NationalSearchAdapter, "run_one", lambda self, context, query: searched.append(query))

    class Page:
        def goto(self, url, **kwargs):
            assert url == "https://wenshu.court.gov.cn/"

    messages = []
    context = RunContext(
        browser=None, page=Page(), pdf_path=tmp_path / "result.pdf",
        pdf_settings=WenshuAdapter.default_pdf_settings,
        site_settings={"bribery_investigation": True, "include_birth_date": True},
        stop_event=Event(), report_status=messages.append, artifacts=[],
        subject_kwargs={"身份证": "350100198005060012"},
    )
    WenshuAdapter().run_one(context, "张三")
    assert searched == ["张三 贿 1980年5月6日"]
    assert any("登录" in message for message in messages)


def test_birth_date_option_without_identity(monkeypatch, tmp_path: Path):
    searched = []
    monkeypatch.setattr("kwtool.adapters.wenshu._logged_in", lambda page: True)
    monkeypatch.setattr(_NationalSearchAdapter, "run_one", lambda self, context, query: searched.append(query))

    class Page:
        def goto(self, url, **kwargs):
            pass

    context = RunContext(
        browser=None, page=Page(), pdf_path=tmp_path / "result.pdf",
        pdf_settings=WenshuAdapter.default_pdf_settings,
        site_settings={"include_birth_date": True},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )
    WenshuAdapter().run_one(context, "张三")
    assert searched == ["张三"]


@pytest.mark.parametrize("limit,expected", [(1, 1), (3, 2)])
def test_prints_available_result_pages_and_combines(monkeypatch, tmp_path: Path, limit: int, expected: int):
    from kwtool.adapters import wenshu

    class Page:
        number = 1

        def evaluate(self, script):
            return None

        def emulate_media(self, **kwargs):
            pass

    class NextButton:
        def click(self):
            page.number += 1

    page = Page()
    monkeypatch.setattr(wenshu, "_result_signature", lambda current: f"result-{current.number}")
    monkeypatch.setattr(wenshu, "_next_page_button", lambda current: NextButton() if current.number < 2 else None)
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",
        pdf_settings=WenshuAdapter.default_pdf_settings, site_settings={"max_pages": limit},
        stop_event=Event(), report_status=lambda message: None, artifacts=[],
    )

    def render_pdf(current):
        writer = PdfWriter()
        writer.add_blank_page(width=100 + current.number, height=100)
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    context.render_pdf = render_pdf
    adapter = WenshuAdapter()
    adapter._max_pages = limit
    adapter._handle_results(context, page)
    assert len(context.artifacts) == 1
    pdf_pages = PdfReader(context.artifacts[0]).pages
    assert len(pdf_pages) == expected
    assert [float(item.mediabox.width) for item in pdf_pages] == [101, 102][:expected]
