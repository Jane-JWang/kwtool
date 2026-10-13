from pathlib import Path
from threading import Event

import pytest

from kwtool.adapters.base import RunContext
from kwtool.adapters.wenshu import WenshuAdapter, _birth_date
from kwtool.adapters.national_search import _NationalSearchAdapter


def test_birth_date_and_settings():
    adapter = WenshuAdapter()
    assert _birth_date("350100198005060012") == "1980年5月6日"
    assert _birth_date("350100800506001") == "1980年5月6日"
    assert adapter.optional_kwargs == ("身份证",)
    assert adapter.site.result_markers == ("共检索到",)
    assert [field.label for field in adapter.settings_schema] == ["受贿调查", "加入出生日期筛选"]
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
