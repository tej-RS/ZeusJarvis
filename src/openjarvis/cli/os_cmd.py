"""``jarvis os`` — J.A.R.V.I.S. OS, a full-screen terminal HUD."""

from __future__ import annotations

import sys

import click


@click.command("os")
@click.option(
    "-m", "--model", default=None, help="Model to use (default: from config)."
)
@click.option(
    "--voice/--no-voice",
    default=None,
    help="Speak replies aloud. Default: on, or as you last left it with Ctrl+O.",
)
@click.option("--fast", is_flag=True, default=False, help="Skip the boot animation.")
def os_cmd(model: str | None, voice: bool | None, fast: bool) -> None:
    """Launch J.A.R.V.I.S. OS: chat, live diagnostics and your shell in one HUD.

    \b
    Inside the HUD:
      <message>        talk to JARVIS
      !<command>       run a shell command (editors and REPLs get the full terminal)
      /open <app|url>  open an app, website or file
      /help            all commands
      Ctrl+T talk · Ctrl+O voice on/off · Esc stop · Ctrl+Q shut down
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise click.ClickException("J.A.R.V.I.S. OS needs an interactive terminal.")
    try:
        import psutil  # noqa: F401
        import textual  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "J.A.R.V.I.S. OS needs the 'hud' extra. "
            "Install it with: uv sync --extra hud"
        )

    from openjarvis.hud import prefs
    from openjarvis.hud.app import JarvisOS

    if voice is None:
        voice = prefs.voice_default()
    app = JarvisOS(model=model, voice=voice, fast_boot=fast)
    try:
        app.run()
    finally:
        app.cleanup()


__all__ = ["os_cmd"]
