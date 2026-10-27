"""Application entry point."""


import darkdetect
import sys
from PySide6.QtWidgets import QApplication
from pathlib import Path

from . import constants
from .constants import THEME_DIR
from .main_window import BinderApp


def main():
    app = QApplication(sys.argv)
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    window = BinderApp(initial_file=initial, app=app)
    theme = "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
    app.setStyleSheet(Path(THEME_DIR / f"{theme}.qss").read_text())
    window.show()
    sys.exit(app.exec())
