from io import BytesIO
from zipfile import ZipFile

from kwtool.adapters import registered_adapters
from kwtool.adapters.zhongdeng import _safe_extract, _total_rows


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
