from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, quote, urlparse

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.csrc import CsrcAdapter, search_url


def test_csrc_search_uses_latest_first_and_first_page() -> None:
    url = urlparse(search_url("测试 公司"))
    assert url.netloc == "www.csrc.gov.cn"
    params = parse_qs(url.query, keep_blank_values=True)
    assert params["searchWord"] == [quote("测试 公司", safe="")]
    assert params["manualWord"] == params["searchWord"]
    assert params["column"] == [quote("全部", safe="")]
    assert params["siteCode"] == ["bm56000001"]
    assert params["orderBy"] == ["1"]
    assert params["pageNum"] == ["0"]
    assert params["pageSize"] == ["10"]
    assert params["checkHandle"] == ["1"]
    assert params["govWorkBean"] == [quote("{}", safe="")]


def test_csrc_prints_search_page_with_site_defaults(tmp_path: Path) -> None:
    class Body:
        def is_visible(self):
            return True

        def inner_text(self, **kwargs):
            return "相关结果 10 个 2024-10-18"

    class Page:
        url = "about:blank"

        def goto(self, url, **kwargs):
            self.url = url
            return None

        def locator(self, selector):
            assert selector == "body"
            return Body()

        def evaluate(self, expression):
            assert expression in {"document.fonts.ready", "window.scrollTo(0, 0)"}

        def emulate_media(self, **kwargs):
            assert kwargs == {"media": "screen"}

        def pdf(self, **kwargs):
            self.pdf_options = kwargs
            return b"%PDF-1.4\n"

    adapter = registered_adapters()["csrc"]
    assert isinstance(adapter, CsrcAdapter)
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
