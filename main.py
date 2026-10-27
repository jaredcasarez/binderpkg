"""
Binder Layout Maker — PySide6 version.

GUI for planning Pokémon TCG binder page layouts. Arrange cards on facing
binder pages in real-time: search or load a card list, select a card, then
click (or drag) it into any slot. Cards can be rearranged by dragging between
slots. Layouts are saved as JSON and can be exported page-by-page to a
printable PDF.

The implementation lives in the :mod:`binder_app` package (see
binder_app/__init__.py for a map of its modules). This script is just the
entry point.

Usage:
    python RE5r.py
    python RE5r.py my_binder.json
"""

from binder_app.app import main

if __name__ == "__main__":
    main()
