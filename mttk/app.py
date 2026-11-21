"""Application entry point."""
from __future__ import annotations

import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME
    from .ui import icons
    from .ui.main_window import MainWindow
    from .ui.theme import build_stylesheet

    if sys.platform == "win32":
        try:  # own taskbar icon instead of python.exe's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MoreThanTinyTask.App")
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("MoreThanTinyTask")
    app.setStyle("Fusion")
    import os
    import tempfile
    assets = icons.write_style_assets(os.path.join(tempfile.gettempdir(), "mttk-style"))
    app.setStyleSheet(build_stylesheet(assets))
    app.setWindowIcon(icons.app_icon())
    app.setQuitOnLastWindowClosed(False)  # the tray icon keeps us alive while hidden

    path = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(open_path=path)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
