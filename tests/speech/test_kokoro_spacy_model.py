"""Kokoro's English G2P needs a spaCy model; it must install quietly."""

from __future__ import annotations

import importlib
import subprocess
import sys
from unittest import mock

import pytest

spacy = pytest.importorskip("spacy")

from openjarvis.speech import kokoro_tts  # noqa: E402


@pytest.fixture
def missing_model(monkeypatch: pytest.MonkeyPatch):
    # spacy.cli re-exports a `download` function that shadows the module name.
    download_module = importlib.import_module("spacy.cli.download")
    monkeypatch.setattr(spacy.util, "is_package", lambda name: False)
    monkeypatch.setattr(download_module, "get_compatibility", lambda: {})
    monkeypatch.setattr(download_module, "get_version", lambda name, comp: "3.8.0")


def test_installed_model_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spacy.util, "is_package", lambda name: True)
    with mock.patch.object(subprocess, "run") as run:
        kokoro_tts._ensure_spacy_english()
    run.assert_not_called()


def test_installs_into_this_interpreter_with_uv(
    missing_model, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(kokoro_tts.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(kokoro_tts.shutil, "which", lambda name: "/usr/bin/uv")
    ok = subprocess.CompletedProcess([], 0, "", "")
    with mock.patch.object(subprocess, "run", return_value=ok) as run:
        kokoro_tts._ensure_spacy_english()
    argv = run.call_args.args[0]
    assert argv[:5] == ["uv", "pip", "install", "--python", sys.executable]
    assert "--no-deps" in argv
    assert argv[-1].endswith("en_core_web_sm-3.8.0-py3-none-any.whl")
    # Output is captured so it cannot print over a full-screen UI.
    assert run.call_args.kwargs["capture_output"] is True


def test_prefers_pip_when_available(missing_model, monkeypatch) -> None:
    monkeypatch.setattr(kokoro_tts.importlib.util, "find_spec", lambda name: object())
    ok = subprocess.CompletedProcess([], 0, "", "")
    with mock.patch.object(subprocess, "run", return_value=ok) as run:
        kokoro_tts._ensure_spacy_english()
    assert run.call_args.args[0][:4] == [sys.executable, "-m", "pip", "install"]


def test_failed_install_raises(missing_model, monkeypatch) -> None:
    monkeypatch.setattr(kokoro_tts.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(kokoro_tts.shutil, "which", lambda name: "/usr/bin/uv")
    failed = subprocess.CompletedProcess([], 1, "", "error: network unreachable")
    with mock.patch.object(subprocess, "run", return_value=failed):
        with pytest.raises(RuntimeError, match="network unreachable"):
            kokoro_tts._ensure_spacy_english()


def test_no_installer_raises(missing_model, monkeypatch) -> None:
    monkeypatch.setattr(kokoro_tts.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(kokoro_tts.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="spacy download en_core_web_sm"):
        kokoro_tts._ensure_spacy_english()
