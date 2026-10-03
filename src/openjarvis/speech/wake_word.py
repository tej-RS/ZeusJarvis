"""Always-on "Hey JARVIS" wake-word listener, using openWakeWord locally.

Microphone audio is analysed in memory 80 ms at a time and then discarded:
nothing is recorded, stored or sent anywhere. The openWakeWord code is
Apache-2.0; its pretrained models are CC BY-NC-SA 4.0 (personal use).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from openjarvis.core.paths import get_config_dir

logger = logging.getLogger(__name__)

MODEL_NAME = "hey_jarvis_v0.1"
SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280  # 80 ms, the chunk openWakeWord scores at a time
_MODEL_FILES = (f"{MODEL_NAME}.onnx", "melspectrogram.onnx", "embedding_model.onnx")


def model_dir() -> Path:
    # Outside site-packages, so reinstalling the package doesn't delete them.
    return get_config_dir() / "models" / "openwakeword"


def ensure_models(directory: Optional[Path] = None) -> Path:
    """Download the wake-word models (a few MB) on first use."""
    directory = directory or model_dir()
    if all((directory / name).exists() for name in _MODEL_FILES):
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    from openwakeword.utils import download_models

    download_models(model_names=[MODEL_NAME], target_directory=str(directory))
    missing = [name for name in _MODEL_FILES if not (directory / name).exists()]
    if missing:
        raise RuntimeError(f"Wake-word model download incomplete: {', '.join(missing)}")
    return directory


def load_model(directory: Path) -> Any:
    from openwakeword.model import Model

    return Model(
        wakeword_models=[str(directory / f"{MODEL_NAME}.onnx")],
        inference_framework="onnx",
        melspec_model_path=str(directory / "melspectrogram.onnx"),
        embedding_model_path=str(directory / "embedding_model.onnx"),
    )


def _microphone(callback: Callable[..., None]) -> Any:
    import sounddevice as sd

    return sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        blocksize=FRAME_SAMPLES,
        callback=callback,
    )


class WakeWordListener:
    """Streams the microphone through openWakeWord and calls ``on_wake``.

    ``on_wake`` runs on the listener's own thread. While paused (say, while
    the app records the command that follows) audio is ignored; the
    microphone stays open so resuming is instant.
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        *,
        threshold: float = 0.5,
        cooldown: float = 2.0,
        model: Any = None,
        stream_factory: Callable[[Callable[..., None]], Any] = _microphone,
    ) -> None:
        self._on_wake = on_wake
        self._threshold = threshold
        self._cooldown = cooldown
        self._model = model
        self._stream_factory = stream_factory
        self._frames: "queue.Queue[Any]" = queue.Queue(maxsize=64)
        self._paused = threading.Event()
        self._stopped = threading.Event()
        self._stream: Any = None
        self._worker: Optional[threading.Thread] = None
        self._last_wake = 0.0
        # macOS feeds silence, not an error, when the microphone is denied.
        self.audio_seen = False

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> None:
        """Load the model (downloading it once) and start listening."""
        if self.running:
            return
        if self._model is None:
            self._model = load_model(ensure_models())
        self._stopped.clear()
        stream = self._stream_factory(self._on_audio)
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            except Exception:
                pass
            raise
        self._stream = stream
        self._worker = threading.Thread(target=self._run, name="wake-word", daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stopped.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.debug("Closing the wake-word stream failed", exc_info=True)
        if self._worker is not None:
            self._worker.join(timeout=2)
            self._worker = None

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._drain()
        if self._model is not None:
            self._model.reset()  # forget audio from before the pause
        self._paused.clear()

    def _on_audio(self, indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if self._paused.is_set():
            return
        if not self.audio_seen and indata.any():
            self.audio_seen = True
        try:
            self._frames.put_nowait(indata[:, 0].copy())
        except queue.Full:
            pass  # scoring fell behind; dropping a frame beats lagging

    def _drain(self) -> None:
        while True:
            try:
                self._frames.get_nowait()
            except queue.Empty:
                return

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                frame = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._paused.is_set():
                continue
            score = self._model.predict(frame).get(MODEL_NAME, 0.0)
            now = time.monotonic()
            if score >= self._threshold and now - self._last_wake >= self._cooldown:
                self._last_wake = now
                self._model.reset()
                try:
                    self._on_wake()
                except Exception:
                    logger.warning("Wake-word handler failed", exc_info=True)


__all__ = ["MODEL_NAME", "WakeWordListener", "ensure_models", "load_model", "model_dir"]
