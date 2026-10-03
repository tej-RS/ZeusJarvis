"""macOS TTS backend — the system speech engine via ``say``.

No model to load and no Python dependencies. Quality depends on the voice:
the compact voices that ship with macOS are clean but synthetic, while the
downloadable "Enhanced" and "Premium" voices (System Settings → Accessibility
→ Spoken Content → System voice → Manage Voices) are natural, Siri-grade.
With no ``voice_id`` configured the best installed British voice is used, so
downloading a Premium voice upgrades JARVIS automatically.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

logger = logging.getLogger(__name__)

_SAMPLE_RATE = 24000
_BASE_WPM = 185  # macOS default is ~175; JARVIS speaks a touch briskly

# Preferred British voices, most JARVIS-like first.
_PREFERRED = ("Jamie", "Daniel", "Oliver", "Arthur", "Serena", "Stephanie", "Kate")
# Novelty and low-fidelity voices, never picked automatically.
_NOVELTY = frozenset(
    {
        "Albert", "Bad News", "Bahh", "Bells", "Boing", "Bubbles", "Cellos",
        "Eddy", "Flo", "Fred", "Good News", "Grandma", "Grandpa", "Jester",
        "Junior", "Kathy", "Organ", "Ralph", "Reed", "Rocko", "Sandy",
        "Shelley", "Superstar", "Trinoids", "Whisper", "Wobble", "Zarvox",
    }
)  # fmt: skip

_VOICE_LINE = re.compile(r"^(?P<name>.+?)\s+(?P<locale>[a-z]{2,3}_[A-Za-z0-9]+)\s+#")


@dataclass(frozen=True)
class SystemVoice:
    name: str  # as `say -v` takes it, e.g. "Jamie (Premium)"
    locale: str  # e.g. "en_GB"

    @property
    def base_name(self) -> str:
        return self.name.split(" (", 1)[0]

    @property
    def tier(self) -> int:
        """3 for Premium, 2 for Enhanced, 1 for the compact default voices."""
        if "(Premium)" in self.name:
            return 3
        if "(Enhanced)" in self.name:
            return 2
        return 1


def parse_voices(listing: str) -> List[SystemVoice]:
    """Parse the output of ``say -v '?'``."""
    voices = []
    for line in listing.splitlines():
        match = _VOICE_LINE.match(line)
        if match:
            voices.append(SystemVoice(match["name"].strip(), match["locale"]))
    return voices


def best_voice(voices: List[SystemVoice]) -> Optional[SystemVoice]:
    """Highest-quality British voice, then any English one; novelty excluded."""

    def rank(voice: SystemVoice) -> tuple:
        preferred = (
            len(_PREFERRED) - _PREFERRED.index(voice.base_name)
            if voice.base_name in _PREFERRED
            else 0
        )
        return (voice.locale == "en_GB", voice.tier, preferred)

    candidates = [
        v for v in voices if v.locale.startswith("en_") and v.base_name not in _NOVELTY
    ]
    return max(candidates, key=rank) if candidates else None


@TTSRegistry.register("macos")
class MacOSTTSBackend(TTSBackend):
    """macOS system voices via the ``say`` command."""

    backend_id = "macos"

    def __init__(self) -> None:
        self._voices: Optional[List[SystemVoice]] = None

    def health(self) -> bool:
        return sys.platform == "darwin" and shutil.which("say") is not None

    def _system_voices(self) -> List[SystemVoice]:
        if self._voices is None:
            result = subprocess.run(
                ["say", "-v", "?"], capture_output=True, text=True, timeout=30
            )
            self._voices = parse_voices(result.stdout)
        return self._voices

    def available_voices(self) -> List[str]:
        return [voice.name for voice in self._system_voices()]

    def resolve_voice(self, voice_id: str) -> str:
        """The voice to use: ``voice_id`` if installed, else the best one."""
        installed = self.available_voices()
        if voice_id and voice_id != "auto":
            if voice_id in installed:
                return voice_id
            logger.warning(
                "macOS voice %r is not installed; using the best one", voice_id
            )
        best = best_voice(self._system_voices())
        return best.name if best else ""  # "" lets `say` use the system voice

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "wav",
    ) -> TTSResult:
        import soundfile as sf

        voice = self.resolve_voice(voice_id)
        with tempfile.TemporaryDirectory() as tmp:
            text_path = Path(tmp) / "text.txt"
            out_path = Path(tmp) / "speech.wav"
            text_path.write_text(text, encoding="utf-8")
            command = [
                "say",
                "--file-format=WAVE",
                f"--data-format=LEI16@{_SAMPLE_RATE}",
                "-r",
                str(max(80, round(_BASE_WPM * speed))),
                "-o",
                str(out_path),
                "-f",
                str(text_path),
            ]
            if voice:
                command[1:1] = ["-v", voice]
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=300
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise RuntimeError(f"say failed: {detail or result.returncode}")
            audio = out_path.read_bytes()
            duration = sf.info(str(out_path)).duration
        return TTSResult(
            audio=audio,
            format="wav",  # only WAV is produced, whatever was asked for
            voice_id=voice,
            sample_rate=_SAMPLE_RATE,
            duration_seconds=duration,
            metadata={"backend": "macos", "requested_format": output_format},
        )


__all__ = ["MacOSTTSBackend", "SystemVoice", "best_voice", "parse_voices"]
