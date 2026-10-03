"""macOS control tools."""

from __future__ import annotations

import subprocess
import sys

import pytest

from openjarvis.tools import mac_control
from openjarvis.tools.mac_control import (
    AppleScriptTool,
    MacAppsTool,
    MacMediaTool,
    MacSystemTool,
    match_app,
)

LSAPPINFO = """\
 1) "loginwindow" ASN:0x0-0x2002:
    pid = 413 type="UIElement" flavor=3
 2) "Google Chrome" ASN:0x0-0x10010:
    pid = 687 type="Foreground" flavor=3
 3) "universalaccessd" ASN:0x0-0x8008:
    pid = 641 !signalled type="BackgroundOnly" flavor=3
 4) "Spotify" ASN:0x0-0x10011:
    pid = 690 type="Foreground" flavor=3
"""


class Recorder:
    """Stands in for mac_control._run; records calls, replies by command."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    def __call__(self, argv, *, stdin=None, timeout=30):
        self.calls.append((argv, stdin))
        out = ""
        for key, value in self.replies.items():
            if key in argv:
                out = value
        return subprocess.CompletedProcess(argv, 0, out, "")


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(mac_control.sys, "platform", "darwin")
    recorder = Recorder({"list": LSAPPINFO})
    monkeypatch.setattr(mac_control, "_run", recorder)
    return recorder


def test_foreground_apps_skips_background_processes(mac) -> None:
    assert mac_control.foreground_apps() == ["Google Chrome", "Spotify"]


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [("chrome", "Google Chrome"), ("SPOTIFY", "Spotify"), ("Spotify.app", "Spotify"),
     ("spotfy", "Spotify"), ("excel", None), ("", None)],
)  # fmt: skip
def test_match_app(spoken, expected) -> None:
    assert match_app(spoken, ["Google Chrome", "Spotify", "Safari"]) == expected


def test_quit_passes_app_name_as_argument(mac) -> None:
    result = MacAppsTool().execute(action="quit", app="chrome")
    argv = mac.calls[-1][0]
    assert argv[0] == "osascript"
    assert argv[-2:] == ["--", "Google Chrome"]  # resolved, and never in the source
    assert not any("Google Chrome" in part for part in argv[:-1])
    assert result.success


def test_open_falls_back_to_installed_name(mac, monkeypatch) -> None:
    def fake_run(argv, *, stdin=None, timeout=30):
        mac.calls.append((argv, stdin))
        code = 0 if argv == ["open", "-a", "Google Chrome"] else 1
        return subprocess.CompletedProcess(argv, code, "", "")

    monkeypatch.setattr(mac_control, "_run", fake_run)
    monkeypatch.setattr(
        mac_control, "installed_apps", lambda: ["Google Chrome", "Safari"]
    )
    result = MacAppsTool().execute(action="open", app="chrome")
    assert result.success and result.content == "Opened Google Chrome."


def test_open_unknown_app_fails_cleanly(monkeypatch) -> None:
    monkeypatch.setattr(mac_control.sys, "platform", "darwin")
    monkeypatch.setattr(
        mac_control,
        "_run",
        lambda argv, **_: subprocess.CompletedProcess(argv, 1, "", ""),
    )
    monkeypatch.setattr(mac_control, "installed_apps", lambda: ["Safari"])
    result = MacAppsTool().execute(action="open", app="Nonexistent App")
    assert not result.success


def test_media_auto_prefers_running_player(mac) -> None:
    MacMediaTool().execute(action="toggle")
    script = " ".join(mac.calls[-1][0])
    assert 'tell application "Spotify" to playpause' in script


def test_media_rejects_unknown_player(mac) -> None:
    assert not MacMediaTool().execute(action="play", player="Winamp").success


def test_set_volume_clamps_and_uses_argument(mac) -> None:
    result = MacSystemTool().execute(action="set_volume", level=250)
    assert mac.calls[-1][0][-2:] == ["--", "100"]
    assert result.content == "Volume set to 100%."


def test_notify_passes_text_as_arguments(mac) -> None:
    MacSystemTool().execute(action="notify", message='-say "hi"', title="Test")
    assert mac.calls[-1][0][-3:] == ["--", '-say "hi"', "Test"]


def test_applescript_asks_first_and_uses_stdin(mac) -> None:
    tool = AppleScriptTool()
    assert tool.spec.requires_confirmation
    tool.execute(script='tell application "Finder" to activate')
    argv, stdin = mac.calls[-1]
    assert argv == ["osascript", "-"]
    assert stdin == 'tell application "Finder" to activate'


def test_authorization_error_is_explained(monkeypatch) -> None:
    monkeypatch.setattr(mac_control.sys, "platform", "darwin")
    monkeypatch.setattr(
        mac_control,
        "_run",
        lambda argv, **_: subprocess.CompletedProcess(
            argv, 1, "", "execution error: Not authorized to send Apple events (-1743)"
        ),
    )
    result = AppleScriptTool().execute(script='tell application "Mail" to activate')
    assert not result.success and "Automation" in result.content


def test_other_platforms_are_refused(monkeypatch) -> None:
    monkeypatch.setattr(mac_control.sys, "platform", "linux")
    assert not MacAppsTool().execute(action="list_running").success


mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


@mac_only
def test_real_read_only_queries() -> None:
    running = MacAppsTool().execute(action="list_running")
    assert running.success and "Finder" in running.content
    assert MacAppsTool().execute(action="frontmost").success
    volume = MacSystemTool().execute(action="get_volume")
    assert volume.success and volume.content.startswith("Volume is ")


def test_timeout_mentions_permission_dialog(monkeypatch) -> None:
    monkeypatch.setattr(mac_control.sys, "platform", "darwin")

    def hang(argv, **_):
        if argv[0] == "lsappinfo":
            return subprocess.CompletedProcess(argv, 0, LSAPPINFO, "")
        raise subprocess.TimeoutExpired(argv, 25)

    monkeypatch.setattr(mac_control, "_run", hang)
    result = MacMediaTool().execute(action="now_playing")
    assert not result.success and "dialog" in result.content
