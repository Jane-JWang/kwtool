from pathlib import Path
from threading import Event

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext, SiteUnavailable
from kwtool.adapters.pboc import PbocAdapter, RATING_TITLE, RATING_URL


class Page:
    url = RATING_URL

    def __init__(self, status=200, body=RATING_TITLE):
        self.status = status
        self.body = body
        self.visited = None

    def goto(self, url, **kwargs):
        self.visited = url
        return type("Response", (), {"status": self.status})()

    def locator(self, selector):
        assert selector == "body"
        return self

    def inner_text(self, **kwargs):
        return self.body

    def evaluate(self, script):
        return None

    def emulate_media(self, **kwargs):
        assert kwargs == {"media": "screen"}


def context(tmp_path: Path, page: Page, selected=True):
    saved = []
    run = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "result.pdf",  # type: ignore[arg-type]
        pdf_settings=PbocAdapter.default_pdf_settings,
        site_settings={"rating_usable": selected}, stop_event=Event(),
        report_status=lambda _: None, artifacts=[],
    )
    run.wait_until = lambda ready, *_args, **_kwargs: assert_ready(ready)
    run.save_pdf = lambda current: saved.append(current) or run.pdf_path
    return run, saved


def assert_ready(ready):
    assert ready()


def test_pboc_checkbox_prints_verified_official_roster(tmp_path: Path):
    adapter = registered_adapters()["pboc"]
    assert adapter.display_name == "中国人民银行"
    assert [(field.key, field.label, field.kind, field.default) for field in adapter.settings_schema] == [
        ("rating_usable", RATING_TITLE, "bool", False)
    ]
    assert adapter.default_pdf_settings.scale == 0.35
    page = Page()
    run, saved = context(tmp_path, page)
    adapter.run_one(run, "样例公司")
    assert page.visited == RATING_URL
    assert saved == [page]


def test_pboc_requires_selection_and_rejects_unavailable_page(tmp_path: Path):
    run, saved = context(tmp_path, Page(), selected=False)
    with pytest.raises(ValueError, match="至少勾选"):
        PbocAdapter().run_one(run, "样例公司")
    assert saved == []

    run, saved = context(tmp_path, Page(status=404))
    with pytest.raises(SiteUnavailable, match="不可用"):
        PbocAdapter().run_one(run, "样例公司")
    assert saved == []

    page = Page()
    page.url = "https://www.pbc.gov.cn.evil.test/roster"
    run, saved = context(tmp_path, page)
    with pytest.raises(SiteUnavailable, match="不可用"):
        PbocAdapter().run_one(run, "样例公司")
    assert saved == []
