"""macOS control tools: apps, media playback, system settings and AppleScript.

Values the model supplies (app names, messages, levels) reach AppleScript as
``on run argv`` arguments, never spliced into script source. The first time a
tool drives a given app, macOS asks the user to allow it (Privacy & Security
→ Automation); app listing, volume and notifications need no such grant.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_TIMEOUT = 30  # ToolSpec timeout: the executor gives up after this
_SCRIPT_TIMEOUT = 25  # shorter, so our explanation arrives first
_PLAYERS = ("Spotify", "Music")  # "auto" prefers the first one running


def _run(
    argv: List[str], *, stdin: Optional[str] = None, timeout: float = _TIMEOUT
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_PERMISSION_HINT = (
    "Timed out. macOS may be showing a dialog asking to allow control of this "
    "app; click OK, then try again."
)


def _osascript(lines: List[str], *args: str) -> subprocess.CompletedProcess:
    """Run AppleScript source ``lines``; ``args`` arrive as ``argv``."""
    argv = ["osascript"]
    for line in lines:
        argv += ["-e", line]
    # "--" so an argument starting with "-" isn't read as an osascript option.
    argv = [*argv, "--", *args] if args else argv
    try:
        return _run(argv, timeout=_SCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        # The first Apple Event to an app blocks on macOS's permission dialog.
        return subprocess.CompletedProcess(argv, 1, "", _PERMISSION_HINT)


def _explain(detail: str) -> str:
    if "-1743" in detail or "Not authorized" in detail:
        return (
            f"{detail} macOS blocked this: allow it in System Settings → Privacy "
            "& Security → Automation (macOS asks the first time)."
        )
    return detail or "failed"


def _not_mac(name: str) -> Optional[ToolResult]:
    if sys.platform == "darwin":
        return None
    return ToolResult(
        tool_name=name, content="This tool only works on macOS.", success=False
    )


def foreground_apps() -> List[str]:
    """Names of running apps with a Dock icon, from ``lsappinfo``."""
    apps: List[str] = []
    name: Optional[str] = None
    for line in _run(["lsappinfo", "list"]).stdout.splitlines():
        match = re.match(r'\s*\d+\) "(.+)" ASN:', line)
        if match:
            name = match.group(1)
        elif name and 'type="Foreground"' in line:
            apps.append(name)
            name = None
    return apps


def frontmost_app() -> str:
    asn = _run(["lsappinfo", "front"]).stdout.strip()
    info = _run(["lsappinfo", "info", "-only", "name", asn]).stdout if asn else ""
    match = re.search(r'"LSDisplayName"="(.*)"', info)
    return match.group(1) if match else ""


def installed_apps() -> List[str]:
    roots = [
        Path("/Applications"),
        Path("/System/Applications"),
        Path("/System/Applications/Utilities"),
        Path.home() / "Applications",
    ]
    names = set()
    for root in roots:
        if root.is_dir():
            names.update(p.stem for p in root.glob("*.app"))
    return sorted(names)


def match_app(name: str, candidates: List[str]) -> Optional[str]:
    """Resolve a spoken app name ("chrome") to a real one ("Google Chrome")."""
    wanted = name.strip().lower().removesuffix(".app")
    if not wanted:
        return None
    lowered = {c.lower(): c for c in candidates}
    if wanted in lowered:
        return lowered[wanted]
    contains = [c for c in candidates if wanted in c.lower()]
    if len(contains) == 1:
        return contains[0]
    close = difflib.get_close_matches(wanted, list(lowered), n=1, cutoff=0.75)
    return lowered[close[0]] if close else None


@ToolRegistry.register("mac_apps")
class MacAppsTool(BaseTool):
    """Open, quit and list Mac apps."""

    tool_id = "mac_apps"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mac_apps",
            description=(
                "Control apps on the user's Mac: open (launch or bring to the "
                "front), quit, list the running apps, or get the frontmost app."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "quit", "list_running", "frontmost"],
                    },
                    "app": {
                        "type": "string",
                        "description": "App name for open/quit, e.g. Safari, Spotify.",
                    },
                },
                "required": ["action"],
            },
            category="macos",
            timeout_seconds=_TIMEOUT,
        )

    def execute(self, **params: Any) -> ToolResult:
        if (blocked := _not_mac(self.spec.name)) is not None:
            return blocked
        action = params.get("action", "")
        app = str(params.get("app", "") or "").strip()
        if action == "list_running":
            apps = foreground_apps()
            return self._ok("Running apps: " + (", ".join(apps) or "none"))
        if action == "frontmost":
            return self._ok(f"Frontmost app: {frontmost_app() or 'unknown'}")
        if not app:
            return self._fail(f"Say which app to {action or 'use'}.")
        if action == "open":
            return self._open(app)
        if action == "quit":
            return self._quit(app)
        return self._fail(f"Unknown action: {action}")

    def _open(self, app: str) -> ToolResult:
        result = _run(["open", "-a", app])
        if result.returncode == 0:
            return self._ok(f"Opened {app}.")
        resolved = match_app(app, installed_apps())
        if resolved and resolved != app:
            retry = _run(["open", "-a", resolved])
            if retry.returncode == 0:
                return self._ok(f"Opened {resolved}.")
        return self._fail(f"Couldn't find an app called {app}.")

    def _quit(self, app: str) -> ToolResult:
        name = match_app(app, foreground_apps()) or app
        result = _osascript(
            [
                "on run argv",
                "set appName to item 1 of argv",
                "if application appName is running then",
                "tell application appName to quit",
                'return "quit"',
                "end if",
                'return "not running"',
                "end run",
            ],
            name,
        )
        if result.returncode != 0:
            return self._fail(_explain((result.stderr or result.stdout).strip()))
        if result.stdout.strip() == "not running":
            return self._ok(f"{name} isn't running.")
        return self._ok(f"Quit {name}.")

    def _ok(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, content=content, success=True)

    def _fail(self, content: str) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, content=content, success=False)


_MEDIA_COMMANDS = {
    "play": "play",
    "pause": "pause",
    "toggle": "playpause",
    "next": "next track",
    "previous": "previous track",
}


@ToolRegistry.register("mac_media")
class MacMediaTool(BaseTool):
    """Control Music or Spotify playback."""

    tool_id = "mac_media"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mac_media",
            description=(
                "Control music playback in Spotify or Apple Music: play, pause, "
                "toggle, next, previous, or now_playing to see the current track."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [*_MEDIA_COMMANDS, "now_playing"],
                    },
                    "player": {
                        "type": "string",
                        "enum": ["auto", *_PLAYERS],
                        "description": "Default auto: whichever is running.",
                    },
                },
                "required": ["action"],
            },
            category="macos",
            timeout_seconds=_TIMEOUT,
        )

    def execute(self, **params: Any) -> ToolResult:
        if (blocked := _not_mac(self.spec.name)) is not None:
            return blocked
        action = params.get("action", "")
        player = params.get("player") or "auto"
        if player == "auto":
            running = foreground_apps()
            player = next((p for p in _PLAYERS if p in running), None)
            if player is None:
                if action == "now_playing":
                    return self._done("Nothing is playing (no music app is open).")
                player = "Music"
        if player not in _PLAYERS:
            return self._done(f"Unsupported player: {player}", ok=False)

        # Terminology like "playpause" only compiles against a literal app
        # name; player comes from the fixed list above, never the model.
        if action == "now_playing":
            lines = [
                f'tell application "{player}"',
                'if player state is not playing then return "Nothing is playing."',
                "set t to current track",
                (
                    'return (name of t) & " — " & (artist of t)'
                    ' & " (" & (album of t) & ")"'
                ),
                "end tell",
            ]
        elif action in _MEDIA_COMMANDS:
            lines = [f'tell application "{player}" to {_MEDIA_COMMANDS[action]}']
        else:
            return self._done(f"Unknown action: {action}", ok=False)
        result = _osascript(lines)
        if result.returncode != 0:
            return self._done(
                _explain((result.stderr or result.stdout).strip()), ok=False
            )
        if action == "now_playing":
            return self._done(f"{player}: {result.stdout.strip()}")
        return self._done(f"{player}: {action} done.")

    def _done(self, content: str, *, ok: bool = True) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, content=content, success=ok)


@ToolRegistry.register("mac_system")
class MacSystemTool(BaseTool):
    """Volume, notifications, display sleep and dark mode."""

    tool_id = "mac_system"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="mac_system",
            description=(
                "Control the Mac: get_volume, set_volume (level 0-100), mute, "
                "unmute, notify (show a notification with message and optional "
                "title), sleep_display, or toggle_dark_mode."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "get_volume",
                            "set_volume",
                            "mute",
                            "unmute",
                            "notify",
                            "sleep_display",
                            "toggle_dark_mode",
                        ],
                    },
                    "level": {"type": "integer", "description": "Volume 0-100."},
                    "message": {"type": "string", "description": "Notification text."},
                    "title": {"type": "string", "description": "Notification title."},
                },
                "required": ["action"],
            },
            category="macos",
            timeout_seconds=_TIMEOUT,
        )

    def execute(self, **params: Any) -> ToolResult:
        if (blocked := _not_mac(self.spec.name)) is not None:
            return blocked
        action = params.get("action", "")
        if action == "get_volume":
            result = _osascript(
                [
                    "set s to get volume settings",
                    (
                        'return (output volume of s as text) & ","'
                        " & (output muted of s as text)"
                    ),
                ]
            )
            if result.returncode == 0:
                level, _, muted = result.stdout.strip().partition(",")
                state = " (muted)" if muted == "true" else ""
                return self._done(f"Volume is {level}%{state}.")
        elif action == "set_volume":
            try:
                level = max(0, min(100, int(params.get("level"))))
            except (TypeError, ValueError):
                return self._done("set_volume needs a level from 0 to 100.", ok=False)
            result = _osascript(
                [
                    "on run argv",
                    (
                        "set volume output volume (item 1 of argv as integer)"
                        " without output muted"
                    ),
                    "end run",
                ],
                str(level),
            )
            if result.returncode == 0:
                return self._done(f"Volume set to {level}%.")
        elif action in ("mute", "unmute"):
            word = "with" if action == "mute" else "without"
            result = _osascript([f"set volume {word} output muted"])
            if result.returncode == 0:
                return self._done("Muted." if action == "mute" else "Unmuted.")
        elif action == "notify":
            message = str(params.get("message") or "").strip()
            if not message:
                return self._done("notify needs a message.", ok=False)
            result = _osascript(
                [
                    "on run argv",
                    "display notification (item 1 of argv) with title (item 2 of argv)",
                    "end run",
                ],
                message,
                str(params.get("title") or "J.A.R.V.I.S."),
            )
            if result.returncode == 0:
                return self._done("Notification shown.")
        elif action == "sleep_display":
            result = _run(["pmset", "displaysleepnow"])
            if result.returncode == 0:
                return self._done("Display is going to sleep.")
        elif action == "toggle_dark_mode":
            result = _osascript(
                [
                    'tell application "System Events" to tell appearance preferences',
                    "set dark mode to not dark mode",
                    "return dark mode as text",
                    "end tell",
                ]
            )
            if result.returncode == 0:
                mode = "on" if result.stdout.strip() == "true" else "off"
                return self._done(f"Dark mode is now {mode}.")
        else:
            return self._done(f"Unknown action: {action}", ok=False)
        return self._done(_explain((result.stderr or result.stdout).strip()), ok=False)

    def _done(self, content: str, *, ok: bool = True) -> ToolResult:
        return ToolResult(tool_name=self.spec.name, content=content, success=ok)


@ToolRegistry.register("applescript")
class AppleScriptTool(BaseTool):
    """Run arbitrary AppleScript; always asks the user first."""

    tool_id = "applescript"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="applescript",
            description=(
                "Run AppleScript on the user's Mac to control apps the other mac_* "
                "tools don't cover: Finder, Safari tabs, Notes, Reminders, Calendar, "
                "Mail, Messages and more. Returns the script's result. The user "
                "approves each script before it runs."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "The AppleScript source to run.",
                    },
                },
                "required": ["script"],
            },
            category="macos",
            requires_confirmation=True,
            timeout_seconds=120,
        )

    def execute(self, **params: Any) -> ToolResult:
        if (blocked := _not_mac(self.spec.name)) is not None:
            return blocked
        script = str(params.get("script") or "")
        if not script.strip():
            return ToolResult(
                tool_name="applescript", content="No script given.", success=False
            )
        try:
            result = _run(["osascript", "-"], stdin=script, timeout=110)
        except subprocess.TimeoutExpired:
            return ToolResult(
                tool_name="applescript", content=_PERMISSION_HINT, success=False
            )
        if result.returncode != 0:
            return ToolResult(
                tool_name="applescript",
                content=_explain((result.stderr or result.stdout).strip()),
                success=False,
            )
        return ToolResult(
            tool_name="applescript",
            content=result.stdout.strip() or "Done.",
            success=True,
        )


__all__ = [
    "AppleScriptTool",
    "MacAppsTool",
    "MacMediaTool",
    "MacSystemTool",
    "foreground_apps",
    "frontmost_app",
    "match_app",
]
