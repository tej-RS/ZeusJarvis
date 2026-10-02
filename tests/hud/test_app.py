"""Drive J.A.R.V.I.S. OS headlessly against a fake session."""

from __future__ import annotations

import time
from functools import partial
from pathlib import Path
from typing import Callable

import pytest

pytest.importorskip("textual")
pytest.importorskip("psutil")

from textual.widgets import Input  # noqa: E402

from openjarvis.hud.app import (  # noqa: E402
    BootScreen,
    ConfirmScreen,
    JarvisOS,
    JarvisReply,
    ShellBlock,
    speakable,
)
from openjarvis.hud.session import BootStep  # noqa: E402


class FakeSession:
    def __init__(self, *, model=None, confirm=None, on_tool_event=None, online=True):
        self._online = online
        self.model = (model or "fake-model") if online else ""
        self.engine_name = "fake"
        self.agent_name = "orchestrator"
        self.tool_names = ["calculator"]
        self.config = None
        self.confirm = confirm
        self.on_tool_event = on_tool_event
        self.asked: list[str] = []
        self.discarded: list[str] = []
        self.closed = False

    @property
    def online(self) -> bool:
        return self._online

    def boot(self, report: Callable[[BootStep], None]) -> bool:
        report(BootStep("Configuration", "ok", "test"))
        report(BootStep("Neural core", "ok" if self._online else "fail", self.model))
        return self._online

    def ask(self, text: str, should_discard=None) -> str:
        self.asked.append(text)
        if text == "use a tool":
            self.on_tool_event(
                "start", {"tool": "calculator", "arguments": {"x": "6*7"}}
            )
            self.on_tool_event(
                "end", {"tool": "calculator", "success": True, "latency": 0.01}
            )
            return "It's 42."
        if text == "need permission":
            return "allowed" if self.confirm("Allow tool 'shell_exec'?") else "denied"
        if text == "slow":
            time.sleep(0.5)
            if should_discard and should_discard():
                self.discarded.append(text)
        return f"Echo: {text}"

    def clear(self) -> None:
        self.asked.clear()

    def list_models(self) -> list[str]:
        return ["fake-model", "other-model"]

    def set_model(self, model: str) -> None:
        self.model = model

    def reload_persona(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


async def _until(pilot, condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await pilot.pause(0.05)


def _replies(app: JarvisOS) -> list[str]:
    return [reply.full_text for reply in app.hud.query(JarvisReply)] if app.hud else []


async def _boot(pilot, app: JarvisOS) -> None:
    await _until(pilot, lambda: app.hud is not None and app.screen is app.hud)
    await _until(
        pilot, lambda: any("All systems are online" in r for r in _replies(app))
    )


async def _send(pilot, app: JarvisOS, text: str) -> None:
    prompt = app.hud.query_one("#prompt", Input)
    prompt.value = text
    await pilot.press("enter")


def _app(**kwargs) -> JarvisOS:
    return JarvisOS(fast_boot=True, session_factory=partial(FakeSession, **kwargs))


@pytest.mark.asyncio
async def test_boot_greets_and_chats() -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        assert app.state == "idle"
        await _send(pilot, app, "hello")
        await _until(pilot, lambda: "Echo: hello" in _replies(app))
        assert app.session.asked == ["hello"]
        assert app.state == "idle"


@pytest.mark.asyncio
async def test_shell_command_output_is_captured(tmp_path: Path) -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        app.cwd = tmp_path
        await _send(pilot, app, "!echo jarvis-os-test && pwd")
        await _until(pilot, lambda: any(b.result for b in app.hud.query(ShellBlock)))
        block = app.hud.query_one(ShellBlock)
        output = "\n".join(line.plain for line in block.lines)
        assert "jarvis-os-test" in output
        assert str(tmp_path.resolve()) in output
        assert block.result[0] == 0


@pytest.mark.asyncio
async def test_cd_changes_working_directory(tmp_path: Path) -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        await _send(pilot, app, f"/cd {tmp_path}")
        await pilot.pause()
        assert app.cwd == tmp_path.resolve()
        await _send(pilot, app, "!cd ..")
        await pilot.pause()
        assert app.cwd == tmp_path.resolve().parent


@pytest.mark.asyncio
async def test_tool_activity_is_shown() -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        await _send(pilot, app, "use a tool")
        await _until(pilot, lambda: "It's 42." in _replies(app))
        tool_lines = [str(w.render()) for w in app.hud.query(".tool")]
        assert any("calculator" in line and "✓" in line for line in tool_lines)


@pytest.mark.asyncio
async def test_permission_dialog_allows() -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        await _send(pilot, app, "need permission")
        await _until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("y")
        await _until(pilot, lambda: "allowed" in _replies(app))


@pytest.mark.asyncio
async def test_permission_dialog_denies() -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        await _send(pilot, app, "need permission")
        await _until(pilot, lambda: isinstance(app.screen, ConfirmScreen))
        await pilot.press("n")
        await _until(pilot, lambda: "denied" in _replies(app))


@pytest.mark.asyncio
async def test_escape_cancels_a_reply() -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        await _send(pilot, app, "slow")
        await _until(pilot, lambda: app.state == "thinking")
        await pilot.press("escape")
        assert app.state == "idle"
        await _until(pilot, lambda: app.session.discarded == ["slow"])
        await pilot.pause(0.2)
        assert "Echo: slow" not in _replies(app)


@pytest.mark.asyncio
async def test_offline_boot_still_opens_the_hud() -> None:
    app = _app(online=False)
    async with app.run_test(size=(140, 44)) as pilot:
        await _until(
            pilot, lambda: isinstance(app.screen, BootScreen) and app.screen.finished
        )
        await pilot.press("enter")
        await _until(pilot, lambda: app.hud is not None and app.screen is app.hud)
        await _until(pilot, lambda: app.state == "offline")
        await _send(pilot, app, "hello")
        await pilot.pause()
        assert app.session.asked == []


@pytest.mark.asyncio
async def test_slash_commands() -> None:
    app = _app()
    async with app.run_test(size=(140, 44)) as pilot:
        await _boot(pilot, app)
        before = len(app.hud.query(".msg"))
        await _send(pilot, app, "/help")
        await _send(pilot, app, "/sys")
        await _send(pilot, app, "/time")
        await pilot.pause()
        assert len(app.hud.query(".msg")) >= before + 3
        assert any(r.startswith("It's ") for r in _replies(app))
        await _send(pilot, app, "/model other-model")
        await pilot.pause()
        assert app.session.model == "other-model"
        await _send(pilot, app, "/nonsense")
        await pilot.pause()
        assert "Unknown command /nonsense" in str(
            list(app.hud.query(".system"))[-1].render()
        )


@pytest.mark.asyncio
async def test_narrow_terminal_hides_side_panels() -> None:
    app = _app()
    async with app.run_test(size=(80, 30)) as pilot:
        await _boot(pilot, app)
        assert app.hud.query_one("#left").display is False
        assert app.hud.query_one("#right").display is False


def test_speakable_strips_markdown() -> None:
    text = (
        "# Title\n\n**Bold** and `code` with [a link](http://x).\n```py\nprint(1)\n```"
    )
    assert speakable(text) == "Title Bold and code with a link. (code omitted)"
