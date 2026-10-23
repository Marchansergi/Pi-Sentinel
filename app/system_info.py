"""Estado del equipo (temperatura, memoria, disco, carga). Solo lectura, sin dependencias externas."""
from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from pathlib import Path

APP_VERSION = "1.2.0"
_START = time.time()


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip("\x00\n ")
    except OSError:
        return None


def cpu_temperature() -> float | None:
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        v = _read(str(zone))
        if v and v.lstrip("-").isdigit():
            return round(int(v) / 1000.0, 1)
    return None


def memory() -> dict:
    info: dict[str, int] = {}
    txt = _read("/proc/meminfo") or ""
    for line in txt.splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts and parts[0].isdigit():
            info[k] = int(parts[0]) * 1024
    total, avail = info.get("MemTotal"), info.get("MemAvailable")
    return {"total": total, "available": avail, "used_pct": round(100 * (1 - avail / total), 1) if total and avail else None}


def host_uptime() -> float | None:
    v = _read("/proc/uptime")
    try:
        return float(v.split()[0]) if v else None
    except ValueError:
        return None


def system_info(data_dir: Path, db_path: Path) -> dict:
    try:
        import cv2

        cv = cv2.__version__
    except Exception:  # noqa: BLE001
        cv = None
    du = shutil.disk_usage(data_dir if data_dir.exists() else "/")
    try:
        load = os.getloadavg()
    except OSError:
        load = None
    db_size = sum(Path(str(db_path) + s).stat().st_size for s in ("", "-wal", "-shm") if Path(str(db_path) + s).exists())
    temp = cpu_temperature()
    return {
        "version": APP_VERSION,
        "hostname": socket.gethostname(),
        "model": _read("/proc/device-tree/model") or platform.machine(),
        "os": (_read("/etc/os-release") or "").split("PRETTY_NAME=")[-1].split("\n")[0].strip('"') or platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "opencv": cv,
        "cpu_count": os.cpu_count(),
        "load": [round(x, 2) for x in load] if load else None,
        "cpu_temp": temp,
        "temp_warning": bool(temp and temp >= 75),
        "memory": memory(),
        "disk": {"total": du.total, "free": du.free, "used_pct": round(100 * (1 - du.free / du.total), 1)},
        "db_size": db_size,
        "uptime_app": round(time.time() - _START),
        "uptime_host": host_uptime(),
        "data_dir": str(data_dir),
    }
