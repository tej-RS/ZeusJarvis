"""'Hey JARVIS' wake-word listener."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from openjarvis.speech import wake_word  # noqa: E402
from openjarvis.speech.wake_word import (  # noqa: E402
    FRAME_SAMPLES,
    MODEL_NAME,
    WakeWordListener,
)


class FakeModel:
    def __init__(self, scores):
        self.scores = list(scores)
        self.resets = 0

    def predict(self, frame):
        return {MODEL_NAME: self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def feed(self, frame):
        self.callback(frame.reshape(-1, 1), len(frame), None, None)


def _listener(scores, **kwargs):
    streams = []
    woke = threading.Event()
    calls = []

    def on_wake():
        calls.append(time.monotonic())
        woke.set()

    def factory(callback):
        streams.append(FakeStream(callback))
        return streams[-1]

    listener = WakeWordListener(
        on_wake, model=FakeModel(scores), stream_factory=factory, **kwargs
    )
    listener.start()
    return listener, streams[0], woke, calls


def _frame(value=100):
    return np.full(FRAME_SAMPLES, value, dtype=np.int16)


def _settle():
    time.sleep(0.3)


def test_wakes_above_threshold_once_per_cooldown() -> None:
    listener, stream, woke, calls = _listener([0.1, 0.9, 0.95, 0.97])
    for _ in range(4):
        stream.feed(_frame())
    assert woke.wait(2)
    _settle()
    listener.stop()
    assert len(calls) == 1  # the follow-up high scores fall inside the cooldown
    assert stream.closed


def test_paused_listener_ignores_audio() -> None:
    listener, stream, woke, calls = _listener([0.9, 0.9])
    listener.pause()
    stream.feed(_frame())
    _settle()
    assert not calls
    listener.resume()
    stream.feed(_frame())
    assert woke.wait(2)
    listener.stop()


def test_tracks_whether_the_microphone_delivers_audio() -> None:
    listener, stream, _, _ = _listener([])
    stream.feed(_frame(0))
    assert not listener.audio_seen  # a denied macOS microphone delivers silence
    stream.feed(_frame(50))
    assert listener.audio_seen
    listener.stop()


def test_ensure_models_skips_download_when_present(tmp_path, monkeypatch) -> None:
    for name in wake_word._MODEL_FILES:
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(
        "openwakeword.utils.download_models",
        lambda **_: pytest.fail("should not download"),
        raising=False,
    )
    assert wake_word.ensure_models(tmp_path) == tmp_path


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("say") is None, reason="needs macOS say"
)
def test_real_model_hears_hey_jarvis(tmp_path) -> None:
    """End to end with the real model: synthesized speech in, wake out."""
    pytest.importorskip("openwakeword")
    sf = pytest.importorskip("soundfile")
    # The suite runs with a throwaway config home; also look in the real one.
    candidates = [
        wake_word.model_dir(),
        Path.home() / ".openjarvis/models/openwakeword",
    ]
    directory = next(
        (
            d
            for d in candidates
            if all((d / n).exists() for n in wake_word._MODEL_FILES)
        ),
        None,
    )
    if directory is None:
        pytest.skip("wake-word models not downloaded")

    def clip(text):
        path = tmp_path / "clip.wav"
        subprocess.run(
            ["say", "-v", "Daniel", "--file-format=WAVE", "--data-format=LEI16@16000",
             "-o", str(path), text],
            check=True,
        )  # fmt: skip
        audio, _ = sf.read(str(path), dtype="int16")
        pad = np.zeros(FRAME_SAMPLES * 12, dtype=np.int16)
        return np.concatenate([pad, audio, pad])

    def heard(text):
        streams, woke = [], threading.Event()
        listener = WakeWordListener(
            woke.set,
            model=wake_word.load_model(directory),
            stream_factory=lambda cb: streams.append(FakeStream(cb)) or streams[-1],
        )
        listener.start()
        audio = clip(text)
        for start in range(0, len(audio) - FRAME_SAMPLES, FRAME_SAMPLES):
            streams[0].feed(audio[start : start + FRAME_SAMPLES])
            time.sleep(0.002)  # let the scoring thread keep up
        result = woke.wait(2)
        listener.stop()
        return result

    assert heard("Hey Jarvis")
    assert not heard("Hello there, how are you today?")
