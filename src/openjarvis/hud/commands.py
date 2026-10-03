"""Input parsing and shell helpers for J.A.R.V.I.S. OS (no Textual imports)."""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Command:
    """One line of user input, classified."""

    kind: str  # "chat", "shell" or "slash"
    name: str = ""  # slash command name, lower-case, without the slash
    arg: str = ""


# (usage, description) for /help and the input suggester.
COMMANDS: tuple[tuple[str, str], ...] = (
    ("<message>", "Talk to JARVIS"),
    ("!<command>", "Run a shell command (also /run)"),
    ("/cd <dir>", "Change the working directory"),
    ("/shell", "Drop into your shell; type 'exit' to return"),
    ("/open <app|url|path>", "Open an app, website or file"),
    ("/sys", "Full system report"),
    ("/time", "Current time and date"),
    ("/weather [place]", "Ask JARVIS for the weather"),
    ("/voice [on|off]", "Spoken replies (F3)"),
    ("/wake [on|off]", 'Listen for "Hey JARVIS" (Ctrl+G)'),
    ("/listen", "Speak to JARVIS through the mic (F2)"),
    ("/model [name]", "Show or switch the AI model"),
    ("/remember <fact>", "Add a fact to your profile"),
    ("/memory", "Show what JARVIS knows about you"),
    ("/clear", "Clear the conversation (Ctrl+L)"),
    ("/help", "This list (F1)"),
    ("/exit", "Shut down J.A.R.V.I.S. OS (Ctrl+Q)"),
)

SLASH_NAMES: tuple[str, ...] = (
    "cd",
    "clear",
    "exit",
    "help",
    "listen",
    "memory",
    "model",
    "open",
    "quit",
    "remember",
    "run",
    "shell",
    "sys",
    "time",
    "voice",
    "wake",
    "weather",
)

# Full-screen or prompting programs that need the real terminal; JARVIS OS
# suspends itself while they run instead of capturing their output.
INTERACTIVE_PROGRAMS = frozenset(
    {
        "btop", "emacs", "fzf", "ftp", "htop", "less", "man", "more", "mysql",
        "nano", "nvim", "psql", "screen", "sftp", "ssh", "telnet", "tig", "tmux",
        "top", "vi", "vim", "watch",
    }
)  # fmt: skip
# REPLs are interactive only when started without a script or command.
REPL_PROGRAMS = frozenset(
    {
        "bash",
        "fish",
        "ipython",
        "irb",
        "node",
        "python",
        "python3",
        "sh",
        "sqlite3",
        "zsh",
    }
)

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def parse_input(line: str) -> Command | None:
    """Classify a line typed into the prompt; ``None`` for blank input."""
    text = line.strip()
    if not text:
        return None
    if text.startswith("!"):
        return Command("shell", "run", text[1:].strip())
    if text.startswith("/") and len(text) > 1 and not text.startswith("//"):
        name, _, arg = text[1:].partition(" ")
        name = name.lower()
        if name in ("run", "sh"):
            return Command("shell", "run", arg.strip())
        return Command("slash", name, arg.strip())
    return Command("chat", "", text)


def resolve_directory(target: str, cwd: Path) -> Path:
    """Resolve a ``cd`` target against ``cwd``; raises ``NotADirectoryError``."""
    raw = target.strip() or "~"
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        raw = raw[1:-1]
    path = Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = cwd / path
    path = path.resolve()
    if not path.is_dir():
        raise NotADirectoryError(f"No such directory: {target.strip() or '~'}")
    return path


def is_interactive(command: str) -> bool:
    """True if ``command`` needs the real terminal (an editor, pager, REPL...)."""
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    # Skip leading VAR=value assignments and common wrappers.
    while words and ("=" in words[0] and not words[0].startswith("=")):
        words = words[1:]
    if words and words[0] == "sudo":
        return True  # may prompt for a password
    while words and words[0] in ("exec", "command", "nohup", "time"):
        words = words[1:]
    if not words:
        return False
    program = os.path.basename(words[0])
    if program in INTERACTIVE_PROGRAMS:
        return True
    if program == "git":
        return _git_needs_terminal(words[1:])
    return program in REPL_PROGRAMS and len(words) == 1


def _git_needs_terminal(args: list[str]) -> bool:
    """git subcommands that open an editor or prompt per hunk."""
    if any(a in ("-i", "--interactive", "-p", "--patch") for a in args):
        return True
    if args[:1] == ["commit"]:
        for arg in args[1:]:
            if arg.startswith(("--message", "--file", "--no-edit", "--reuse-message")):
                return False
            # Short flags may be combined, as in `git commit -am "msg"`.
            if (
                arg.startswith("-")
                and not arg.startswith("--")
                and set(arg[1:]) & set("mFC")
            ):
                return False
        return True
    return False


def open_command(target: str) -> list[str]:
    """argv that opens an app (by name), URL or path with the OS launcher."""
    target = target.strip()
    if not target:
        raise ValueError("Nothing to open")
    if target.startswith("www."):
        target = "https://" + target
    is_url = bool(_URL_RE.match(target))
    is_path = not is_url and os.path.exists(os.path.expanduser(target))
    if sys.platform == "darwin":
        if is_url:
            return ["open", target]
        if is_path:
            return ["open", os.path.expanduser(target)]
        return ["open", "-a", target]
    # Linux and friends: xdg-open handles URLs and paths; apps by command name.
    if is_url or is_path:
        return ["xdg-open", os.path.expanduser(target) if is_path else target]
    return [target]


def display_path(path: Path) -> str:
    """Path with the home directory shortened to ``~``."""
    try:
        return "~/" + str(path.relative_to(Path.home())) if path != Path.home() else "~"
    except ValueError:
        return str(path)


__all__ = [
    "COMMANDS",
    "Command",
    "SLASH_NAMES",
    "display_path",
    "is_interactive",
    "open_command",
    "parse_input",
    "resolve_directory",
]
