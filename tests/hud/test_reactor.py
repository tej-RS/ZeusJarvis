from __future__ import annotations

from openjarvis.hud.reactor import PALETTES, render_reactor


def _cells(text) -> list[str]:
    return text.plain.split("\n")


def test_frame_has_requested_size() -> None:
    rows = _cells(render_reactor(0.0, "idle", cols=22, rows=11))
    assert len(rows) == 11
    assert all(len(row) == 22 for row in rows)


def test_frame_uses_only_braille_or_blank_cells() -> None:
    plain = render_reactor(1.0, "idle", cols=20, rows=10).plain.replace("\n", "")
    assert all(ch == " " or 0x2800 <= ord(ch) <= 0x28FF for ch in plain)


def test_core_is_filled() -> None:
    rows = _cells(render_reactor(0.0, "idle", cols=22, rows=11))
    assert rows[5][11] == "⣿"  # centre cell: every dot lit


def test_rings_rotate_over_time() -> None:
    assert render_reactor(0.0, "idle").plain != render_reactor(0.5, "idle").plain


def test_state_changes_colour() -> None:
    idle = {span.style.color.name for span in render_reactor(0.0, "idle").spans}
    busy = {span.style.color.name for span in render_reactor(0.0, "thinking").spans}
    assert PALETTES["thinking"][2] in busy
    assert idle != busy


def test_unknown_state_falls_back_to_idle() -> None:
    assert render_reactor(0.3, "nonsense").plain == render_reactor(0.3, "idle").plain


def test_tiny_canvas_does_not_crash() -> None:
    assert render_reactor(0.0, "idle", cols=1, rows=0).plain
