from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

import pytest

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.mof import MofAdapter, NO_RESULTS, search_url


def test_mof_search_uses_all_content_latest_first_and_first_page() -> None:
    parsed = urlparse(search_url("甲公司 & 乙公司"))
    params = parse_qs(parsed.query, keep_blank_values=True)
    assert parsed.hostname == "search.mof.gov.cn"
    assert parsed.path == "/was5/web/search"
    assert params["channelid"] == ["295890"]
    assert params["andsen"] == ["甲公司 & 乙公司"]
    assert params["searchscope"] == [""]
    assert params["orderby"] == ["-DOCRELTIME"]
    assert params["page"] == ["1"]
    assert params["perpage"] == ["10"]


@pytest.mark.parametrize("has_results", [True, False])
def test_mof_prints_loaded_search_page(tmp_path: Path, has_results: bool) -> None:
    class Locator:
        def __init__(self, selector):
            self.selector = selector
            self.first = self

        def count(self):
            if self.selector == ".list_search dl":
                return 1 if has_results else 0
            return 1

        def inner_text(self):
            return NO_RESULTS if self.selector == "body" else "按时间排序"

    class Page:
        url = "about:blank"

        def goto(self, url, **kwargs):
            self.url = url
            return None

        def locator(self, selector):
            return Locator(selector)

        def evaluate(self, expression):
            assert expression in {"document.fonts.ready", "window.scrollTo(0, 0)"}

        def emulate_media(self, **kwargs):
            assert kwargs == {"media": "screen"}

        def pdf(self, **kwargs):
            self.pdf_options = kwargs
            return b"%PDF-1.4\n"

    adapter = registered_adapters()["mof"]
    assert isinstance(adapter, MofAdapter)
    page = Page()
    context = RunContext(
        browser=None,  # type: ignore[arg-type]
        page=page,  # type: ignore[arg-type]
        pdf_path=tmp_path / "search.pdf",
        pdf_settings=adapter.default_pdf_settings,
        site_settings={},
        stop_event=Event(),
        report_status=lambda _: None,
        artifacts=[],
    )
    adapter.run_one(context, "测试公司")
    assert page.pdf_options["landscape"] is False
    assert page.pdf_options["scale"] == 0.35
    assert page.pdf_options["display_header_footer"] is True
    assert context.artifacts == [tmp_path / "search.pdf"]
