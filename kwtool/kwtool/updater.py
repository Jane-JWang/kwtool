"""GitHub Release discovery and staged Windows directory updates."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from kwtool import __version__


ASSET_NAME = "kwtool-windows.zip"
REPOSITORY = os.environ.get("KWTOOL_UPDATE_REPO", "")
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def version_key(value: str) -> tuple[int, int, int] | None:
    match = _VERSION.fullmatch(value)
    return tuple(map(int, match.groups())) if match else None


def release_repository() -> str:
    if REPOSITORY:
        return REPOSITORY
    config = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])) / "update_repo.txt"
    try:
        value = config.read_text(encoding="utf-8-sig").strip()
    except OSError:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("尚未配置 GitHub 发布仓库。请在打包时设置 KWTOOL_UPDATE_REPO=owner/repo。")
    return value


def releases() -> list[dict]:
    repo = release_repository()
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": f"kwtool/{__version__}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    if not isinstance(data, list):
        raise ValueError("GitHub 没有返回版本列表")
    result = []
    for release in data:
        if release.get("draft") or release.get("prerelease") or version_key(release.get("tag_name", "")) is None:
            continue
        asset = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
        if asset and asset.get("browser_download_url") and asset.get("digest", "").startswith("sha256:"):
            result.append({"tag": release["tag_name"], "asset": asset["browser_download_url"], "digest": asset["digest"][7:]})
    return sorted(result, key=lambda item: version_key(item["tag"]), reverse=True)


class ReleaseWorker(QThread):
    found = Signal(object)
    failed = Signal(str)

    def run(self) -> None:
        try:
            self.found.emit(releases())
        except Exception as exc:
            self.failed.emit(str(exc))


def stage_release(release: dict, destination: Path) -> Path:
    """Download, verify and unpack a release beside the current installation."""
    staging = Path(tempfile.mkdtemp(prefix=".kwtool-update-", dir=destination.parent))
    archive = staging / ASSET_NAME
    try:
        request = urllib.request.Request(release["asset"], headers={"User-Agent": f"kwtool/{__version__}"})
        digest = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as source, archive.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().lower() != release["digest"].lower():
            raise ValueError("下载文件的 SHA-256 校验失败")
        package = staging / "package"
        package.mkdir()
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                path = Path(member.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "kwtool":
                    raise ValueError("发布包目录结构无效")
                target = package.joinpath(*path.parts)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(member) as source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
        extracted = package / "kwtool"
        if not (extracted / "kwtool.exe").is_file() or not (extracted / "_internal").is_dir():
            raise ValueError("发布包缺少程序文件")
        archive.unlink()
        return extracted
    except Exception:
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        raise


class DownloadWorker(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, release: dict, destination: Path) -> None:
        super().__init__()
        self.release = release
        self.destination = destination

    def run(self) -> None:
        try:
            self.ready.emit(stage_release(self.release, self.destination))
        except Exception as exc:
            self.failed.emit(str(exc))


_SWAP_SCRIPT = r'''
param([int]$ParentPid, [string]$Current, [string]$Replacement)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    Wait-Process -Id $ParentPid -Timeout 90 -ErrorAction SilentlyContinue
    if (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) { throw '程序未能在 90 秒内退出' }
    $backup = "$Current.kwtool-backup"
    if (Test-Path -LiteralPath $backup) { throw "旧备份目录已存在：$backup" }
    Move-Item -LiteralPath $Current -Destination $backup
    try {
        Move-Item -LiteralPath $Replacement -Destination $Current
        Start-Process -FilePath (Join-Path $Current 'kwtool.exe') -WindowStyle Hidden
    } catch {
        if (Test-Path -LiteralPath $Current) { Remove-Item -LiteralPath $Current -Recurse -Force }
        Move-Item -LiteralPath $backup -Destination $Current
        throw
    }
    Remove-Item -LiteralPath $backup -Recurse -Force
} catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'kwtool 更新失败') | Out-Null
}
'''


def launch_swap(replacement: Path) -> None:
    script = replacement.parent.parent / "apply-update.ps1"
    script.write_text(_SWAP_SCRIPT, encoding="utf-8-sig")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         "-ParentPid", str(os.getpid()), "-Current", str(Path(sys.executable).resolve().parent),
         "-Replacement", str(replacement)],
        creationflags=creationflags,
    )
