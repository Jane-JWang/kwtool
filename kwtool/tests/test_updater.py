import hashlib
import io
import zipfile

import pytest

from kwtool import updater


def test_version_key_rejects_unexpected_tags() -> None:
    assert updater.version_key("v0.2.0") == (0, 2, 0)
    assert updater.version_key("0.10.1") > updater.version_key("0.9.9")
    assert updater.version_key("v0.2.0-rc1") is None


def test_stage_release_checks_digest_and_structure(monkeypatch, tmp_path) -> None:
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as bundle:
        bundle.writestr("kwtool/kwtool.exe", b"exe")
        bundle.writestr("kwtool/_internal/data", b"data")
    payload = content.getvalue()
    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *args, **kwargs: io.BytesIO(payload))
    target = updater.stage_release({"asset": "https://example.invalid/file", "digest": hashlib.sha256(payload).hexdigest()}, tmp_path / "kwtool")
    assert (target / "kwtool.exe").read_bytes() == b"exe"
    with pytest.raises(ValueError, match="SHA-256"):
        updater.stage_release({"asset": "https://example.invalid/file", "digest": "0" * 64}, tmp_path / "kwtool")
