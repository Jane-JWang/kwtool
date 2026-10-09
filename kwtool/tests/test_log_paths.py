from pathlib import Path

from kwtool import preferences


def test_source_log_directory_is_next_to_launcher(monkeypatch) -> None:
    monkeypatch.delattr(preferences.sys, "frozen", raising=False)
    assert preferences.log_directory() == Path(preferences.__file__).resolve().parents[1] / ".log"


def test_packaged_log_directory_is_next_to_executable(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "kwtool.exe"
    monkeypatch.setattr(preferences.sys, "frozen", True, raising=False)
    monkeypatch.setattr(preferences.sys, "executable", str(executable))
    assert preferences.log_directory() == tmp_path / ".log"
