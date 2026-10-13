from io import BytesIO
from pathlib import Path
from threading import Event
from zipfile import ZipFile

from kwtool.adapters import registered_adapters
from kwtool.adapters.base import RunContext
from kwtool.adapters.zhongdeng import _safe_extract, _save_overall_document, _total_rows
from kwtool.models import PdfSettings


def test_zhongdeng_registered_and_uses_identity_keyword():
    adapter = registered_adapters()["zhongdeng"]
    assert adapter.optional_kwargs == ("身份证",)
    assert adapter.default_pdf_settings.scale == 0.35


def test_result_count_and_safe_attachment_extraction(tmp_path):
    assert _total_rows("共 36 条记录") == 36
    assert _total_rows("加载中") is None
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
