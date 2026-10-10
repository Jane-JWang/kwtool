from pathlib import Path

from kwtool import preferences


def test_log_directory_uses_local_app_data(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert preferences.log_directory() == tmp_path / "kwtool" / ".log"


def test_packaged_log_directory_is_independent_of_executable(monkeypatch, tmp_path) -> None:
    executable = tmp_path / "kwtool.exe"
    monkeypatch.setattr(preferences.sys, "frozen", True, raising=False)
    monkeypatch.setattr(preferences.sys, "executable", str(executable))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    assert preferences.log_directory() == tmp_path / "data" / "kwtool" / ".log"


def test_migrate_legacy_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(preferences.sys, "frozen", True, raising=False)
    monkeypatch.setattr(preferences.sys, "executable", str(tmp_path / "program" / "kwtool.exe"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))
    old = tmp_path / "program" / ".log"
    old.mkdir(parents=True)
    (old / "kwtool.log").write_text("previous run", encoding="utf-8")
    preferences.migrate_legacy_logs()
    assert (preferences.log_directory() / "kwtool.log").read_text(encoding="utf-8") == "previous run"
    assert not old.exists()
