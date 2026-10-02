from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openjarvis.hud.commands import (
    Command,
    display_path,
    is_interactive,
    open_command,
    parse_input,
    resolve_directory,
)


class TestParseInput:
    def test_blank(self) -> None:
        assert parse_input("   ") is None

    def test_chat(self) -> None:
        assert parse_input("  hello there ") == Command("chat", "", "hello there")

    def test_bang_runs_shell(self) -> None:
        assert parse_input("!ls -la") == Command("shell", "run", "ls -la")

    def test_run_alias(self) -> None:
        assert parse_input("/run git status") == Command("shell", "run", "git status")

    def test_slash_command_is_case_insensitive(self) -> None:
        assert parse_input("/OPEN Safari") == Command("slash", "open", "Safari")

    def test_bare_slash_is_chat(self) -> None:
        assert parse_input("/") == Command("chat", "", "/")

    def test_double_slash_is_chat(self) -> None:
        assert parse_input("//not a command").kind == "chat"


class TestResolveDirectory:
    def test_relative(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        assert resolve_directory("sub", tmp_path) == (tmp_path / "sub").resolve()

    def test_home_default(self, tmp_path: Path) -> None:
        assert resolve_directory("", tmp_path) == Path.home().resolve()

    def test_quoted(self, tmp_path: Path) -> None:
        (tmp_path / "with space").mkdir()
        assert resolve_directory('"with space"', tmp_path).name == "with space"

    def test_missing(self, tmp_path: Path) -> None:
        with pytest.raises(NotADirectoryError):
            resolve_directory("nope", tmp_path)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("vim notes.txt", True),
        ("sudo htop", True),
        ("sudo ls", True),
        ("EDITOR=vi git commit", True),
        ("git commit -m 'msg'", False),
        ("git commit -am 'msg'", False),
        ("git add -p", True),
        ("git status", False),
        ("python", True),
        ("python script.py", False),
        ("ls -la", False),
        ("ssh server", True),
        ("echo 'unbalanced", False),
    ],
)
def test_is_interactive(command: str, expected: bool) -> None:
    assert is_interactive(command) is expected


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
class TestOpenCommandMac:
    def test_app_name(self) -> None:
        assert open_command("Safari") == ["open", "-a", "Safari"]

    def test_url(self) -> None:
        assert open_command("https://example.com") == ["open", "https://example.com"]

    def test_www_gets_scheme(self) -> None:
        assert open_command("www.example.com") == ["open", "https://www.example.com"]

    def test_existing_path(self, tmp_path: Path) -> None:
        assert open_command(str(tmp_path)) == ["open", str(tmp_path)]


def test_open_command_needs_target() -> None:
    with pytest.raises(ValueError):
        open_command("  ")


def test_display_path_shortens_home() -> None:
    assert display_path(Path.home()) == "~"
    assert display_path(Path.home() / "code") == "~/code"
    assert display_path(Path("/tmp")) == "/tmp"
