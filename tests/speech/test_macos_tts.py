"""macOS system-voice TTS backend."""

from __future__ import annotations

import io
import shutil
import sys

import pytest

from openjarvis.speech.macos_tts import MacOSTTSBackend, best_voice, parse_voices

LISTING = """\
Albert              en_US    # Hello! My name is Albert.
Daniel              en_GB    # Hello! My name is Daniel.
Eddy (English (UK)) en_GB    # Hello! My name is Eddy.
Grandpa (English (UK)) en_GB    # Hello! My name is Grandpa.
Jamie (Premium)     en_GB    # Hello! My name is Jamie.
Samantha            en_US    # Hello! My name is Samantha.
Thomas              fr_FR    # Bonjour, je m'appelle Thomas.
"""


def test_parse_voices_handles_nested_parentheses() -> None:
    voices = {v.name: v.locale for v in parse_voices(LISTING)}
    assert voices["Eddy (English (UK))"] == "en_GB"
    assert voices["Jamie (Premium)"] == "en_GB"
    assert len(voices) == 7


def test_best_voice_prefers_premium_british() -> None:
    assert best_voice(parse_voices(LISTING)).name == "Jamie (Premium)"


def test_best_voice_without_premium_skips_novelty_voices() -> None:
    listing = "\n".join(line for line in LISTING.splitlines() if "Jamie" not in line)
    assert best_voice(parse_voices(listing)).name == "Daniel"


def test_best_voice_falls_back_to_any_english() -> None:
    listing = "Samantha            en_US    # Hello!\nThomas  fr_FR    # Bonjour"
    assert best_voice(parse_voices(listing)).name == "Samantha"
    assert best_voice(parse_voices("Thomas  fr_FR    # Bonjour")) is None


def test_unknown_configured_voice_falls_back(monkeypatch) -> None:
    backend = MacOSTTSBackend()
    monkeypatch.setattr(backend, "_system_voices", lambda: parse_voices(LISTING))
    assert backend.resolve_voice("Nonexistent") == "Jamie (Premium)"
    assert backend.resolve_voice("Daniel") == "Daniel"
    assert backend.resolve_voice("") == "Jamie (Premium)"


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("say") is None, reason="needs macOS say"
)
def test_synthesizes_wav() -> None:
    sf = pytest.importorskip("soundfile")
    backend = MacOSTTSBackend()
    assert backend.health()
    result = backend.synthesize("Systems online.", speed=1.2)
    assert result.format == "wav"
    data, rate = sf.read(io.BytesIO(result.audio))
    assert rate == 24000
    assert len(data) > 0.3 * rate
    assert result.voice_id in backend.available_voices() + [""]
