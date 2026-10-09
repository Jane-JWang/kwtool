"""Baidu search configuration and ordered PDF export."""

from io import BytesIO
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlparse

from pypdf import PdfReader, PdfWriter

from kwtool.adapters import registered_adapters
from kwtool.adapters.baidu import BaiduAdapter, search_url
from kwtool.adapters.base import RunContext


def test_baidu_registration_and_query() -> None:
    adapter = registered_adapters()["baidu"]
    assert isinstance(adapter, BaiduAdapter)
    assert adapter.default_pdf_settings.scale == 0.35
    assert [(field.key, field.default) for field in adapter.settings_schema] == [
        ("keywords", "立案 调查 监管 处罚"), ("max_pages", 2)]
    parsed = urlparse(search_url("某公司 处罚", 3))
    assert parsed.hostname == "www.baidu.com"
    assert parse_qs(parsed.query) == {"wd": ["某公司 处罚"], "pn": ["20"]}


def test_baidu_exports_keywords_and_pages_in_order(tmp_path: Path, monkeypatch) -> None:
    import kwtool.adapters.baidu as module

    class Page:
        url = ""
        printed: list[tuple[str, int]] = []

        def goto(self, url, **_kwargs):
            self.url = url
            return None

        def evaluate(self, _script, *_args):
            return None

        def emulate_media(self, **_kwargs):
            return None

    page = Page()
    monkeypatch.setattr(module, "_result_state", lambda _page, _query, _number: "results")
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    pdf = BytesIO()
    writer.write(pdf)

    context = RunContext(None, page, tmp_path / "result.pdf", BaiduAdapter.default_pdf_settings,
                         {"keywords": "立案 调查 立案", "max_pages": 3}, Event(), lambda _: None, [])  # type: ignore[arg-type]

    def render_pdf(_page):
        params = parse_qs(urlparse(page.url).query)
        page.printed.append((params["wd"][0], int(params["pn"][0])))
        return pdf.getvalue()

    monkeypatch.setattr(context, "render_pdf", render_pdf)
    BaiduAdapter().run_one(context, "某公司")
    assert page.printed == [
        ("某公司 立案", 0), ("某公司 立案", 10), ("某公司 立案", 20),
        ("某公司 调查", 0), ("某公司 调查", 10), ("某公司 调查", 20),
    ]
    assert len(PdfReader(context.artifacts[0]).pages) == 6
