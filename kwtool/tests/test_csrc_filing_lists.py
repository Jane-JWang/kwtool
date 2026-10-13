from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest

from kwtool.adapters.base import RunContext, SiteUnavailable
from kwtool.adapters.csrc_filing_lists import (
    ACCOUNTING_TITLE, LIST_URL, RATING_TITLE,
    CsrcAccountingFilingAdapter, CsrcRatingFilingAdapter,
    roster_match,
)


@pytest.mark.parametrize("adapter_type,title,suffix", [
    (CsrcRatingFilingAdapter, RATING_TITLE, "docx"),
    (CsrcAccountingFilingAdapter, ACCOUNTING_TITLE, "xlsx"),
])
def test_filing_roster_saves_announcement_and_original_attachment(
    tmp_path: Path, adapter_type, title: str, suffix: str,
) -> None:
    class Link:
        def __init__(self, href):
            self.href = href

        @property
        def first(self):
            return self

        def count(self):
            return 1

        def get_attribute(self, name):
            assert name == "href"
            return self.href

        def click(self):
            page.clicked = True

    class Locator:
        def filter(self, *, has_text):
            assert has_text == title
            return Link("/csrc/c100102/c123/content.shtml")

        @property
        def first(self):
            return Link(f"/csrc/c100102/c123/files/list.{suffix}")

        def count(self):
            return 1

        def inner_text(self, **kwargs):
            return title

    class Download:
        suggested_filename = f"roster.{suffix}"

        def save_as(self, path):
            Path(path).write_bytes(b"original")

    class DownloadInfo:
        value = Download()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class Page:
        url = "about:blank"
        clicked = False

        def goto(self, url, **kwargs):
            self.url = url
            return None

        def locator(self, selector):
            if selector == "a":
                return Locator()
            if selector == "body" or selector.startswith("a[href$"):
                return Locator()
            raise AssertionError(selector)

        def expect_download(self, **kwargs):
            return DownloadInfo()

        def evaluate(self, expression):
            assert expression in {"document.fonts.ready", "window.scrollTo(0, 0)"}

        def emulate_media(self, **kwargs):
            assert kwargs == {"media": "screen"}

        def pdf(self, **kwargs):
            self.pdf_options = kwargs
            return b"%PDF-1.4\n"

    page = Page()
    context = RunContext(
        browser=None, page=page, pdf_path=tmp_path / "roster.pdf",  # type: ignore[arg-type]
        pdf_settings=adapter_type.default_pdf_settings, site_settings={},
        stop_event=Event(), report_status=lambda _: None, artifacts=[],
    )
    context.wait_until = lambda ready, *_args, **_kwargs: assert_ready(ready)
    adapter_type().run_one(context, "样例公司")
    assert page.clicked
    assert context.artifacts == [
        tmp_path / "roster_附件" / f"roster.{suffix}",
        tmp_path / "roster_附件" / "roster_核对结果.txt",
        tmp_path / "roster.pdf",
    ]
    assert "无法判定" in context.artifacts[1].read_text(encoding="utf-8")
    assert page.pdf_options["scale"] == 0.35
    assert page.pdf_options["display_header_footer"] is True


def assert_ready(ready):
    assert ready()


def test_offsite_article_is_rejected(tmp_path: Path) -> None:
    class Link:
        @property
        def first(self):
            return self

        def count(self):
            return 1

        def get_attribute(self, _):
            return "https://example.com/roster"

    class Page:
        url = LIST_URL

        def goto(self, *_args, **_kwargs):
            return None

        def locator(self, _):
            return self

        def filter(self, **_):
            return Link()

    context = RunContext(
        browser=None, page=Page(), pdf_path=tmp_path / "out.pdf",  # type: ignore[arg-type]
        pdf_settings=CsrcRatingFilingAdapter.default_pdf_settings,
        site_settings={}, stop_event=Event(), report_status=lambda _: None, artifacts=[],
    )
    with pytest.raises(SiteUnavailable, match="域名异常"):
        CsrcRatingFilingAdapter().run_one(context, "样例公司")


def test_docx_match_and_no_match(tmp_path: Path) -> None:
    path = tmp_path / "rating.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
          <w:body><w:p><w:r><w:t>东方金诚国际</w:t></w:r><w:r><w:t>信用评估有限公司</w:t></w:r></w:p></w:body>
        </w:document>""")
    assert roster_match(path, "东方金诚国际信用评估有限公司") is True
    assert roster_match(path, "其他公司") is False


def test_xlsx_shared_strings_and_inline_strings(tmp_path: Path) -> None:
    path = tmp_path / "accounting.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <si><t>大华会计师事务所</t></si></sst>""")
        archive.writestr("xl/worksheets/sheet1.xml", """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
          <sheetData><row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>（特殊普通合伙）</t></is></c></row></sheetData>
        </worksheet>""")
    assert roster_match(path, "大华会计师事务所") is True
    assert roster_match(path, "特殊普通合伙") is True
    assert roster_match(path, "未知事务所") is False


def test_unreadable_office_file_is_unknown(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not a zip")
    assert roster_match(path, "任意公司") is None
