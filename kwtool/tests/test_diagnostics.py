import json

from kwtool.diagnostics import capture_page, safe_url


def test_diagnostic_snapshot_omits_url_query_values(tmp_path) -> None:
    class Page:
        url = "https://neris.csrc.gov.cn/shixinchaxun/searchResult?obj_name=主体&verification=secret-token"

        def evaluate(self, script):
            assert "document.querySelectorAll" in script
            return {"title": "查询结果", "text": "一条记录", "elements": [{"tag": "div", "class": "resultBox"}]}

        def screenshot(self, **kwargs):
            assert kwargs["full_page"] is True
            return b"PNG"

    image, metadata = capture_page(Page(), tmp_path, "result-ready")  # type: ignore[arg-type]
    assert image.read_bytes() == b"PNG"
    data = json.loads(metadata.read_text(encoding="utf-8"))
    assert data["url"] == "https://neris.csrc.gov.cn/shixinchaxun/searchResult"
    assert "secret-token" not in metadata.read_text(encoding="utf-8")
    assert safe_url("about:blank") == "about:blank"
