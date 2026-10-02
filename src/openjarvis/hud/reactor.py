"""Animated arc-reactor core, drawn with braille characters.

Each terminal cell holds a 2x4 grid of braille dots, so a ``cols`` x ``rows``
canvas is a ``2*cols`` x ``4*rows`` bitmap. Terminal cells are roughly twice
as tall as they are wide, which makes those dots close to square: a canvas
with ``cols == 2 * rows`` draws a round reactor.
"""

from __future__ import annotations

import math
from functools import lru_cache

from rich.style import Style
from rich.text import Text

# Bit for the braille dot at [row][col] inside one cell (Unicode U+2800 block).
_DOT_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))

# Shade levels, painted per cell (a cell takes its brightest dot's level).
_DIM, _MID, _BRIGHT, _CORE = 1, 2, 3, 4

# Per state: colours for (dim, mid, bright, core).
PALETTES: dict[str, tuple[str, str, str, str]] = {
    "idle": ("#0f6f86", "#1fb6d4", "#2ee6ff", "#e8fdff"),
    "thinking": ("#7a5310", "#c9861d", "#ffb43a", "#fff1d6"),
    "speaking": ("#0f6f86", "#1fb6d4", "#5ff0ff", "#ffffff"),
    "listening": ("#11704f", "#22b07d", "#3df5b0", "#eafff6"),
    "offline": ("#5a1f27", "#8a2f3a", "#ff4d5e", "#ffd6da"),
}

# Per state: angular speed in degrees/second for (ticks, dashes, scanner, coils).
_SPEEDS: dict[str, tuple[float, float, float, float]] = {
    "idle": (6, -18, 90, -12),
    "thinking": (12, -60, 320, -70),
    "speaking": (8, -30, 150, -20),
    "listening": (8, -24, 120, -16),
    "offline": (2, -4, 20, -3),
}

# Per state: core pulse frequency in Hz.
_PULSE_HZ = {
    "idle": 0.3,
    "thinking": 1.0,
    "speaking": 2.6,
    "listening": 0.8,
    "offline": 0.15,
}


@lru_cache(maxsize=8)
def _polar_grid(cols: int, rows: int) -> tuple[tuple[float, float], ...]:
    """(radius as a fraction of the canvas, angle in degrees) for every dot."""
    width, height = cols * 2, rows * 4
    cx, cy = (width - 1) / 2, (height - 1) / 2
    radius = max(min(cx, cy), 1.0)
    return tuple(
        (
            math.hypot(x - cx, y - cy) / radius,
            math.degrees(math.atan2(y - cy, x - cx)) % 360,
        )
        for y in range(height)
        for x in range(width)
    )


def _dot_level(
    r: float, theta: float, angles: tuple[float, float, float, float], core_r: float
) -> int:
    """Shade of the dot at polar position (r, theta), or 0 for empty."""
    ticks, dashes, scanner, coils = angles
    if r <= core_r:
        return _CORE
    if 0.30 <= r <= 0.34:
        return _DIM
    if 0.42 <= r <= 0.54:
        # Ten coil segments with small gaps.
        return _MID if (theta - coils) % 36 < 27 else 0
    if 0.62 <= r <= 0.66:
        return _DIM if (theta - dashes) % 12 < 5 else 0
    if 0.74 <= r <= 0.82:
        # Two scanner arcs, 60 degrees each, opposite one another.
        return _BRIGHT if (theta - scanner) % 180 < 60 else 0
    if 0.90 <= r <= 1.0:
        if (theta - ticks) % 30 < 2.5:
            return _BRIGHT
        if r >= 0.95 and (theta - ticks) % 7.5 < 1.6:
            return _DIM
    return 0


def render_reactor(
    t: float, state: str = "idle", cols: int = 22, rows: int = 11
) -> Text:
    """Render one animation frame at time ``t`` (seconds) as Rich ``Text``."""
    cols, rows = max(cols, 2), max(rows, 1)
    dim, mid, bright, core = PALETTES.get(state, PALETTES["idle"])
    speeds = _SPEEDS.get(state, _SPEEDS["idle"])
    angles = tuple((speed * t) % 360 for speed in speeds)
    pulse = 0.5 + 0.5 * math.sin(2 * math.pi * _PULSE_HZ.get(state, 0.3) * t)
    core_r = 0.18 + 0.07 * pulse

    bits = [[0] * cols for _ in range(rows)]
    levels = [[0] * cols for _ in range(rows)]
    width = cols * 2
    for index, (r, theta) in enumerate(_polar_grid(cols, rows)):
        level = _dot_level(r, theta, angles, core_r)  # type: ignore[arg-type]
        if not level:
            continue
        y, x = divmod(index, width)
        row, dot_row = divmod(y, 4)
        col, dot_col = divmod(x, 2)
        bits[row][col] |= _DOT_BITS[dot_row][dot_col]
        if level > levels[row][col]:
            levels[row][col] = level

    styles = {
        _DIM: Style(color=dim),
        _MID: Style(color=mid),
        _BRIGHT: Style(color=bright, bold=True),
        _CORE: Style(color=core, bold=True),
    }
    text = Text(no_wrap=True, overflow="crop")
    for row in range(rows):
        for col in range(cols):
            cell = bits[row][col]
            if cell:
                text.append(chr(0x2800 + cell), styles[levels[row][col]])
            else:
                text.append(" ")
        if row < rows - 1:
            text.append("\n")
    return text


__all__ = ["PALETTES", "render_reactor"]
