"""Live system readings for the J.A.R.V.I.S. OS diagnostics panel."""

from __future__ import annotations

import os
import platform
import socket
import time
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    """One reading. Fields the platform cannot report are ``None``."""

    cpu_percent: float
    mem_percent: float
    mem_used_gb: float
    mem_total_gb: float
    disk_percent: float
    disk_free_gb: float
    battery_percent: float | None
    battery_plugged: bool | None
    net_down_bps: float
    net_up_bps: float
    uptime_s: float
    load_avg: tuple[float, float, float] | None
    processes: int


class SystemMonitor:
    """Samples psutil; keeps short histories for sparklines."""

    def __init__(self, history: int = 40) -> None:
        import psutil  # optional dependency (hud extra)

        self._psutil = psutil
        psutil.cpu_percent(interval=None)  # the first call primes the counter
        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        self.cpu_history: deque[float] = deque(maxlen=history)
        self.net_history: deque[float] = deque(maxlen=history)

    def sample(self) -> Snapshot:
        psutil = self._psutil
        now = time.monotonic()
        elapsed = max(now - self._last_time, 1e-3)
        net = psutil.net_io_counters()
        down = max(net.bytes_recv - self._last_net.bytes_recv, 0) / elapsed
        up = max(net.bytes_sent - self._last_net.bytes_sent, 0) / elapsed
        self._last_net, self._last_time = net, now

        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.expanduser("~"))
        try:
            battery = psutil.sensors_battery()
        except (AttributeError, NotImplementedError, RuntimeError):
            battery = None
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = None
        try:
            processes = len(psutil.pids())
        except Exception:
            processes = 0

        cpu = psutil.cpu_percent(interval=None)
        self.cpu_history.append(cpu)
        self.net_history.append(down + up)
        return Snapshot(
            cpu_percent=cpu,
            mem_percent=mem.percent,
            mem_used_gb=(mem.total - mem.available) / 1024**3,
            mem_total_gb=mem.total / 1024**3,
            disk_percent=disk.percent,
            disk_free_gb=disk.free / 1024**3,
            battery_percent=battery.percent if battery else None,
            battery_plugged=battery.power_plugged if battery else None,
            net_down_bps=down,
            net_up_bps=up,
            uptime_s=time.time() - psutil.boot_time(),
            load_avg=load,
            processes=processes,
        )


def format_rate(bytes_per_second: float) -> str:
    """Human-readable transfer rate, e.g. ``1.2 MB/s``."""
    value = float(bytes_per_second)
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if value < 1024 or unit == "GB/s":
            return f"{value:.0f} {unit}" if unit == "B/s" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB/s"  # unreachable; keeps type checkers happy


def format_duration(seconds: float) -> str:
    """Compact uptime, e.g. ``3d 4h 12m``."""
    minutes = int(seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


_SPARK = "▁▂▃▄▅▆▇█"


def sparkline(
    values: list[float] | deque[float], width: int, ceiling: float | None = None
) -> str:
    """Block-character sparkline of the last ``width`` values."""
    data = list(values)[-width:] if width > 0 else []
    if not data:
        return ""
    top = ceiling if ceiling else max(data) or 1.0
    return "".join(
        _SPARK[min(int(v / top * (len(_SPARK) - 1) + 0.5), len(_SPARK) - 1)]
        for v in data
    )


def local_ip() -> str | None:
    """This machine's LAN address. Connecting a UDP socket sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere
            return probe.getsockname()[0]
    except OSError:
        return None


def system_report() -> list[tuple[str, str]]:
    """(label, value) rows for the ``/sys`` command."""
    rows: list[tuple[str, str]] = [("Host", socket.gethostname())]
    mac_version = platform.mac_ver()[0]
    rows.append(
        (
            "OS",
            f"macOS {mac_version}"
            if mac_version
            else f"{platform.system()} {platform.release()}",
        )
    )
    try:
        from openjarvis.core.config import detect_hardware

        hw = detect_hardware()
        rows.append(
            ("Chip", hw.cpu_brand or platform.processor() or platform.machine())
        )
        rows.append(("Cores", str(hw.cpu_count)))
        rows.append(("Memory", f"{hw.ram_gb:.0f} GB"))
        if hw.gpu:
            rows.append(("GPU", f"{hw.gpu.name} ({hw.gpu.vram_gb:.0f} GB)"))
    except Exception:
        rows.append(("Chip", platform.processor() or platform.machine()))
    ip = local_ip()
    if ip:
        rows.append(("Local IP", ip))
    rows.append(("Python", platform.python_version()))
    return rows


__all__ = [
    "Snapshot",
    "SystemMonitor",
    "format_duration",
    "format_rate",
    "local_ip",
    "sparkline",
    "system_report",
]
