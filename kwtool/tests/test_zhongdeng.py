from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from pypdf import PdfReader, PdfWriter

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.zhongdeng import (
    ZhongdengAdapter, _download_after_click, _find_detail_page, _merge_detail_pdfs, _safe_extract,
    _page_range_ready, _save_overall_document, _subject_output_path, _total_rows,
)
from kwtool.models import PdfSettings


def test_zhongdeng_registered_and_uses_identity_keyword():
    adapter = registered_adapters()["zhongdeng"]
    assert adapter.optional_kwargs == ("身份证",)
    assert adapter.default_pdf_settings.scale == 0.35
    assert adapter.settings_schema[0].key == "page_size"
    assert adapter.settings_schema[0].choices == ("10", "20", "30")
    assert adapter.settings_schema[0].default == "30"


def test_subject_artifacts_share_a_folder(tmp_path: Path):
    base = tmp_path / "261014" / "中登网" / "黑龙江金象生化有限责任公司.pdf"
    grouped = _subject_output_path(base, "黑龙江金象生化有限责任公司")
    assert grouped == base.parent / "黑龙江金象生化有限责任公司" / base.name
    assert _subject_output_path(grouped, "黑龙江金象生化有限责任公司") == grouped


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


@pytest.mark.parametrize("page_size,total,number,old_rows,expected_rows", [
    (10, 26, 1, list(range(1, 6)), list(range(1, 11))),
    (20, 26, 2, list(range(1, 21)), list(range(21, 27))),
    (30, 26, 1, list(range(1, 11)), list(range(1, 27))),
])
def test_detail_waits_for_actual_rows_after_page_size_changes(
        page_size, total, number, old_rows, expected_rows):
    class Page:
        def __init__(self):
            self.rows = old_rows

        def locator(self, selector):
            assert selector == "body"
            return SimpleNamespace(inner_text=lambda timeout: (
                f"显示第 {expected_rows[0]} 到第 {expected_rows[-1]} 条记录，共 {total} 条记录"))

        def evaluate(self, _script):
            return self.rows

    page = Page()
    checks = []

    def wait_until(ready, *_args, **_kwargs):
        checks.append(ready())
        page.rows = expected_rows
        checks.append(ready())

    ZhongdengAdapter._wait_page(SimpleNamespace(wait_until=wait_until), page, number, total, page_size)
    assert checks == [False, True]


def test_detail_pdfs_are_combined_and_parts_removed(tmp_path: Path):
    parts = [tmp_path / f"主体_详情_第{number}页.pdf" for number in (1, 2)]
    for part in parts:
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        with part.open("wb") as output:
            writer.write(output)
        writer.close()
    context = SimpleNamespace(pdf_path=tmp_path / "主体.pdf", artifacts=parts.copy())
    merged = _merge_detail_pdfs(context, parts)
    assert merged.name == "主体_详情.pdf"
    assert len(PdfReader(merged).pages) == 2
    assert context.artifacts == [merged]
    assert not any(part.exists() for part in parts)


def assert_ready(predicate):
    assert predicate()
