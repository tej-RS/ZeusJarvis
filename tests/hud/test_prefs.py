from __future__ import annotations

import pytest

from openjarvis.hud import prefs


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch) -> None:
    # The suite's test home is shared; saved settings would leak between tests.
    monkeypatch.setenv("OPENJARVIS_HOME", str(tmp_path))


def test_voice_defaults_on() -> None:
    assert prefs.voice_default() is True


def test_voice_choice_is_remembered() -> None:
    prefs.save(voice=False)
    assert prefs.voice_default() is False
    prefs.save(voice=True)
    assert prefs.voice_default() is True


def test_corrupt_file_falls_back(tmp_path) -> None:
    (tmp_path / ".state").mkdir()
    (tmp_path / ".state" / "hud.json").write_text("{not json")
    assert prefs.voice_default() is True
    prefs.save(voice=False)
    assert prefs.load() == {"voice": False}
