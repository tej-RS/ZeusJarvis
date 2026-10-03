"""J.A.R.V.I.S. OS: the Textual app behind ``jarvis os``.

Layout::

    header   status · model · working directory · clock
    left     DIAGNOSTICS  live CPU / memory / disk / power / network
    centre   COMMS        conversation, tool activity, shell output
    right    CORE         animated reactor, quick reference, activity log
    bottom   prompt       talk to JARVIS, !shell commands, /commands

Blocking work (the agent, shell commands, voice) runs in worker threads and
reports back with ``call_from_thread``.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from rich.align import Align
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Center, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.suggester import SuggestFromList
from textual.widget import Widget
from textual.widgets import Button, Footer, Input, RichLog, Static

from openjarvis.hud.commands import (
    COMMANDS,
    SLASH_NAMES,
    display_path,
    is_interactive,
    open_command,
    parse_input,
    resolve_directory,
)
from openjarvis.hud.reactor import render_reactor
from openjarvis.hud.session import BootStep, JarvisSession
from openjarvis.hud.sysinfo import (
    SystemMonitor,
    format_duration,
    format_rate,
    sparkline,
    system_report,
)

CYAN = "#2ee6ff"
CYAN_DIM = "#1aa3bf"
LINE = "#0f4a5c"
TEXT = "#d7f4ff"
TEXT_2 = "#86b9cf"
TEXT_3 = "#5b8ca3"
AMBER = "#ffb43a"
GREEN = "#3df5b0"
RED = "#ff4d5e"

STATES: dict[str, tuple[str, str]] = {
    "idle": ("ONLINE", CYAN),
    "thinking": ("PROCESSING", AMBER),
    "speaking": ("SPEAKING", CYAN),
    "listening": ("LISTENING", GREEN),
    "offline": ("OFFLINE", RED),
}

BOOT_STEPS = 9  # checks JarvisSession.boot reports on a healthy start

# "ANSI Shadow" letterforms for the boot logo.
_GLYPHS = {
    "J": ["     ██╗", "     ██║", "     ██║", "██   ██║", "╚█████╔╝", " ╚════╝ "],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "V": ["██╗   ██╗", "██║   ██║", "██║   ██║", "╚██╗ ██╔╝", " ╚████╔╝ ", "  ╚═══╝  "],
    "I": ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
    ".": ["   ", "   ", "   ", "   ", "██╗", "╚═╝"],
}
_LOGO_WORD = "J.A.R.V.I.S."
_LOGO_GRADIENT = ("#c8faff", "#8cf3ff", "#2ee6ff", "#1fc4e6", "#169fc0", "#0f7a96")
LOGO_WIDTH = sum(len(_GLYPHS[ch][0]) for ch in _LOGO_WORD)


def logo(width: int) -> Text:
    """The boot logo, or a one-line wordmark when the terminal is narrow."""
    if width < LOGO_WIDTH + 4:
        return Text("J . A . R . V . I . S .", style=f"bold {CYAN}", justify="center")
    text = Text(justify="center")
    for row, colour in enumerate(_LOGO_GRADIENT):
        line = "".join(_GLYPHS[ch][row] for ch in _LOGO_WORD)
        text.append(
            line + ("\n" if row < len(_LOGO_GRADIENT) - 1 else ""),
            style=f"bold {colour}",
        )
    return text


def greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def speakable(markdown: str) -> str:
    """Plain text for text-to-speech: no code blocks, links or markup."""
    text = re.sub(r"```.*?```", " (code omitted) ", markdown, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(#+|[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>|]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _bar(percent: float, width: int) -> Text:
    colour = CYAN if percent < 60 else AMBER if percent < 85 else RED
    filled = max(0, min(width, round(percent / 100 * width)))
    return Text.assemble(("█" * filled, colour), ("░" * (width - filled), LINE))


# ---------------------------------------------------------------- widgets


class HudHeader(Widget):
    """One-line status bar."""

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh)

    def render(self) -> RenderableType:
        app: JarvisOS = self.app  # type: ignore[assignment]
        label, colour = STATES.get(app.state, STATES["idle"])
        now = datetime.now()
        left = Text.assemble(
            (" ◉ J.A.R.V.I.S. OS ", f"bold {CYAN}"),
            ("  ", ""),
            (f"● {label}", f"bold {colour}"),
        )
        if app.session.model:
            left.append("   CORE ", style=TEXT_3)
            left.append(app.session.model, style=TEXT_2)
        right = Text.assemble(
            (display_path(app.cwd), TEXT_3),
            ("   ", ""),
            (now.strftime("%H:%M:%S"), f"bold {CYAN}"),
            (" · " + now.strftime("%a %d %b").upper() + " ", TEXT_2),
        )
        gap = self.size.width - left.cell_len - right.cell_len
        if gap < 1:
            return left
        return Text.assemble(left, " " * gap, right)


class Diagnostics(Widget):
    """Live system readings, refreshed every second."""

    def on_mount(self) -> None:
        self.border_title = "DIAGNOSTICS"
        try:
            self.monitor: Optional[SystemMonitor] = SystemMonitor()
        except ImportError:
            self.monitor = None
        self.snapshot = self.monitor.sample() if self.monitor else None
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        if self.monitor is not None:
            self.snapshot = self.monitor.sample()
        self.refresh()

    def render(self) -> RenderableType:
        snap = self.snapshot
        if snap is None or self.monitor is None:
            return Text("psutil not installed\nuv sync --extra hud", style=TEXT_3)
        width = max(self.content_size.width, 16)
        bar_width = max(width - 10, 4)

        def label(name: str) -> Text:
            return Text(f"{name:<5}", style=TEXT_3)

        def pct(value: float) -> Text:
            return Text(f" {value:>3.0f}%", style=f"bold {TEXT}")

        indent = Text(" " * 5)
        rows: list[Text] = [
            Text.assemble(
                label("CPU"), _bar(snap.cpu_percent, bar_width), pct(snap.cpu_percent)
            ),
            Text.assemble(
                indent,
                (sparkline(self.monitor.cpu_history, bar_width, ceiling=100), CYAN_DIM),
            ),
            Text(""),
            Text.assemble(
                label("MEM"), _bar(snap.mem_percent, bar_width), pct(snap.mem_percent)
            ),
            Text.assemble(
                indent, (f"{snap.mem_used_gb:.1f} / {snap.mem_total_gb:.0f} GB", TEXT_2)
            ),
            Text(""),
            Text.assemble(
                label("DISK"),
                _bar(snap.disk_percent, bar_width),
                pct(snap.disk_percent),
            ),
            Text.assemble(indent, (f"{snap.disk_free_gb:.0f} GB free", TEXT_2)),
            Text(""),
        ]
        if snap.battery_percent is not None:
            charging = Text(" ⚡" if snap.battery_plugged else "", style=AMBER)
            rows.append(
                Text.assemble(
                    label("PWR"),
                    # ⚡ is double-width; shorten the bar so the row still fits.
                    _battery_bar(snap.battery_percent, bar_width - charging.cell_len),
                    pct(snap.battery_percent),
                    charging,
                )
            )
        else:
            rows.append(Text.assemble(label("PWR"), ("AC power", TEXT_2)))
        rows += [
            Text(""),
            Text.assemble(
                label("NET"),
                ("↓ ", GREEN),
                (format_rate(snap.net_down_bps), TEXT),
                ("  ↑ ", AMBER),
                (format_rate(snap.net_up_bps), TEXT),
            ),
            Text.assemble(
                indent, (sparkline(self.monitor.net_history, bar_width), CYAN_DIM)
            ),
            Text(""),
            Text.assemble(label("UP"), (format_duration(snap.uptime_s), TEXT)),
        ]
        if snap.load_avg is not None:
            rows.append(
                Text.assemble(
                    label("LOAD"), (" ".join(f"{v:.2f}" for v in snap.load_avg), TEXT)
                )
            )
        rows.append(Text.assemble(label("PROC"), (str(snap.processes), TEXT)))
        return Text("\n").join(rows)


def _battery_bar(percent: float, width: int) -> Text:
    # Battery reads the other way round: low charge is the warning.
    colour = CYAN if percent > 40 else AMBER if percent > 15 else RED
    filled = max(0, min(width, round(percent / 100 * width)))
    return Text.assemble(("█" * filled, colour), ("░" * (width - filled), LINE))


class ReactorView(Widget):
    """The animated core, tinted by what JARVIS is doing."""

    def on_mount(self) -> None:
        self.border_title = "CORE"
        self._start = time.monotonic()
        self.set_interval(1 / 12, self.refresh)

    def render(self) -> RenderableType:
        app: JarvisOS = self.app  # type: ignore[assignment]
        width, height = self.content_size.width, self.content_size.height
        rows = max(min(height - 1, width // 2), 2)
        frame = render_reactor(
            time.monotonic() - self._start, app.state, rows * 2, rows
        )
        label, colour = STATES.get(app.state, STATES["idle"])
        return Group(
            Align.center(frame),
            Align.center(Text(f"● {label}", style=f"bold {colour}")),
        )


class JarvisReply(Static):
    """A reply from JARVIS, revealed with a quick typewriter effect."""

    def __init__(self, text: str, *, animate: bool = True) -> None:
        super().__init__(classes="msg jarvis")
        self.full_text = text
        self.shown = 0 if animate else len(text)
        self._timer: Any = None

    def on_mount(self) -> None:
        self._paint()
        if self.shown < len(self.full_text):
            self._timer = self.set_interval(0.03, self._advance)

    def _advance(self) -> None:
        self.shown = min(
            len(self.full_text), self.shown + max(4, len(self.full_text) // 40)
        )
        self._paint()
        if self.shown >= len(self.full_text) and self._timer is not None:
            self._timer.stop()
        if isinstance(self.parent, VerticalScroll):
            self.parent.scroll_end(animate=False)

    def _paint(self) -> None:
        cursor = "▌" if self.shown < len(self.full_text) else ""
        self.update(
            Group(
                Text("J.A.R.V.I.S. ▸", style=f"bold {CYAN}"),
                Markdown(self.full_text[: self.shown] + cursor, code_theme="monokai"),
            )
        )


class ShellBlock(Static):
    """Live output of a shell command."""

    MAX_LINES = 400

    def __init__(self, command: str, where: str) -> None:
        super().__init__(classes="msg shell")
        self.command = command
        self.where = where
        self.lines: list[Text] = []
        self.result: Optional[tuple[int, float]] = None

    def on_mount(self) -> None:
        self._paint()

    def append(self, lines: list[str]) -> None:
        self.lines.extend(Text.from_ansi(line) for line in lines)
        del self.lines[: -self.MAX_LINES]
        self._paint()

    def finish(self, code: int, elapsed: float) -> None:
        self.result = (code, elapsed)
        self._paint()

    def _paint(self) -> None:
        head = Text.assemble(
            ("$ ", f"bold {GREEN}"),
            (self.command, f"bold {TEXT}"),
            (f"   {self.where}", TEXT_3),
        )
        parts: list[RenderableType] = [head]
        if self.lines:
            parts.append(Text("\n").join(self.lines))
        if self.result is None:
            parts.append(Text("⟳ running… (Esc to stop)", style=AMBER))
        else:
            code, elapsed = self.result
            if not self.lines:
                parts.append(Text("(no output)", style=TEXT_3))
            colour = GREEN if code == 0 else RED
            parts.append(Text(f"exit {code} · {elapsed:.1f}s", style=colour))
        self.update(Group(*parts))


class Working(Static):
    """Spinner shown in COMMS while JARVIS is working on a reply."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        super().__init__(classes="msg working")
        self._start = time.monotonic()

    def on_mount(self) -> None:
        self._paint()
        self.set_interval(0.1, self._paint)

    def _paint(self) -> None:
        elapsed = time.monotonic() - self._start
        frame = self.FRAMES[int(elapsed * 10) % len(self.FRAMES)]
        self.update(
            Text.assemble(
                (f"{frame} ", f"bold {AMBER}"),
                ("Processing…", AMBER),
                (f"  {elapsed:.0f}s", TEXT_3),
            )
        )


class Comms(VerticalScroll):
    """The conversation panel."""

    def on_mount(self) -> None:
        self.border_title = "COMMS"

    def add(self, widget: Widget) -> Widget:
        self.mount(widget)
        self.call_after_refresh(self.scroll_end, animate=False)
        return widget

    def user(self, text: str) -> None:
        self.add(
            Static(
                Text.assemble(("YOU ▸ ", f"bold {TEXT_2}"), (text, TEXT)),
                classes="msg user",
            )
        )

    def jarvis(self, text: str, *, animate: bool = True) -> None:
        self.add(JarvisReply(text, animate=animate))

    def system(self, text: str, colour: str = TEXT_3) -> Static:
        return self.add(Static(Text(text, style=colour), classes="msg system"))  # type: ignore[return-value]

    def renderable(self, renderable: RenderableType) -> None:
        self.add(Static(renderable, classes="msg system"))


class ConfirmScreen(ModalScreen[bool]):
    """Asks before JARVIS runs a tool that needs permission."""

    BINDINGS = [
        Binding("y", "allow", "Allow"),
        Binding("n,escape", "deny", "Deny"),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.prompt = prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(
                Text("⚠  PERMISSION REQUIRED", style=f"bold {AMBER}"),
                id="confirm-title",
            )
            yield Static(Text(self.prompt, style=TEXT), id="confirm-body")
            with Horizontal(id="confirm-buttons"):
                yield Button("Allow  (y)", id="allow", variant="warning")
                yield Button("Deny  (n)", id="deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "allow")

    def action_allow(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)


# ---------------------------------------------------------------- screens


class BootScreen(Screen):
    """Boot sequence: each line is a real start-up check."""

    BINDINGS = [Binding("enter,space,escape", "continue", "Continue", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical(id="boot"):
            yield Static(id="boot-logo")
            yield Static(id="boot-tagline")
            # Centred on its own: Textual aligns a container's children as one
            # block, and the full-width rows around it would pin it left.
            with Center():
                yield Static(id="boot-log")
            yield Static(id="boot-progress")
            yield Static(id="boot-status")

    def on_mount(self) -> None:
        self.steps: list[BootStep] = []
        self.finished = False
        self.continue_requested = False
        self.query_one("#boot-logo", Static).update(logo(self.app.size.width))
        self.query_one("#boot-tagline", Static).update(
            Text(
                "JUST A RATHER VERY INTELLIGENT SYSTEM  ·  OS v1.0",
                style=TEXT_3,
                justify="center",
            )
        )
        self._paint_progress()
        self.app._spawn(self._boot)  # type: ignore[attr-defined]

    def _boot(self) -> None:
        app: JarvisOS = self.app  # type: ignore[assignment]

        def report(step: BootStep) -> None:
            app._post(self._add_step, step)
            if not app.fast_boot:
                time.sleep(0.12)

        try:
            online = app.session.boot(report)
        except Exception as exc:
            app._post(self._add_step, BootStep("Boot", "fail", str(exc)))
            online = False
        app._post(self._finish, online)

    def _add_step(self, step: BootStep) -> None:
        self.steps.append(step)
        lines = Text()
        for index, item in enumerate(self.steps):
            tag, colour = {"ok": ("  OK  ", GREEN), "warn": (" WARN ", AMBER)}.get(
                item.status, (" FAIL ", RED)
            )
            name = item.label.upper()
            lines.append("[", style=TEXT_3)
            lines.append(tag, style=f"bold {colour}")
            lines.append("] ", style=TEXT_3)
            lines.append(f"{name} ", style=TEXT)
            lines.append("·" * max(2, 22 - len(name)) + " ", style=LINE)
            lines.append(item.detail, style=TEXT_2)
            if index < len(self.steps) - 1:
                lines.append("\n")
        self.query_one("#boot-log", Static).update(lines)
        self._paint_progress()

    def _paint_progress(self, done: bool = False) -> None:
        fraction = 1.0 if done else min(len(self.steps) / BOOT_STEPS, 0.99)
        width = 44
        filled = round(fraction * width)
        self.query_one("#boot-progress", Static).update(
            Text.assemble(
                ("▕", TEXT_3),
                ("█" * filled, CYAN),
                ("░" * (width - filled), LINE),
                ("▏", TEXT_3),
                (f" {fraction * 100:3.0f}%", f"bold {TEXT}"),
                justify="center",
            )
        )

    def _finish(self, online: bool) -> None:
        app: JarvisOS = self.app  # type: ignore[assignment]
        self.finished = True
        self._paint_progress(done=True)
        status = self.query_one("#boot-status", Static)
        if online:
            status.update(
                Text.assemble(
                    ("ALL SYSTEMS ONLINE\n", f"bold {GREEN}"),
                    (f"{greeting()}.", f"italic {TEXT}"),
                    justify="center",
                )
            )
            delay = 0.1 if app.fast_boot or self.continue_requested else 1.0
            self.set_timer(delay, app.enter_hud)
        else:
            status.update(
                Text.assemble(
                    ("NEURAL CORE OFFLINE\n", f"bold {RED}"),
                    (
                        "Start Ollama (ollama serve) for chat. "
                        "Diagnostics and shell still work.\n",
                        TEXT_2,
                    ),
                    ("Press Enter to continue", f"bold {TEXT}"),
                    justify="center",
                )
            )

    def action_continue(self) -> None:
        if self.finished:
            self.app.enter_hud()  # type: ignore[attr-defined]
        else:
            self.continue_requested = True


class HudScreen(Screen):
    """The main HUD."""

    # On the screen, not the app: screen bindings win over Textual's defaults.
    BINDINGS = [Binding("escape", "app.stop", "Stop")]

    def compose(self) -> ComposeResult:
        yield HudHeader(id="header")
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Diagnostics(id="diagnostics", classes="panel")
            yield Comms(id="comms", classes="panel")
            with Vertical(id="right"):
                yield ReactorView(id="reactor", classes="panel")
                yield Static(_quick_reference(), id="modules", classes="panel")
                # min_width: RichLog otherwise lays lines out 78 columns wide
                # and crops them in this narrow column.
                yield RichLog(
                    id="activity",
                    classes="panel",
                    wrap=True,
                    max_lines=200,
                    min_width=10,
                )
        with Horizontal(id="prompt-bar"):
            yield Static("▸", id="prompt-glyph")
            yield Input(
                placeholder="Talk to JARVIS  ·  !command runs in your shell  ·  /help",
                id="prompt",
                suggester=SuggestFromList(
                    [f"/{name}" for name in SLASH_NAMES], case_sensitive=False
                ),
            )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#modules", Static).border_title = "MODULES"
        self.query_one("#activity", RichLog).border_title = "ACTIVITY"
        self.query_one("#prompt", Input).focus()
        self._fit(self.size.width)
        self.call_after_refresh(self.app.greet)  # type: ignore[attr-defined]

    def on_resize(self, event: Any) -> None:
        self._fit(event.size.width)

    def _fit(self, width: int) -> None:
        # Side panels give way on narrow terminals; the conversation stays.
        self.query_one("#right").display = width >= 112
        self.query_one("#left").display = width >= 84


def _quick_reference() -> Text:
    rows = (
        ("!cmd", "run in shell"),
        ("/open X", "app · url · file"),
        ("/sys", "system report"),
        ("/voice", "spoken replies"),
        ("^T", "talk (mic)"),
        ("/help", "everything else"),
    )
    text = Text()
    for index, (key, description) in enumerate(rows):
        text.append(f"{key:<9}", style=f"bold {CYAN}")
        text.append(description, style=TEXT_2)
        if index < len(rows) - 1:
            text.append("\n")
    return text


# ---------------------------------------------------------------- app


class _HudConsole:
    """Adapter so the chat voice helpers can print into COMMS."""

    def __init__(self, app: "JarvisOS") -> None:
        self._app = app

    def print(self, *objects: Any, **_: Any) -> None:
        message = " ".join(str(o) for o in objects)
        self._app.run_on_ui(self._app.system_message, Text.from_markup(message).plain)


def _start_resource_tracker() -> None:
    """Start multiprocessing's resource tracker while stderr is still real.

    Libraries create multiprocessing locks lazily (tqdm does when the Whisper
    and Kokoro models load). The first one launches the tracker process and
    hands it ``sys.stderr.fileno()``; under Textual, stderr is a capture
    object whose fileno() is -1, so the launch, and the model load with it,
    fails with "bad value(s) in fds_to_keep".
    """
    if sys.platform == "win32":
        return
    try:
        from multiprocessing import resource_tracker

        resource_tracker.ensure_running()
    except Exception:
        logging.getLogger(__name__).debug(
            "Could not start the resource tracker", exc_info=True
        )


class _ActivityStream:
    """Stream for log handlers while the HUD runs: lines go to ACTIVITY.

    Textual draws the HUD on the real stderr, so a handler still writing there
    would print over the screen. Lines are queued, not posted: a handler
    writes while holding its lock, and waiting on the UI thread from there
    could deadlock against a log call made on the UI thread.
    """

    def __init__(self, lines: "queue.SimpleQueue[tuple[str, str]]") -> None:
        self._lines = lines

    def write(self, text: str) -> int:
        for line in text.splitlines():
            line = line.strip()
            if line:
                colour = RED if line.startswith(("ERROR", "CRITICAL")) else AMBER
                self._lines.put((line[:160], colour))
        return len(text)

    def flush(self) -> None:
        pass


class JarvisOS(App):
    """J.A.R.V.I.S. OS."""

    CSS_PATH = "jarvis_os.tcss"
    TITLE = "J.A.R.V.I.S. OS"
    BINDINGS = [
        Binding("ctrl+t,f2", "listen", "Talk"),
        Binding("ctrl+o,f3", "toggle_voice", "Voice"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("f1", "help", "Help"),
        Binding("ctrl+q", "quit", "Shutdown", priority=True),
    ]

    state: reactive[str] = reactive("idle")

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        voice: bool = False,
        fast_boot: bool = False,
        session_factory: Callable[..., Any] = JarvisSession,
    ) -> None:
        super().__init__()
        _start_resource_tracker()  # before run() swaps out stderr
        self.fast_boot = fast_boot
        self.voice_enabled = voice
        self.cwd = Path.cwd()
        self.shell_path = os.environ.get("SHELL") or "/bin/sh"
        self.session = session_factory(
            model=model,
            confirm=self._confirm_from_worker,
            on_tool_event=self._tool_event_from_worker,
        )
        self.hud: Optional[HudScreen] = None
        self._ui_thread = threading.get_ident()
        self._request_id = 0
        self._cancelled: set[int] = set()
        self._busy = False
        self._shell_proc: Optional[subprocess.Popen] = None
        self._voice_session: Any = None
        self._pending_confirms: set[concurrent.futures.Future] = set()
        self._tool_line: Optional[Static] = None
        self._working: Optional[Working] = None
        self._voice_thread: Optional[threading.Thread] = None
        self._log_lines: "queue.SimpleQueue[tuple[str, str]]" = queue.SimpleQueue()
        self._diverted: list[tuple[logging.StreamHandler, Any]] = []
        self._closed = False

    # -- plumbing ---------------------------------------------------------

    def on_mount(self) -> None:
        self._ui_thread = threading.get_ident()
        self._divert_logs()
        self.set_interval(0.25, self._drain_logs)
        self.push_screen(BootScreen())

    def _divert_logs(self) -> None:
        """Point log handlers that write to the terminal at ACTIVITY instead."""
        sink = _ActivityStream(self._log_lines)
        loggers = [logging.getLogger()] + [
            logger
            for logger in logging.Logger.manager.loggerDict.values()
            if isinstance(logger, logging.Logger)
        ]
        for logger in loggers:
            for handler in logger.handlers:
                if (
                    isinstance(handler, logging.StreamHandler)
                    and not isinstance(handler, logging.FileHandler)
                    and handler.stream in (sys.__stderr__, sys.__stdout__)
                ):
                    self._diverted.append((handler, handler.setStream(sink)))

    def _restore_logs(self) -> None:
        for handler, stream in self._diverted:
            handler.setStream(stream)
        self._diverted.clear()

    def _drain_logs(self) -> None:
        if self.hud is None:
            return  # keep boot-time lines until the HUD can show them
        while True:
            try:
                line, colour = self._log_lines.get_nowait()
            except queue.Empty:
                return
            self.activity(line, colour)

    def _spawn(self, target: Callable[..., Any], *args: Any) -> threading.Thread:
        """Run blocking work on a daemon thread.

        Not Textual's thread workers: those use asyncio's default executor,
        which shutdown joins, so quitting mid-reply would wait for the model.
        """
        thread = threading.Thread(
            target=self._guarded, args=(target, *args), daemon=True
        )
        thread.start()
        return thread

    def _guarded(self, target: Callable[..., Any], *args: Any) -> None:
        try:
            target(*args)
        except Exception as exc:  # surface rather than die silently
            self._post(self.system_message, f"Internal error: {exc}", RED)

    def _post(self, callback: Callable[..., Any], *args: Any) -> Any:
        """From a worker thread: run ``callback`` on the UI thread and wait.

        Dropped quietly once the app has shut down.
        """
        try:
            return self.call_from_thread(callback, *args)
        except RuntimeError:
            return None

    def run_on_ui(self, callback: Callable[..., Any], *args: Any) -> Any:
        """Call ``callback`` on the UI thread from anywhere."""
        if threading.get_ident() == self._ui_thread:
            return callback(*args)
        return self._post(callback, *args)

    @property
    def comms(self) -> Comms:
        assert self.hud is not None
        return self.hud.query_one(Comms)

    def watch_state(self, _: str) -> None:
        if self.hud is not None:
            self.hud.query_one(HudHeader).refresh()

    def activity(self, message: str, colour: str = TEXT_2) -> None:
        if self.hud is None:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.hud.query_one("#activity", RichLog).write(
            Text.assemble((stamp + " ", TEXT_3), (message, colour))
        )

    def system_message(self, message: str, colour: str = TEXT_3) -> None:
        if self.hud is not None:
            self.comms.system(message, colour)

    def enter_hud(self) -> None:
        if self.hud is not None:
            return
        self.hud = HudScreen()
        self.switch_screen(self.hud)

    def greet(self) -> None:
        if self.session.online:
            self.state = "idle"
            text = f"{greeting()}. All systems are online — how may I help?"
            self.comms.jarvis(text)
            self.activity(f"core online · {self.session.model}", GREEN)
            if self.voice_enabled:
                self._speak(text)
        else:
            self.state = "offline"
            self.system_message(
                "Neural core offline. Start Ollama (ollama serve) "
                "and relaunch for chat. "
                "Shell commands and diagnostics still work.",
                RED,
            )
        self.system_message(
            "Type to talk · !command runs in your shell · /help for more · "
            "Ctrl+Q to shut down"
        )

    def on_unmount(self) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Release everything; safe to call more than once."""
        for future in list(self._pending_confirms):
            if not future.done():
                future.set_result(False)  # unblock a worker waiting on the dialog
        self._kill_shell()
        self._stop_audio()
        self._restore_logs()
        if not self._closed:
            self._closed = True
            try:
                self.session.close()
            except Exception:
                pass

    # -- input --------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        line = event.value
        event.input.value = ""
        command = parse_input(line)
        if command is None:
            return
        if command.kind == "chat":
            self.submit_chat(command.arg)
        elif command.kind == "shell":
            self.run_shell(command.arg)
        else:
            self.run_slash(command.name, command.arg)

    # -- chat -----------------------------------------------------------------

    def submit_chat(self, text: str) -> None:
        if not self.session.online:
            self.system_message("Neural core offline — chat unavailable.", RED)
            return
        if self._busy:
            self.system_message(
                "Still working on your last request — Esc to cancel it.", AMBER
            )
            return
        self._stop_audio()
        self.comms.user(text)
        self._working = self.comms.add(Working())  # type: ignore[assignment]
        self._busy = True
        self.state = "thinking"
        self._request_id += 1
        self.activity(f"query · {text[:32]}")
        self._spawn(self._ask, text, self._request_id)

    def _ask(self, text: str, request_id: int) -> None:
        started = time.monotonic()
        try:
            reply = self.session.ask(
                text, should_discard=lambda: request_id in self._cancelled
            )
        except Exception as exc:
            self._post(self._ask_failed, request_id, exc)
            return
        self._post(self._ask_done, request_id, reply, time.monotonic() - started)

    def _ask_done(self, request_id: int, reply: str, elapsed: float) -> None:
        if request_id in self._cancelled:
            self._cancelled.discard(request_id)  # the session already dropped it
            return
        self._busy = False
        self._tool_line = None
        self._clear_working()
        self.comms.jarvis(reply.strip() or "(no reply)")
        self.activity(f"reply · {len(reply)} chars · {elapsed:.1f}s", CYAN)
        if self.voice_enabled and reply.strip():
            self._speak(reply)
        else:
            self.state = "idle"

    def _ask_failed(self, request_id: int, exc: Exception) -> None:
        if request_id in self._cancelled:
            self._cancelled.discard(request_id)
            return
        self._busy = False
        self._clear_working()
        self.state = "idle" if self.session.online else "offline"
        self.system_message(f"Error: {exc}", RED)
        self.activity("error", RED)

    def _clear_working(self) -> None:
        if self._working is not None:
            self._working.remove()
            self._working = None

    # -- tools ----------------------------------------------------------------

    def _tool_event_from_worker(self, kind: str, data: dict) -> None:
        self.run_on_ui(self._tool_event, kind, data)

    def _tool_event(self, kind: str, data: dict) -> None:
        if self.hud is None:
            return
        name = str(data.get("tool", "tool"))
        if kind == "start":
            arguments = data.get("arguments") or {}
            summary = ""
            if isinstance(arguments, dict) and arguments:
                summary = str(next(iter(arguments.values())))
                summary = summary if len(summary) <= 60 else summary[:57] + "…"
            line = Text.assemble(
                ("  ⟳ ", AMBER),
                (name, f"bold {AMBER}"),
                (f"  {summary}" if summary else "", TEXT_2),
            )
            self._tool_line = self.comms.add(Static(line, classes="msg tool"))  # type: ignore[assignment]
            self.activity(f"tool · {name}", AMBER)
        else:
            ok = bool(data.get("success", True))
            latency = data.get("latency")
            took = f" · {latency:.1f}s" if isinstance(latency, (int, float)) else ""
            line = Text.assemble(
                ("  ✓ " if ok else "  ✗ ", GREEN if ok else RED),
                (name, f"bold {GREEN if ok else RED}"),
                (took if ok else f"{took} · failed", TEXT_3),
            )
            if self._tool_line is not None:
                self._tool_line.update(line)
                self._tool_line = None
            else:
                self.comms.add(Static(line, classes="msg tool"))

    def _confirm_from_worker(self, prompt: str) -> bool:
        if threading.get_ident() == self._ui_thread:
            return False  # cannot block the UI thread waiting for the user
        answer: concurrent.futures.Future[bool] = concurrent.futures.Future()
        self._pending_confirms.add(answer)

        def ask() -> None:
            self.push_screen(
                ConfirmScreen(prompt),
                callback=lambda allowed: answer.set_result(bool(allowed)),
            )

        self._post(ask)
        try:
            allowed = answer.result()
        finally:
            self._pending_confirms.discard(answer)
        self._post(
            self.activity,
            f"permission {'granted' if allowed else 'denied'}",
            GREEN if allowed else RED,
        )
        return allowed

    # -- shell ----------------------------------------------------------------

    def run_shell(self, command: str) -> None:
        if not command:
            self.system_message("Usage: !<command>   e.g. !ls -la", AMBER)
            return
        if command == "cd" or command.startswith("cd "):
            self.change_directory(command[2:].strip())
            return
        if is_interactive(command):
            self.run_in_terminal(command)
            return
        if self._shell_proc is not None:
            self.system_message("A command is still running — Esc stops it.", AMBER)
            return
        block = ShellBlock(command, display_path(self.cwd))
        self.comms.add(block)
        self.activity(f"$ {command[:34]}", GREEN)
        self._spawn(self._run_command, command, block)

    def _run_command(self, command: str, block: ShellBlock) -> None:
        started = time.monotonic()
        env = dict(
            os.environ,
            CLICOLOR_FORCE="1",
            FORCE_COLOR="1",
            PAGER="cat",
            GIT_PAGER="cat",
        )
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                executable=self.shell_path,
                cwd=self.cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self._post(block.append, [f"error: {exc}"])
            self._post(block.finish, 127, time.monotonic() - started)
            return
        self._shell_proc = proc
        pending: list[str] = []
        flushed = time.monotonic()
        assert proc.stdout is not None
        for line in proc.stdout:
            pending.append(line.rstrip("\n"))
            if time.monotonic() - flushed > 0.08:
                self._post(block.append, pending)
                pending, flushed = [], time.monotonic()
        code = proc.wait()
        self._shell_proc = None
        if pending:
            self._post(block.append, pending)
        self._post(block.finish, code, time.monotonic() - started)
        self._post(self._scroll_comms)

    def _scroll_comms(self) -> None:
        if self.hud is not None:
            self.comms.scroll_end(animate=False)

    def _kill_shell(self) -> bool:
        proc = self._shell_proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        return True

    def run_in_terminal(self, command: str, banner: str = "") -> None:
        """Hand the real terminal to an interactive program, then resume."""
        self.system_message(
            f"Handing the terminal to `{command}` — the HUD returns when it exits."
        )
        try:
            with self.suspend():
                if banner:
                    print(banner, flush=True)
                subprocess.run(
                    command, shell=True, executable=self.shell_path, cwd=self.cwd
                )
        except SuspendNotSupported:
            self.system_message(
                "This terminal can't hand over control; run it outside JARVIS OS.",
                AMBER,
            )
            return
        self.system_message("Welcome back.", CYAN)
        self.activity(f"↩ {command[:34]}")

    def change_directory(self, target: str) -> None:
        try:
            self.cwd = resolve_directory(target, self.cwd)
        except NotADirectoryError as exc:
            self.system_message(str(exc), RED)
            return
        self.system_message(f"Working directory: {display_path(self.cwd)}", CYAN)
        if self.hud is not None:
            self.hud.query_one(HudHeader).refresh()

    # -- slash commands ---------------------------------------------------------

    def run_slash(self, name: str, arg: str) -> None:
        handler = {
            "help": lambda: self.action_help(),
            "clear": lambda: self.action_clear(),
            "exit": lambda: self.exit(),
            "quit": lambda: self.exit(),
            "sys": lambda: self.show_system_report(),
            "time": lambda: self.tell_time(),
            "open": lambda: self.open_target(arg),
            "cd": lambda: self.change_directory(arg),
            "shell": lambda: self.run_in_terminal(
                self.shell_path,
                banner="J.A.R.V.I.S. OS · type 'exit' to return to the HUD",
            ),
            "voice": lambda: self.set_voice(arg),
            "listen": lambda: self.action_listen(),
            "model": lambda: self.switch_model(arg),
            "remember": lambda: self.remember(arg),
            "memory": lambda: self.show_memory(),
            "weather": lambda: self.submit_chat(
                f"What's the weather {'in ' + arg if arg else 'where I am'} "
                "right now? Search the web."
            ),
        }.get(name)
        if handler is None:
            self.system_message(f"Unknown command /{name} — try /help", AMBER)
            return
        handler()

    def tell_time(self) -> None:
        now = datetime.now()
        clock = now.strftime("%I:%M %p").lstrip("0")
        self.comms.jarvis(
            f"It's {clock} on {now.strftime('%A')}, {now.day} {now.strftime('%B %Y')}."
        )

    def show_system_report(self) -> None:
        table = Table(
            box=None,
            show_header=False,
            padding=(0, 2),
            title="SYSTEM REPORT",
            title_style=f"bold {CYAN}",
        )
        table.add_column(style=TEXT_3)
        table.add_column(style=TEXT)
        for label, value in system_report():
            table.add_row(label, value)
        table.add_row("", "")
        table.add_row(
            "Core",
            f"{self.session.model or 'offline'} via {self.session.engine_name or '—'}",
        )
        table.add_row("Agent", self.session.agent_name or "direct")
        table.add_row("Tools", ", ".join(self.session.tool_names) or "none")
        table.add_row("Voice", "on" if self.voice_enabled else "off")
        table.add_row("Directory", display_path(self.cwd))
        self.comms.renderable(table)

    def open_target(self, target: str) -> None:
        try:
            argv = open_command(target)
        except ValueError:
            self.system_message(
                "Usage: /open <app | url | path>   e.g. /open Safari", AMBER
            )
            return
        self._spawn(self._open, argv, target)

    def _open(self, argv: list[str], target: str) -> None:
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._post(self.system_message, f"Couldn't open {target}: {exc}", RED)
            return
        if result.returncode == 0:
            self._post(self.system_message, f"Opening {target}.", CYAN)
            self._post(self.activity, f"open · {target[:30]}")
        else:
            detail = (result.stderr or result.stdout).strip().splitlines()
            reason = detail[-1] if detail else f"exit {result.returncode}"
            self._post(self.system_message, f"Couldn't open {target}: {reason}", RED)

    def switch_model(self, name: str) -> None:
        models = self.session.list_models()
        if not name:
            if not models:
                self.system_message("No models available.", AMBER)
                return
            listing = Text("Models (/model <name> to switch):\n", style=TEXT_2)
            for model in models:
                current = model == self.session.model
                listing.append(
                    ("  ● " if current else "    ") + model + "\n",
                    style=f"bold {CYAN}" if current else TEXT,
                )
            self.comms.renderable(listing)
            return
        if models and name not in models:
            self.system_message(
                f"Model {name} isn't installed. Try: ollama pull {name}", AMBER
            )
            return
        try:
            self.session.set_model(name)
        except Exception as exc:
            self.system_message(f"Couldn't switch model: {exc}", RED)
            return
        self.system_message(f"Core switched to {name}.", CYAN)
        self.activity(f"core · {name}", CYAN)
        if self.hud is not None:
            self.hud.query_one(HudHeader).refresh()

    def remember(self, fact: str) -> None:
        if not fact:
            self.system_message(
                "Usage: /remember <fact>   e.g. /remember I prefer metric units", AMBER
            )
            return
        from openjarvis.tools.user_profile_manage import UserProfileManageTool

        result = UserProfileManageTool().execute(action="add", entry=fact)
        if not result.success:
            self.system_message(f"Couldn't save that: {result.content}", RED)
            return
        try:
            self.session.reload_persona()
        except Exception:
            pass
        self.comms.jarvis("Noted. I'll remember that.")
        self.activity("profile updated", GREEN)

    def show_memory(self) -> None:
        config = getattr(self.session, "config", None)
        files = getattr(config, "memory_files", None)
        if files is None:
            self.system_message("Memory files unavailable.", AMBER)
            return
        parts: list[str] = []
        for title, path in (
            ("Your profile", files.user_path),
            ("JARVIS's notes", files.memory_path),
        ):
            content = (
                Path(path).expanduser().read_text()
                if path and Path(path).expanduser().exists()
                else ""
            )
            body = "\n".join(
                line for line in content.splitlines() if not line.startswith("# ")
            ).strip()
            parts.append(f"**{title}**\n\n{body or '_(empty)_'}")
        self.comms.renderable(Markdown("\n\n".join(parts)))

    # -- voice ------------------------------------------------------------------

    def _get_voice_session(self) -> Any:
        if self._voice_session is None:
            from openjarvis.cli._voice_chat import VoiceSession

            self._voice_session = VoiceSession(getattr(self.session, "config", None))
        return self._voice_session

    def set_voice(self, arg: str) -> None:
        choice = arg.strip().lower()
        enabled = {"on": True, "off": False}.get(choice, not self.voice_enabled)
        self.voice_enabled = enabled
        if enabled:
            self.system_message("Voice output on — warming up the voice…", CYAN)
            self._spawn(self._warm_voice)
        else:
            self._stop_audio()
            self.system_message("Voice output off.", TEXT_2)
        self.activity(f"voice · {'on' if enabled else 'off'}")

    def _warm_voice(self) -> None:
        try:
            backend = self._get_voice_session().get_tts_backend()
        except Exception as exc:
            backend = None
            self._post(self.system_message, f"Voice unavailable: {exc}", RED)
        if backend is None:
            self._post(
                self.system_message,
                "No text-to-speech backend (uv sync --extra voice).",
                RED,
            )
            return
        self._post(
            self.system_message,
            f"Voice ready ({getattr(backend, 'backend_id', 'tts')}).",
            CYAN,
        )

    def _speak(self, text: str) -> None:
        self.state = "speaking"
        self._voice_thread = self._spawn(self._speak_worker, speakable(text))

    def _speak_worker(self, text: str) -> None:
        try:
            from openjarvis.cli._voice_chat import speak

            speak(text, _HudConsole(self), self._get_voice_session())
        except Exception as exc:
            self._post(self.system_message, f"Voice error: {exc}", RED)
        finally:
            self._post(self._audio_finished)

    def _audio_finished(self) -> None:
        if not self._busy and self.state in ("speaking", "listening"):
            self.state = "idle" if self.session.online else "offline"

    def _stop_audio(self) -> None:
        try:
            import sounddevice

            sounddevice.stop()
        except Exception:
            pass

    def _listen(self) -> None:
        try:
            text = self._record_and_transcribe()
        finally:
            self._post(self._audio_finished)
        if text:
            self._post(self.submit_chat, text)
        elif text is not None:
            self._post(self.system_message, "I didn't catch that.", TEXT_2)

    def _record_and_transcribe(self) -> Optional[str]:
        """Runs in a worker. Returns the transcript, or None after an error."""
        say = self.system_message
        try:
            self._post(say, "Loading speech recognition…")
            backend = self._get_voice_session().get_stt_backend()
            if backend is None:
                self._post(
                    say, "No speech-to-text backend (uv sync --extra voice).", RED
                )
                return None
            from openjarvis.speech.voice_io import record_until_silence

            self._post(say, "Listening… speak now; I'll stop when you pause.", GREEN)
            audio = record_until_silence()
            self._post(say, "Transcribing…")
            return backend.transcribe(audio, format="wav").text.strip()
        except Exception as exc:
            self._post(say, f"Microphone error: {exc}", RED)
            return None

    # -- actions ------------------------------------------------------------------

    def action_listen(self) -> None:
        if self.hud is None or self.screen is not self.hud:
            return
        if not self.session.online:
            self.system_message("Neural core offline — voice chat unavailable.", RED)
            return
        if self._busy:
            self.system_message("Still working on your last request.", AMBER)
            return
        self._stop_audio()
        self.state = "listening"
        self._voice_thread = self._spawn(self._listen)

    def action_toggle_voice(self) -> None:
        if self.hud is not None:
            self.set_voice("")

    def action_clear(self) -> None:
        if self.hud is None:
            return
        self.comms.remove_children()
        self.session.clear()
        self.system_message("Conversation cleared.", TEXT_2)

    def action_help(self) -> None:
        if self.hud is None:
            return
        table = Table(
            box=None,
            show_header=False,
            padding=(0, 2),
            title="COMMANDS",
            title_style=f"bold {CYAN}",
        )
        table.add_column(style=f"bold {CYAN}", no_wrap=True)
        table.add_column(style=TEXT_2)
        for usage, description in COMMANDS:
            table.add_row(escape(usage), description)
        table.add_row("", "")
        for keys, description in (
            ("Ctrl+T  / F2", "Talk to JARVIS (microphone)"),
            ("Ctrl+O  / F3", "Toggle spoken replies"),
            ("Ctrl+L", "Clear the conversation"),
            ("Esc", "Stop the current reply, command or speech"),
            ("Ctrl+Q", "Shut down"),
        ):
            table.add_row(keys, description)
        self.comms.renderable(table)

    def action_stop(self) -> None:
        stopped = False
        if self._busy:
            self._cancelled.add(self._request_id)
            self._busy = False
            self._tool_line = None
            self._clear_working()
            self.system_message("Request cancelled.", AMBER)
            stopped = True
        if self._kill_shell():
            self.system_message("Command stopped.", AMBER)
            stopped = True
        if self.state in ("speaking", "listening"):
            self._stop_audio()
            stopped = True
        if stopped:
            self.state = "idle" if self.session.online else "offline"


__all__ = ["JarvisOS"]
