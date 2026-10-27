"""Binder Layout Maker application package.

This package is a compartmentalized version of the original single-file
``RE5r.py`` script. Modules are organized by responsibility:

- :mod:`binder_app.constants` -- filesystem paths, layout/grid constants, regexes.
- :mod:`binder_app.history` -- card placement history and undo/redo stack.
- :mod:`binder_app.models` -- binder/page/slot data model.
- :mod:`binder_app.image_cache` -- PIL/QPixmap image caching and fetch workers.
- :mod:`binder_app.renderer` -- binder/page/grid layout and paint logic.
- :mod:`binder_app.widgets` -- the binder canvas and card slot widgets.
- :mod:`binder_app.dialogs` -- slot color, custom card, and card preview dialogs.
- :mod:`binder_app.main_window` -- the main ``BinderApp`` window.
- :mod:`binder_app.app` -- application entry point (``main()``).
"""
