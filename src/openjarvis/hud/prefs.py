"""Settings J.A.R.V.I.S. OS remembers between launches."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openjarvis.core.paths import get_config_dir


def _path() -> Path:
    return get_config_dir() / ".state" / "hud.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(**changes: Any) -> None:
    """Merge ``changes`` into the saved settings; failures are ignored."""
    data = {**load(), **changes}
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass


def voice_default() -> bool:
    """Spoken replies start on unless the user last muted them."""
    value = load().get("voice", True)
    return value if isinstance(value, bool) else True


def wake_default() -> bool:
    """The "Hey JARVIS" wake word keeps the microphone open, so it is opt-in."""
    value = load().get("wake", False)
    return value if isinstance(value, bool) else False


__all__ = ["load", "save", "voice_default", "wake_default"]
