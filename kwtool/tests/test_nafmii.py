from pathlib import Path
from threading import Event

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.nafmii import NafmiiAdapter, ROSTERS, _official, _reset_member_search


def test_nafmii_is_one_site_with_eight_independent_rosters():
    adapters = registered_adapters()
    assert adapters["nafmii"].display_name == "交易协商会"
    assert len(ROSTERS) == 8
    assert len({item.key for item in ROSTERS}) == 8
    assert all(spec.kind == "bool" for spec in adapters["nafmii"].settings_schema)
    assert not any(key in adapters for key in ("csrc_rating_filing", "csrc_accounting_filing"))
    assert _official("https://www.nafmii.org.cn/hyfw/hyflmd/")
    assert not _official("https://www.nafmii.org.cn.evil.test/file.pdf")


def test_nafmii_requires_a_selection(tmp_path: Path):
    context = RunContext(
        browser=None, page=None, pdf_path=tmp_path / "roster.pdf",  # type: ignore[arg-type]
        pdf_settings=NafmiiAdapter.default_pdf_settings,
        site_settings={}, stop_event=Event(), report_status=lambda _: None, artifacts=[],
    )
    with pytest.raises(ValueError, match="至少勾选"):
        NafmiiAdapter().run_one(context, "样例公司")


def test_previous_member_search_is_cleared_before_next_batch():
    class Page:
        stored = "上一批主体"
        reloaded = False

        def evaluate(self, script):
            if "getItem" in script:
                return self.stored
            assert "removeItem" in script
            self.stored = None

        def reload(self, **kwargs):
            assert kwargs == {"wait_until": "domcontentloaded", "timeout": 60000}
            self.reloaded = True

    page = Page()
    _reset_member_search(page)  # type: ignore[arg-type]
    assert page.stored is None
    assert page.reloaded
    page.reloaded = False
    _reset_member_search(page)  # type: ignore[arg-type]
    assert not page.reloaded
