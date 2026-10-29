"""Configuration and URL contracts for the securities-site adapters."""

from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.amac_roster import AmacRosterAdapter, ROSTER_URL
from kwtool.adapters.base import RunContext
from kwtool.adapters.securities_search import (
    AMAC_SEARCH, BSE_SEARCH, SZSE_SEARCH, AmacSearchAdapter, BseAdapter, SzseAdapter,
    _max_pages,
)


@pytest.mark.parametrize("key,kind", [
    ("amac_roster", AmacRosterAdapter),
    ("amac_search", AmacSearchAdapter),
    ("szse", SzseAdapter),
    ("bse", BseAdapter),
])
def test_securities_site_registration_and_print_defaults(key: str, kind: type) -> None:
    adapter = registered_adapters()[key]
    assert isinstance(adapter, kind)
    assert adapter.default_pdf_settings.scale == 0.35
    assert adapter.default_pdf_settings.print_background is True
    assert adapter.settings_schema[0].key == "max_pages"
    assert adapter.settings_schema[0].default == 1


def test_official_search_entry_points() -> None:
    assert urlparse(ROSTER_URL).hostname == "www.amac.org.cn"
    assert urlparse(AMAC_SEARCH).hostname == "www.amac.org.cn"
    assert urlparse(SZSE_SEARCH).hostname == "www.szse.cn"
    assert urlparse(BSE_SEARCH).hostname == "www.bse.cn"
    assert parse_qs(urlparse(f"{SZSE_SEARCH}?keyword=%E7%94%B2%26%E4%B9%99").query) == {"keyword": ["甲&乙"]}


@pytest.mark.parametrize("setting", [0, 501, "bad"])
def test_securities_page_limit_rejects_invalid_values(tmp_path: Path, setting: object) -> None:
    context = RunContext(None, None, tmp_path / "result.pdf",  # type: ignore[arg-type]
                         BseAdapter.default_pdf_settings, {"max_pages": setting},  # type: ignore[dict-item]
                         Event(), lambda _: None, [])
    with pytest.raises(ValueError, match="前 K 页"):
        _max_pages(context)
