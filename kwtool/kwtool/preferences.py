from __future__ import annotations

import json
import os
from pathlib import Path

from kwtool.models import DEFAULT_TEMPLATE


def settings_path() -> Path:
    roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return roaming / "kwtool" / "settings.json"


def profile_path() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "kwtool" / "chrome-profile"


def load_settings(path: Path | None = None) -> dict:
    source = path or settings_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict, path: Path | None = None) -> None:
    destination = path or settings_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def default_template(data: dict) -> str:
    value = data.get("output_template")
    return value if isinstance(value, str) and value else DEFAULT_TEMPLATE
