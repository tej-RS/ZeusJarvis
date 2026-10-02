from __future__ import annotations

import pytest

from openjarvis.hud.sysinfo import (
    format_duration,
    format_rate,
    sparkline,
    system_report,
)


def test_format_rate() -> None:
    assert format_rate(0) == "0 B/s"
    assert format_rate(512) == "512 B/s"
    assert format_rate(1536) == "1.5 KB/s"
    assert format_rate(5 * 1024**2) == "5.0 MB/s"
    assert format_rate(3 * 1024**4) == "3072.0 GB/s"


def test_format_duration() -> None:
    assert format_duration(59) == "0m"
    assert format_duration(3 * 3600 + 120) == "3h 2m"
    assert format_duration(2 * 86400 + 3600) == "2d 1h 0m"


def test_sparkline() -> None:
    assert sparkline([], 10) == ""
    assert sparkline([0, 50, 100], 10, ceiling=100) == "▁▅█"
    assert len(sparkline(list(range(100)), 8)) == 8


def test_system_report_has_basics() -> None:
    labels = [label for label, _ in system_report()]
    assert labels[:2] == ["Host", "OS"]
    assert "Python" in labels


def test_monitor_samples() -> None:
    pytest.importorskip("psutil")
    from openjarvis.hud.sysinfo import SystemMonitor

    monitor = SystemMonitor()
    snap = monitor.sample()
    assert 0 <= snap.cpu_percent <= 100
    assert 0 < snap.mem_total_gb
    assert snap.processes > 0
    assert len(monitor.cpu_history) == 1
