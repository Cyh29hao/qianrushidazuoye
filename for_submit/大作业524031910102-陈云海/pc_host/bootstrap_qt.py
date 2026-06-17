from __future__ import annotations

import os
import sys
from pathlib import Path


def _best_windows_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)

    try:
        import ctypes

        get_short = ctypes.windll.kernel32.GetShortPathNameW
        needed = get_short(str(path), None, 0)
        if needed > 0:
            buffer = ctypes.create_unicode_buffer(needed)
            result = get_short(str(path), buffer, needed)
            if result > 0:
                return buffer.value
    except Exception:
        pass

    return str(path)


def configure_qt_runtime(app_dir: Path) -> dict[str, str]:
    python_exe = Path(sys.executable).resolve()
    venv_root = python_exe.parent.parent
    qt_root = venv_root / "Lib" / "site-packages" / "PyQt5" / "Qt5"
    plugin_dir = qt_root / "plugins"
    platform_dir = plugin_dir / "platforms"
    bin_dir = qt_root / "bin"
    runtime: dict[str, str] = {}

    if plugin_dir.exists():
        runtime["plugins"] = _best_windows_path(plugin_dir)
        os.environ["QT_PLUGIN_PATH"] = runtime["plugins"]

    if platform_dir.exists():
        runtime["platforms"] = _best_windows_path(platform_dir)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = runtime["platforms"]

    if bin_dir.exists():
        runtime["bin"] = _best_windows_path(bin_dir)
        os.environ["PATH"] = runtime["bin"] + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(runtime["bin"])
            except OSError:
                pass

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    if not app_dir.exists():
        raise FileNotFoundError(f"App directory not found: {app_dir}")

    return runtime
