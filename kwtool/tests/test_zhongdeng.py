from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.zhongdeng import (
    _download_after_click, _find_detail_page, _safe_extract,
    _page_range_ready, _save_overall_document, _total_rows,
)
from kwtool.models import PdfSettings


def test_zhongdeng_registered_and_uses_identity_keyword():
    adapter = registered_adapters()["zhongdeng"]
    assert adapter.optional_kwargs == ("身份证",)
    assert adapter.default_pdf_settings.scale == 0.35


def test_result_count_and_safe_attachment_extraction(tmp_path):
    assert _total_rows("共 36 条记录") == 36
    assert _total_rows("加载中") is None
    assert _page_range_ready("显示第 1 到第 26 条记录，共 26 条记录", 1, 26)
    assert _page_range_ready("显示第 31 到 36 条记录", 31, 36)
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr("证明/文件.pdf", b"PDF")
        archive.writestr("../outside.txt", b"NO")
    source = tmp_path / "files.zip"
    source.write_bytes(data.getvalue())
    extracted = _safe_extract(source, tmp_path / "extracted")
    assert len(extracted) == 1
    assert extracted[0].read_bytes() == b"PDF"
    assert not (tmp_path / "outside.txt").exists()


def test_query_proof_is_saved_beside_pdfs(tmp_path: Path):
    class Download:
        suggested_filename = "20260199279165500724.pdf"

        def save_as(self, destination):
            destination.write_bytes(b"proof")

        def failure(self):
            return None

    context = RunContext(
        browser=None, page=None, pdf_path=tmp_path / "机构_中登网.pdf",  # type: ignore[arg-type]
        pdf_settings=PdfSettings(), site_settings={}, stop_event=Event(),
        report_status=lambda _: None, artifacts=[],
    )
    saved = _save_overall_document(context, Download(), "查询证明")  # type: ignore[arg-type]
    assert saved == tmp_path / "机构_中登网_查询证明.pdf"
    assert saved.read_bytes() == b"proof"
    assert context.artifacts == [saved]


def test_detail_popup_is_selected_over_previous_result_tab():
    class Page:
        def __init__(self, path, body):
            self.url = "https://www.zhongdengwang.org.cn/out/#" + path
            self.body = body

        def is_closed(self):
            return False

        def locator(self, selector):
            assert selector == "body"
            return SimpleNamespace(inner_text=lambda timeout: self.body)

    search = Page("/query/guarantor/summary", "查询结果统计")
    previous = Page("/query/guarantor/register/Result", "登记信息列表")
    popup = Page("/query/guarantor/register/Result", "登记信息列表")
    context = SimpleNamespace(browser=SimpleNamespace(pages=[search, previous, popup]))
    assert _find_detail_page(context, search, {search, previous}) is popup


def test_pending_proof_notice_ends_download_wait():
    notices = []

    class Page:
        def on(self, *_):
            pass

        def remove_listener(self, *_):
            pass

        def is_closed(self):
            return False

        def locator(self, _):
            return SimpleNamespace(all_inner_texts=lambda: notices)

    page = Page()
    browser = SimpleNamespace(pages=[page], on=lambda *_: None, remove_listener=lambda *_: None)
    context = SimpleNamespace(browser=browser, wait_until=lambda predicate, *_args, **_kwargs: assert_ready(predicate))

    def click():
        notices.append("您的查询证明文件还未生成，请稍后在我的查询信息中下载")

    assert _download_after_click(context, page, click, "等待", allow_deferred=True) is None


def assert_ready(predicate):
    assert predicate()
