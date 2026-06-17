from __future__ import annotations

import os
import sys
from pathlib import Path

from bootstrap_qt import configure_qt_runtime


APP_DIR = Path(__file__).resolve().parent


def main() -> int:
    runtime = configure_qt_runtime(APP_DIR)
    print(f"python={sys.executable}")
    for key, value in runtime.items():
        print(f"{key}={value}")
    print(f"env_QT_PLUGIN_PATH={os.environ.get('QT_PLUGIN_PATH')}")
    print(
        "env_QT_QPA_PLATFORM_PLUGIN_PATH="
        f"{os.environ.get('QT_QPA_PLATFORM_PLUGIN_PATH')}"
    )

    qwindows = (
        Path(os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"]) / "qwindows.dll"
        if os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH")
        else None
    )
    print(f"qwindows_exists={bool(qwindows and qwindows.exists())}")

    from PyQt5 import QtCore, QtWidgets

    app = QtWidgets.QApplication(sys.argv)
    print(
        "qt_version="
        f"{QtCore.QT_VERSION_STR}"
    )
    print("qapplication=ok")
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
