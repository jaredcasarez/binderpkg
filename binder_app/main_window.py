"""The main application window (``BinderApp``) that ties the data model, renderer,
widgets, dialogs, and background workers together."""


import darkdetect
import data.database as database
import inspect
import json
import requests
import threading
import uuid
from PIL import Image as PILImage
from PySide6.QtCore import QPoint, QSize, QThread, QThreadPool, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QColorDialog, QDockWidget, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from bs4 import BeautifulSoup
from data.database import SessionLocal
from data.models import CachedCard, CardPrices
from data.services import fetch_card_info
from datetime import datetime
from painter_colors import get_colors
from pathlib import Path
from urllib.parse import quote

from . import constants
from .constants import CARD_H_MM, CARD_W_MM, CUSTOM_DIR, DPI, HEADERS, MAX_HISTORY, PAGE_H_MM, PAGE_W_MM, SLOT_H_MM, SLOT_PAD_H_MM, SLOT_PAD_V_MM, SLOT_W_MM, TABS, THEME_DIR, WORKSPACE, _CARD_PATH_RE, _URL_RE
from .dialogs import CardPreviewDialog, CustomCardDialog
from .history import BinderUndoRedo
from .image_cache import ImageCacheManager, ImageCacherWorker, ImageFetcherWorker
from .models import BinderData, BinderPage, BinderSlot
from .renderer import BinderRenderer
from .widgets import CardSlotWidget, RoundedCornerCanvas


class BinderApp(QMainWindow):
    """Main PySide6 Binder Layout Maker application."""

    # Emitted from background threads so GUI work happens on the main thread.
    card_info_fetched = Signal(str, object, bool)
    image_cached = Signal(str, object)
    search_status = Signal(str)
    update_request = Signal()
    def __init__(
        self, initial_file: str | None = None, app: QApplication | None = None
    ):
        super().__init__()
        self.setWindowTitle("Binder Layout Maker — Pokémon TCG")
        self.setGeometry(100, 100, 1340, 820)
        self.setMinimumSize(960, 640)
        self.app = app
        # self.requestActivate()
        # ── Data model ──
        self._binder = BinderData()
        self._binder.data_changed.connect(self._on_binder_data_changed)
        self._filepath: Path | None = None
        self._unsaved = False
        self._undo_redo = BinderUndoRedo()
        self._match_os_theme = True
        # The renderer owns all page/grid layout & slot-widget bookkeeping.
        # Its canvas reference is filled in once _build_ui creates the canvas.
        self._renderer = BinderRenderer(self, None)
        # ── Image infrastructure ──
        self._image_cache = ImageCacheManager()
        self._fetching: set[str] = set()
        self._thread_pool = QThreadPool()
        self._thread_pool.setMaxThreadCount(1)
        self._thread_pool.setThreadPriority(QThread.Priority.IdlePriority)
        self._thread_pool_signals = ImageFetcherWorker.Signals()
        self._thread_pool_signals_cache = ImageCacherWorker.Signals()
        # self._thread_pool_signals.image_loaded.connect(self._on_image_loaded)
        # self._thread_pool_signals.fetch_failed.connect(self._on_fetch_failed)
        self.card_info_fetched.connect(self._on_card_info_fetched)
        self.search_status.connect(self._update_status)

        # ── Selection & interaction state ──
        # self._binder._sel_page: int | None = None
        # self._binder._sel_slot: int | None = None
        self._drag_src_page: int | None = None
        self._drag_src_slot: int | None = None

        # ── Card pool state ──
        self._pool_iid_by_url: dict[str, QTreeWidgetItem] = {}
        self._pool_iid: dict[str, QTreeWidgetItem] = {}
        self._pool_info: dict[str, dict] = {}
        self._picked_url: str | None = None

        # ── Custom card groups ──
        # self._custom_groups: dict[str, dict] = {}  # group_id → {label, tiles:[url]}
        # self._group_iids: dict[str, str] = {}  # group_id → treeview parent iid

        self._card_history: list[str] = (
            []
        )  # last N placed card URLs for ease of re-placement
        self._card_history_iid_by_url: dict[str, QTreeWidgetItem] = {}
        # ── UI Components ──
        self._central_widget: QWidget | None = None
        self._canvas: RoundedCornerCanvas | None = None
        self._status_label: QLabel | None = None
        self._picker_tree: QTreeWidget | None = None
        self._history_tree: QTreeWidget | None = None
        self._custom_tree: QTreeWidget | None = None
        self._current_tree: QTreeWidget | None = None
        self._preview_name_label: QLabel | None = None
        self._preview_image_label: QLabel | None = None
        self._draw_scale: float = 1.0

        self.tab_state = "Search"
        self._build_ui()
        self._renderer._canvas = self._canvas
        self._canvas._renderer = self._renderer
        self._update_status("Ready — open or create a binder to get started")
        self._update_slot_widgets()
        self._refresh_binder()

        if initial_file:
            self._load_file(initial_file)

    # ── delegation to BinderRenderer (kept for backwards compatibility with
    # the rest of BinderApp, which historically owned this layout state) ──
    @property
    def _page_view(self) -> str:
        return self._renderer.page_view

    @_page_view.setter
    def _page_view(self, view: str) -> None:
        self._renderer.page_view = view

    @property
    def _spread_idx(self) -> int:
        return self._renderer.spread_idx

    @_spread_idx.setter
    def _spread_idx(self, idx: int) -> None:
        self._renderer.spread_idx = idx

    @property
    def _slot_widgets(self) -> dict[tuple[int, int], "CardSlotWidget"]:
        return self._renderer.slot_widgets

    def _build_ui(self) -> None:
        """Build main UI."""
        self._central_widget = QWidget()
        self.setCentralWidget(self._central_widget)

        main_layout = QVBoxLayout(self._central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._build_toolbar()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._picker_widg, self._prev_widg = self._build_picker()
        # left_pane.setMaximumWidth(320)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._picker_widg)
        self.addDockWidget(Qt.LeftDockWidgetArea, self._prev_widg)
        self._picker_widg.setAllowedAreas(Qt.LeftDockWidgetArea|Qt.RightDockWidgetArea)
        self._prev_widg.setAllowedAreas(Qt.LeftDockWidgetArea|Qt.RightDockWidgetArea)

        # splitter.addWidget(left_pane)
        # splitter.setCollapsible(0, False)

        right_pane = self._build_binder_panel()
        right_pane.resizeEvent = lambda event: (QWidget.resizeEvent(right_pane, event), self._refresh_binder())
        splitter.addWidget(right_pane)

        # splitter.setSizes([290, 1000])
        # splitter.setStretchFactor(0, 0)
        # splitter.setStretchFactor(1, 1)
        main_layout.addWidget(splitter, 1)

        self._status_label = QLabel("Ready")
        self.statusBar().addWidget(self._status_label)

    def _build_toolbar(self) -> QToolBar:
        """Build toolbar with file operations and grid settings."""
        menubar = self.menuBar()
        # menubar.setMovable(False)
        filemenu = menubar.addMenu("&File")
        new_action = QAction("New", self)
        new_action.triggered.connect(self._new_binder)
        open_action = QAction("Open...", self)
        open_action.triggered.connect(self._open_file)
        save_action = QAction("Save", self)
        save_action.triggered.connect(self._save_file)
        save_as_action = QAction("Save as...", self)
        save_as_action.triggered.connect(self._save_as)
        filemenu.addAction(new_action)
        filemenu.addAction(open_action)
        filemenu.addAction(save_action)
        filemenu.addAction(save_as_action)

        editmenu = menubar.addMenu("&Edit")
        undo_action = QAction("Undo", self)
        undo_action.triggered.connect(self._undo)
        # undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        redo_action = QAction("Redo", self)
        redo_action.triggered.connect(self._redo)
        # redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self._copy_slot)
        # copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        paste_action = QAction("Paste", self)
        paste_action.triggered.connect(self._paste_slot)
        # paste_action.setShortcut(QKeySequence.StandardKey.Paste)
        editmenu.addAction(undo_action)
        editmenu.addAction(redo_action)
        editmenu.addAction(copy_action)
        editmenu.addAction(paste_action)

        viewmenu = menubar.addMenu("&View")
        dark_mode_action = QAction("Dark mode", self)
        dark_mode_action.setCheckable(True)
        dark_mode_action.setChecked(darkdetect.isDark() ^ (not constants.MATCH_OS_THEME))
        dark_mode_action.triggered.connect(self._toggle_dark_mode)
        viewmenu.addAction(dark_mode_action)
        viewmenu.addSeparator()
        page_view_action = QAction("Single page", self)
        page_view_action.setCheckable(True)
        page_view_action.triggered.connect(self._set_page_view)
        page_view_action.setChecked(False)
        viewmenu.addAction(page_view_action)
        viewmenu.addSeparator()
        window_menu = QMenu("Windows")
        self._picker_window_action = QAction("Card picker", self)
        self._picker_window_action.setCheckable(True)
        self._picker_window_action.setChecked(True)
        self._picker_window_action.triggered.connect(self._update_dockwidgets)
        window_menu.addAction(self._picker_window_action)
        self._preview_window_action = QAction("Preview", self)
        self._preview_window_action.setCheckable(True)
        self._preview_window_action.setChecked(True)
        self._preview_window_action.triggered.connect(self._update_dockwidgets)
        window_menu.addAction(self._preview_window_action)
        window_menu.addSeparator()
        reset_window_action = QAction("Reset to default", self)
        reset_window_action.setCheckable(True)
        reset_window_action.triggered.connect(self._reset_dockwidgets)
        viewmenu.addMenu(window_menu)


        pagemenu = menubar.addMenu("&Pages")
        add_page_action = QAction("Add page", self)
        add_page_action.triggered.connect(self._add_page)
        remove_page_action = QAction("Remove page", self)
        remove_page_action.triggered.connect(self._remove_page)
        pagemenu.addAction(add_page_action)
        pagemenu.addAction(remove_page_action)
        pagemenu.addSeparator()
        self.row_actions = []
        self.col_actions = []
        pagemenu.addSection("Rows")
        for r in range(1, 6):
            action = QAction(f"{r} row{'s' if r > 1 else ''}", self)
            action.triggered.connect(
                lambda checked, r=r: self._set_grid_size(r, self._binder.cols)
            )
            action.setCheckable(True)
            if r == self._binder.rows:
                action.setChecked(True)
            self.row_actions.append(action)
            pagemenu.addAction(action)
        pagemenu.addSeparator()
        pagemenu.addSection("Columns")
        for c in range(1, 6):
            action = QAction(f"{c} column{'s' if c > 1 else ''}", self)
            action.setCheckable(True)
            action.triggered.connect(
                lambda checked, c=c: self._set_grid_size(self._binder.rows, c)
            )
            if c == self._binder.cols:
                action.setChecked(True)
            self.col_actions.append(action)
            pagemenu.addAction(action)
        colormenu = menubar.addMenu("&Style")
        
        self._border_color_action = QAction("Set border color...", self)
        icon = self._create_color_icon(self._binder._border_color)
        self._border_color_action.setIcon(icon)
        self._border_color_action.triggered.connect(self._choose_border_color)
        colormenu.addAction(self._border_color_action)
        self._page_color_action = QAction("Set page color...", self)
        icon = self._create_color_icon(self._binder._page_color)
        self._page_color_action.setIcon(icon)
        self._page_color_action.triggered.connect(self._choose_page_color)
        colormenu.addAction(self._page_color_action)
        self._slot_color_action = QAction("Set slot color...", self)
        icon = self._create_color_icon(self._binder._slot_color)
        self._slot_color_action.setIcon(icon)
        self._slot_color_action.triggered.connect(self._choose_slot_color)
        colormenu.addAction(self._slot_color_action)
        self._select_color_action = QAction("Set selection color...", self)
        icon = self._create_color_icon(self._binder._sel_color)
        self._select_color_action.setIcon(icon)
        self._select_color_action.triggered.connect(self._choose_selection_color)
        colormenu.addAction(self._select_color_action)
        colormenu.addSeparator()
        sleeved_action = QAction("Toggle all sleeves", self)
        sleeved_action.setCheckable(True)
        sleeved_action.setChecked(self._binder._sleeved)        
        sleeved_action.triggered.connect(self._on_sleeve_binder_toggled)
        colormenu.addAction(sleeved_action)

        self._sleeve_color_action = QAction("Set default sleeve colors...", self)
        icon = self._create_color_icon(self._binder._sleeve_color)
        self._sleeve_color_action.setIcon(icon)
        self._sleeve_color_action.triggered.connect(self._choose_sleeve_color)
        colormenu.addAction(self._sleeve_color_action)

        colormenu.addSeparator()

        reset_colors_action = QAction("Reset colors to default", self)
        reset_colors_action.triggered.connect(self._reset_colors)
        colormenu.addAction(reset_colors_action)
        exportmenu = menubar.addMenu("&Export")
        export_xlsx_action = QAction("Export spreadsheet...", self)
        export_xlsx_action.triggered.connect(self._export_card_spreadsheet)
        exportmenu.addAction(export_xlsx_action)

        export_custom_pdf_action = QAction("Export custom cards...", self)
        export_custom_pdf_action.triggered.connect(self._on_export_custom_cards)
        exportmenu.addAction(export_custom_pdf_action)

    def _reset_colors(self) -> None:
        """Reset all colors to their default values."""
        self._binder._border_color = None
        self._binder._page_color = None
        self._binder._slot_color = None
        self._binder._sel_color = None
        self._binder._sleeve_color = None
        self._refresh_binder(binder="color")
        theme = "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
        icon = self._create_color_icon(get_colors(theme)["border"])
        self._border_color_action.setIcon(icon)
        icon = self._create_color_icon(get_colors(theme)["page_bg"])
        self._page_color_action.setIcon(icon)
        icon = self._create_color_icon(get_colors(theme)["slot_empty"])
        self._slot_color_action.setIcon(icon)
        icon = self._create_color_icon(get_colors(theme)["border_sel"])
        self._select_color_action.setIcon(icon)
        icon = self._create_color_icon(get_colors(theme)["default_sleeve"])
        self._sleeve_color_action.setIcon(icon)
        self._border_color_action.update()
        self._page_color_action.update()
        self._slot_color_action.update()
        self._select_color_action.update()
        self._sleeve_color_action.update()

    def _choose_slot_color(self) -> None:
        """Open color picker to choose slot color."""
        color = QColorDialog.getColor()
        if color.isValid():
            self._binder._slot_color = color.name()
            self._refresh_binder(binder="color")
            self._update_status(f"Slot color set to {color.name()}")
            icon = self._create_color_icon(self._binder._slot_color)
            self._slot_color_action.setIcon(icon)


    def _choose_page_color(self) -> None:
        """Open color picker to choose page color."""
        color = QColorDialog.getColor()
        if color.isValid():
            self._binder._page_color = color.name()
            self._refresh_binder(binder="color")
            self._update_status(f"Page color set to {color.name()}")
            icon = self._create_color_icon(self._binder._page_color)
            self._page_color_action.setIcon(icon)

    def _choose_selection_color(self) -> None:
        """Open color picker to choose selection color."""
        color = QColorDialog.getColor()
        if color.isValid():
            self._binder._sel_color = color.name()
            self._refresh_binder(binder="color")
            self._update_status(f"Selection color set to {color.name()}")
            icon = self._create_color_icon(self._binder._sel_color)
            self._select_color_action.setIcon(icon)

    def _choose_border_color(self) -> None:
        """Open color picker to choose border color."""
        color = QColorDialog.getColor()
        if color.isValid():
            self._binder._border_color = color.name()
            self._refresh_binder(binder="color")
            self._update_status(f"Border color set to {color.name()}")
            icon = self._create_color_icon(self._binder._border_color)
            self._border_color_action.setIcon(icon)

    def _toggle_dark_mode(self, checked: bool) -> None:
        """Toggle dark mode."""
        constants.MATCH_OS_THEME = not constants.MATCH_OS_THEME
        theme = "dark" if checked else "light"
        self.app.setStyleSheet(Path(THEME_DIR / f"{theme}.qss").read_text())
        self._refresh_binder(binder="color")
        self._update_status(f"Dark mode {'enabled' if checked else 'disabled'}")

    def _on_sleeve_binder_toggled(self, checked: bool) -> None:
        """Toggle sleeve visibility."""
        self._binder._sleeved = checked
        self._refresh_binder(binder="color")
        self._update_status(f"Sleeves {'enabled' if checked else 'disabled'}")

    def _choose_sleeve_color(self) -> None:
        """Open color picker to choose sleeve color."""
        color = QColorDialog.getColor()
        if color.isValid():
            self._binder._sleeve_color = color.name()
            self._refresh_binder(binder="color")
            self._update_status(f"Sleeve color set to {color.name()}")
            icon = self._create_color_icon(self._binder._sleeve_color)
            self._sleeve_color_action.setIcon(icon)
            self._sleeve_color_action.update()

    def _set_grid_size(self, rows: int, cols: int) -> None:
        """Set grid size for all pages."""
        if (
            any(slot.url for page in self._binder.pages for slot in page.slots)
            and any(len(page.slots) != cols * rows for page in self._binder.pages)
            and (rows < self._binder.rows or cols < self._binder.cols)
        ):
            # If the page has cards but the new grid size is different, we need to resize slots
            warning = QMessageBox.warning(
                self,
                "Grid Size Change",
                f"Changing grid size will reset the slots, possibly removing cards from pages.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            )
            if warning == QMessageBox.StandardButton.Cancel:
                for action in self.row_actions:
                    action.setChecked(
                        action.text().startswith(f"{self._binder.rows} row")
                    )
                for action in self.col_actions:
                    action.setChecked(
                        action.text().startswith(f"{self._binder.cols} column")
                    )
                return

        self._binder.cols = cols
        self._binder.rows = rows

        for page in self._binder.pages:
            page.cols = cols
            page.rows = rows
            new_slots = [BinderSlot() for _ in range(cols * rows)]
            for i, slot in enumerate(page.slots):
                if i < len(new_slots):
                    new_slots[i] = slot
            page.slots = new_slots

        for action in self.row_actions:
            action.setChecked(action.text().startswith(f"{rows} row"))
        for action in self.col_actions:
            action.setChecked(action.text().startswith(f"{cols} column"))
        self._refresh_binder(binder="grid")
        self._update_status(f"Grid size set to {cols}x{rows}")

    def _build_picker(self) -> QWidget:
        """Build left panel: card picker."""
        picker_widget = QDockWidget()
        picker_contents = QWidget()
        layout = QVBoxLayout(picker_contents)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        picker_widget.setWidget(picker_contents)

        # url_group_label = QLabel("Add by URL")
        # url_group_label.setStyleSheet("font-weight: bold; color: #666;")
        # layout.addWidget(url_group_label)

        # url_layout = QHBoxLayout()
        # self._url_entry = QLineEdit()
        # self._url_entry.setPlaceholderText("Enter Limitless TCG card URL...")
        # url_layout.addWidget(self._url_entry)
        # fetch_btn = QPushButton("Fetch")
        # fetch_btn.clicked.connect(self._fetch_url)
        # url_layout.addWidget(fetch_btn)
        # layout.addLayout(url_layout)
        # search_group_label = QLabel("Search Limitless TCG")
        # search_group_label.setStyleSheet("font-weight: bold; color: #666;")
        # layout.addWidget(search_group_label)
        self._build_tabs(layout)
        self._search_tab.setLayout(QVBoxLayout())
        search_layout = QHBoxLayout()
        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText("Search for cards...")
        search_layout.addWidget(self._search_entry)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._do_search)
        search_layout.addWidget(search_btn)
        self._search_tab.layout().addLayout(search_layout)

        btn_row = QHBoxLayout()
        load_btn = QPushButton("Load file...")
        load_btn.clicked.connect(self._load_card_file)
        btn_row.addWidget(load_btn)

        self._search_tab.layout().addLayout(btn_row)

        if self._custom_tab.layout() is None:
            self._custom_tab.setLayout(QVBoxLayout())
        btn_row = QHBoxLayout()
        custom_btn = QPushButton("+Custom")
        custom_btn.clicked.connect(self._add_custom_card)
        btn_row.addWidget(custom_btn)
        self._custom_tab.layout().addLayout(btn_row)

        list_group_label = QLabel("Card Pool")
        list_group_label.setStyleSheet("font-weight: bold; color: #666;")
        layout.addWidget(list_group_label)

        self._picker_tree = QTreeWidget()
        self._picker_tree.setSortingEnabled(True)
        self._picker_tree.setIconSize(QSize(40, 66))
        self._picker_tree.setColumnCount(4)
        self._picker_tree.setHeaderLabels(["Name", "Set", "#", "Date"])
        self._picker_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._picker_tree.itemSelectionChanged.connect(self._on_tree_select)
        self._picker_tree.itemDoubleClicked.connect(self._on_tree_double)
        self._picker_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._picker_tree.customContextMenuRequested.connect(self._on_tree_context_menu)

        header = self._picker_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._search_tab.layout().addWidget(self._picker_tree)
        self._current_tree = self._picker_tree

        self._history_tree = QTreeWidget()
        self._history_tree.setSortingEnabled(True)
        self._history_tree.setIconSize(QSize(40, 66))
        self._history_tree.setColumnCount(4)
        self._history_tree.setHeaderLabels(["Name", "Set", "#", "Date"])
        self._history_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._history_tree.itemSelectionChanged.connect(self._on_tree_select)
        self._history_tree.itemDoubleClicked.connect(self._on_tree_double)
        self._history_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._history_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._history_tab.setLayout(QVBoxLayout())
        reset_history_btn = QPushButton("Clear history")
        reset_history_btn.clicked.connect(self._clear_history)
        self._history_tab.layout().addWidget(reset_history_btn)
        self._history_tab.layout().addWidget(self._history_tree)

        self._custom_tree = QTreeWidget()
        self._custom_tree.setSortingEnabled(True)
        self._custom_tree.setIconSize(QSize(40, 66))
        self._custom_tree.setColumnCount(2)
        self._custom_tree.setHeaderLabels(["Image", "Row, Column"])
        self._custom_tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self._custom_tree.itemSelectionChanged.connect(self._on_tree_select)
        self._custom_tree.itemDoubleClicked.connect(self._on_tree_double)
        self._custom_tree.mousePressEvent = self._on_custom_tree_click
        self._custom_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._custom_tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._custom_tree.header().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        # self._custom_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._custom_tab.setLayout(QVBoxLayout())
        self._custom_tab.layout().addWidget(self._custom_tree)
        
        preview_dock_widget = QDockWidget()
        # preview_dock_widget.resizeEvent = lambda event: preview_dock_contents.resize(event.size())
        preview_dock_contents = QWidget()
        prev_layout = QVBoxLayout()
        preview_group_label = QLabel("Selected Card")
        preview_group_label.setFixedHeight(20)
        preview_group_label.setStyleSheet("font-weight: bold; color: #666;")
        prev_layout.addWidget(preview_group_label)

        self._preview_name_label = QLabel("— no card selected —")
        self._preview_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_name_label.setFixedHeight(20)
        # self._preview_name_label.setStyleSheet("color: #666;")
        prev_layout.addWidget(self._preview_name_label)

        self._preview_image_label = QLabel()
        self._preview_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_image_label.setMinimumHeight(200)
        self._preview_image_label.resizeEvent = lambda event: (QLabel.resizeEvent(self._preview_image_label, event), self._on_preview_resize(event))
        # self._preview_image_label.setStyleSheet("background-color: #f0f0f0; border: 1px solid #ddd;")
        self._preview_image_label.mousePressEvent = self._on_preview_click
        prev_layout.addWidget(self._preview_image_label)

        place_btn = QPushButton("Place in Selected Slot")
        place_btn.clicked.connect(self._place_selected)
        prev_layout.addWidget(place_btn)
        preview_dock_contents.setLayout(prev_layout)
        preview_dock_widget.setWidget(preview_dock_contents)
        return picker_widget, preview_dock_widget

    def _update_dockwidgets(self):
        picker_open = self._picker_window_action.isChecked()
        picker_visible = self._picker_widg.isVisible()
        preview_open = self._preview_window_action.isChecked()
        preview_visible = self._prev_widg.isVisible()
        if picker_open:
            self._picker_widg.setVisible(True)
            if not picker_visible:
                self._picker_widg.setDockLocation(Qt.LeftDockWidgetArea)
        else:
            self._picker_widg.setVisible(False)
        if preview_open:
            self._prev_widg.setVisible(True)
            if not preview_visible:
                self._prev_widg.setDockLocation(Qt.LeftDockWidgetArea)
        else:
            self._prev_widg.setVisible(False)

    def _reset_dockwidgets(self):
        self._picker_widg.setVisible(True)
        self._picker_widg.setDockLocation(Qt.LeftDockWidgetArea)
        self._prev_widg.setVisible(True)
        self._prev_widg.setDockLocation(Qt.LeftDockWidgetArea)
        self._picker_window_action.setChecked(True)
        self._preview_window_action.setChecked(True)
    def _on_preview_resize(self, event):
        size = event.size()
        if self._picked_url:
            pixmap = self._image_cache.get_cached_pixmap(self._picked_url)
            if pixmap:
                scaled = pixmap.scaled(
                    size.width() - 20,
                    size.height() - 20,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._preview_image_label.setPixmap(scaled)
            else:
                # print(f"No pixmap cached for {self._picked_url}, fetching...")
                self._fetch_image_async(self._picked_url)
        
    
    def _build_tabs(self, parent_layout: QHBoxLayout) -> None:
        """Build tabbed interface for card pool and search results."""
        from PySide6.QtWidgets import QTabWidget, QHBoxLayout

        # Tkinter implementation:
        # def _build_tabs(self, parent: ttk.Frame) -> None:
        #         self.nb = ttk.Notebook(parent,)
        #         self.nb.grid(row=0, column=0, sticky="nsew", padx=4, pady=2)

        #         # ── Search ──
        #         srch_f = ttk.LabelFrame(parent, text="Search Limitless TCG", padding=4)
        #         srch_f.grid(row=1, column=0, sticky="ew", padx=4, pady=2)
        #         srch_f.columnconfigure(0, weight=1)
        #         self._srch_var = tk.StringVar()
        #         srch_entry = ttk.Entry(srch_f, textvariable=self._srch_var)
        #         srch_entry.grid(row=0, column=0, sticky="ew")
        #         srch_entry.bind("<Return>", lambda _: self._do_search())
        #         ttk.Button(srch_f, text="🔍", width=3, command=self._do_search).grid(
        #             row=0, column=1, padx=(3, 0)
        #         )
        #         self.nb.add(srch_f, text="Search")

        #         # History tab top

        #         hist_f = ttk.LabelFrame(parent, text="Recently Placed Cards", padding=4)
        #         hist_f.grid(row=1, column=0, sticky="ew", padx=4, pady=2)
        #         # replacement_label = ttk.Label(hist_f, text="Cards recently placed will appear here.", wraplength=250, justify="center")
        #         # replacement_label.grid(row=0, column=0, sticky="w")

        #         self.nb.add(hist_f, text="History")

        #         custom_f = ttk.LabelFrame(parent, padding=4, )
        #         custom_f.grid(row=1, column=0, sticky="ew", padx=4, pady=2)
        #         custom_f.columnconfigure(0, weight=1)
        #         custom_entry = ttk.Button(custom_f, text="+ Add Custom Card", command=self._add_custom_card)
        #         custom_entry.grid(row=0, column=0, sticky="ew")
        #         self.nb.add(custom_f, text="Custom Cards")

        #         self.nb.bind("<<NotebookTabChanged>>", self._on_tab_change)
        layout = QHBoxLayout()
        self._tab_widget = QTabWidget()
        self._search_tab = QWidget()
        self._history_tab = QWidget()
        self._custom_tab = QWidget()
        self._tab_widget.addTab(self._search_tab, "Search")
        self._tab_widget.addTab(self._history_tab, "History")
        self._tab_widget.addTab(self._custom_tab, "Custom Cards")
        layout.addWidget(self._tab_widget)
        self._tab_widget.currentChanged.connect(self._on_tab_change)
        parent_layout.addLayout(layout)

    def _build_binder_panel(self) -> QWidget:
        """Build right panel: canvas + pages."""
        panel_widget = QWidget()
        panel_widget.setObjectName("binder_panel")
        panel_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout = QVBoxLayout(panel_widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)
        self._canvas = RoundedCornerCanvas()
        self._canvas.setObjectName("binder_canvas")
        self._canvas._parent_app = self
        self._canvas.slot_clicked.connect(self._on_slot_widget_clicked)
        self._canvas.empty_space_clicked.connect(self._on_empty_space_clicked)
        # self._canvas.slot_right_clicked.connect(self._on_slot_right_clicked)
        self._canvas.slot_dragged.connect(self._on_slot_dragged)
        self.update_request.connect(self._refresh_binder)
        # self._canvas.slot_dragging.connect(self._on_slot_dragging)
        layout.addWidget(self._canvas, 2)

        page_group_label = QLabel("Pages")
        page_group_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(page_group_label)

        page_layout = QHBoxLayout()

        self._page_scroll_area = QScrollArea()
        self._page_scroll_area.setWidgetResizable(True)
        self._page_scroll_area.setSizeAdjustPolicy(
            QScrollArea.SizeAdjustPolicy.AdjustToContents
        )
        self._page_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        scroll_content = QWidget()
        self._page_scroll_area.setWidget(scroll_content)
        self._page_scroll_layout = QHBoxLayout(scroll_content)
        self._page_scroll_layout.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._page_scroll_layout.setContentsMargins(3, 3, 3, 3)
        self._page_scroll_layout.setSpacing(1)
        self._page_scroll_area.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._page_scroll_area.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._page_scroll_area.setFixedHeight(80)
        self._page_buttons: list[QPushButton] = []

        page_layout.addWidget(self._page_scroll_area)

        page_utility_layout = QVBoxLayout()
        add_page_btn = QPushButton("+")
        add_page_btn.setMaximumSize(QSize(80, 40))
        add_page_btn.clicked.connect(self._add_page)
        page_utility_layout.addWidget(add_page_btn)

        remove_page_btn = QPushButton("-")
        remove_page_btn.setMaximumSize(QSize(80, 40))
        remove_page_btn.clicked.connect(self._remove_page)

        # autosave_toggle = Q
        page_utility_layout.addWidget(remove_page_btn)
        page_layout.addLayout(page_utility_layout)
        layout.addLayout(page_layout)

        return panel_widget

    def _update_status(self, message: str) -> None:
        """Update status bar message."""
        if self._status_label:
            self._status_label.setText(message)

    def _update_page_panel(self) -> None:
        """Rebuild page manager buttons."""
        for btn in self._page_buttons:
            btn.deleteLater()
        self._page_buttons.clear()

        for idx, page in enumerate(self._binder.pages):
            is_current = idx == self._binder._sel_page
            card_count = sum(1 for s in page.slots if not s.empty)
            label_text = page.label or f"Page {idx + 1}"
            btn_text = f"{label_text}\n({card_count})"

            btn = QPushButton(btn_text)
            btn.setObjectName(f"page_button")
            btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum)
            btn.setCheckable(True)
            # print(is_current)
            btn.setChecked(is_current)
            btn.clicked.connect(lambda checked, i=idx: self._on_page_button_clicked(i))
            btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, i=idx: self._show_page_menu(btn.mapToGlobal(pos), i)
            )
            # btn.setFixedSize(QSize(50, 40))
            self._page_scroll_layout.insertWidget(len(self._page_buttons), btn)
            self._page_buttons.append(btn)

    def _on_page_button_clicked(self, page_idx: int) -> None:
        """Handle page button click."""
        # print(f"Page button clicked: {page_idx}")
        self._binder._sel_page = page_idx
        self._binder._sel_slot = None
        self._goto_page(page_idx)
        # self._refresh_binder(pages=[page_idx])

    def _update_spread_label(self) -> None:
        """Update spread navigation label."""
        n = len(self._binder.pages)
        li = self._spread_idx * 2 + 1
        ri = li + 1
        if ri <= n:
            self._spread_label.setText(f"Pages {li}–{ri} / {n}")
        else:
            self._spread_label.setText(f"Page {li} / {n}")

        max_s = max(0, (n - 1) // 2)
        # self._spread_slider.setMaximum(max_s)
        # self._spread_slider.setValue(self._spread_idx)

    def _on_tree_context_menu(self, pos):
        """Show context menu for card pool tree."""
        if self._current_tree is None:
            return

        item = self._current_tree.itemAt(pos)
        if item is None:
            return
        # print(f"Context menu requested for item: {item.text(0)}, URL: {item.data(4, Qt.ItemDataRole.UserRole+1)}")
        menu = QMenu(self)
        remove_action = QAction("Remove from pool", self)
        remove_action.triggered.connect(lambda: self._remove_from_pool(url=item.data(4, Qt.ItemDataRole.UserRole+1)))
        menu.addAction(remove_action)
        menu.addSeparator()
        open_action = QAction("Open in Browser", self)
        open_action.triggered.connect(lambda: self._ctx_open_in_browser(url=item.data(4, Qt.ItemDataRole.UserRole+1)))
        menu.addAction(open_action)
        menu.addSeparator()
        copy_card_action = QAction("Copy card", self)
        copy_card_action.triggered.connect(lambda: self._copy_slot(url=item.data(4, Qt.ItemDataRole.UserRole+1)))
        menu.addAction(copy_card_action)
        menu.addSeparator()
        preview_action = QAction("Preview card", self)
        preview_action.triggered.connect(lambda: self._on_preview_click(event=None, url=item.data(4, Qt.ItemDataRole.UserRole+1)))
        menu.addAction(preview_action)

        menu.exec(self._current_tree.viewport().mapToGlobal(pos))

    def _remove_from_pool(self, url: str = None) -> None:
        """Remove card from pool by URL."""
        if url is None:
            return
        tab = self._tab_widget.currentIndex()
        if tab == 0:  # Search tab
            if url in self._pool_iid:
                item = self._pool_iid[url]
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
                else:
                    index = self._picker_tree.indexOfTopLevelItem(item)
                    self._picker_tree.takeTopLevelItem(index)
                del self._pool_iid[url]
                del self._pool_info[url]
                self._update_status(f"Removed card from pool: {url}")
                
        elif tab == 1:  # History tab
            if url in self._card_history_iid_by_url:
                item = self._card_history_iid_by_url[url]
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
                else:
                    index = self._history_tree.indexOfTopLevelItem(item)
                    self._history_tree.takeTopLevelItem(index)
                if url in self._card_history:
                    self._card_history.remove(url)
                del self._card_history_iid_by_url[url]
                self._update_status(f"Removed card from history: {url}")
        elif tab == 2:  # Custom tab
            if url in self._custom_iid:
                item = self._custom_iid[url]
                parent = item.parent()
                if parent:
                    parent.removeChild(item)
                else:
                    index = self._custom_tree.indexOfTopLevelItem(item)
                    self._custom_tree.takeTopLevelItem(index)
                del self._custom_iid[url]
                del self._custom_info[url]
                self._update_status(f"Removed custom card: {url}")
    @Slot()
    def _new_binder(self) -> None:
        """Create new binder."""
        if self._unsaved:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "Discard unsaved changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._binder = BinderData()
        self._spread_idx = 0
        self._filepath = None
        self._unsaved = False
        self._binder._sel_page = None
        self._binder._sel_slot = None
        self.setWindowTitle("Binder Layout Maker — Pokémon TCG")
        self._refresh_binder()
        self._update_status("New binder created.")

    @Slot()
    def _open_file(self) -> None:
        """Open binder file."""
        if self._unsaved:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "Discard unsaved changes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Binder Layout",
            str(WORKSPACE),
            "Binder JSON (*.json);;All Files (*)",
        )
        if path:
            self._load_file(path)

    def _set_page_view(self, checked: bool) -> None:
        """Set single-page or double-page view."""
        self._page_view = "single" if checked else "double"
        self._refresh_binder(binder="view")

    def _copy_slot(self, url: str = None) -> None:
        """Copy selected slot's card URL to clipboard."""
        if url is not None:
            clipboard = QApplication.clipboard()
            clipboard.setText(url)
            self._update_status(f"Copied card URL to clipboard: {url}")
            return
        if self._binder._sel_page is not None and self._binder._sel_slot is not None:
            slot = self._binder.pages[self._binder._sel_page].slots[
                self._binder._sel_slot
            ]
            if not slot.empty:
                url = slot.url
            else:
                self._update_status("Selected slot is empty; nothing to copy.")
                return
            clipboard = QApplication.clipboard()
            clipboard.setText(url)
            self._update_status(f"Copied card URL to clipboard: {url}")
        else:
            self._update_status("No slot selected; cannot copy.")

    def _paste_slot(self) -> None:
        """Paste card URL from clipboard into selected slot."""
        if self._binder._sel_page is not None and self._binder._sel_slot is not None:
            clipboard = QApplication.clipboard()
            url = clipboard.text().strip()
            if url:
                self._binder.pages[self._binder._sel_page].slots[
                    self._binder._sel_slot
                ].url = url
                self._unsaved = True
                self._add_to_history_pool(url)
                self._update_status(f"Pasted card URL into slot: {url}")
            else:
                self._update_status("Clipboard is empty; nothing to paste.")
        else:
            self._update_status("No slot selected; cannot paste.")

    def _update_viewable_slots(self):
        self._renderer.update_viewable_slots()

    def _refresh_binder(self, binder=None, pages=None, slots=None, diffs=None) -> None:
        #STATUSES
        # BINDER: diff, grid, color
        # PAGES: list[idxs]
        # SLOTS: list[pgidx, sltidx]

        if diffs is None:
            diffs = self._undo_redo.push_state_change(self._binder)
        to_redraw = {}
        if binder:
            if binder == "diff":
                if diffs["slots"]:
                    to_redraw["slots"] = diffs["slots"]
                elif diffs["pages"]:
                    to_redraw["pages"]= diffs["pages"]
                elif diffs["binder"]:
                    if "color" in diffs["binder"]:
                        to_redraw["colored"] = []
                        #redraw colored elements (most things)
                    if "grid" in diffs["binder"]:
                        to_redraw["pages"] = []
                        to_redraw["slots"] = []
            elif binder == "grid":
                #update frid function
                self._update_slot_widgets()
                to_redraw["pages"] = []
                to_redraw["slots"] = []
                
            elif binder == "color":
                #redraw colored elements
                to_redraw["colored"] = []
            
            elif binder == "view":
                to_redraw["pages"] = []
                to_redraw["slots"] = []
        elif pages:
            self._update_slot_widgets([(pgidx, sltidx) for pgidx, _ in enumerate(self._binder.pages) for sltidx in range(self._binder.rows*self._binder.cols)])
            to_redraw["pages"] = pages
        elif slots:
            self._update_slot_widgets(slots)
            to_redraw["slots"] = slots
        else: 
            self._update_slot_widgets()
            to_redraw["slots"] = []
            to_redraw["pages"] = []
            to_redraw["colored"] = []
        self._renderer.redraw(to_redraw)
        # self._update_spread_label()

    def _on_binder_data_changed(self, *args) -> None:
        """Handle binder data change."""
        # print("Binder data changed.")
        self._unsaved = True
        
        
        self._refresh_binder(
            slots=[(self._binder._sel_page, self._binder._sel_slot)]
        )

    def _undo(self) -> None:
        """Undo last action."""
        
        undone_binder = self._undo_redo.undo()
        diff = self._undo_redo.diff(self._binder, undone_binder)
        if undone_binder is not None and diff is not None:
            self._binder = undone_binder
        else:
            self._update_status("Nothing to undo.")
            return
        self._unsaved = True
        self._refresh_binder(binder="diff", diffs=diff)

    def _redo(self) -> None:
        """Redo last undone action."""
        redone_binder = self._undo_redo.redo()
        diff = self._undo_redo.diff(self._binder, redone_binder)
        if redone_binder is not None:
            self._binder = redone_binder
        else:
            self._update_status("Nothing to redo.")
            return
        self._unsaved = True
        self._refresh_binder(binder="diff", diffs=diff)

    @Slot()
    def _save_file(self) -> None:
        """Save binder file."""
        if self._filepath:
            self._write_file(self._filepath)
        else:
            self._save_as()

    @Slot()
    def _save_as(self) -> None:
        """Save binder as new file."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Binder Layout",
            str(WORKSPACE / "binder.json"),
            "Binder JSON (*.json);;All Files (*)",
        )
        if path:
            self._write_file(Path(path))

    def _write_file(self, path: Path) -> None:
        """Write binder to JSON file."""
        try:
            path.write_text(
                json.dumps(self._binder.to_json(), indent=2, default=str),
                encoding="utf-8",
            )
            # print("save", self._binder._slot_color)
            self._filepath = path
            self._unsaved = False
            self.setWindowTitle(f"Binder Layout Maker — {path.name}")
            self._update_status(f"Saved > {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", str(exc))

    def _load_file(self, path: str) -> None:
        """Load binder from JSON file."""
        p = Path(path)
        if not p.exists():
            QMessageBox.critical(self, "Error", f"File not found:\n{p}")
            return
        try:
            self._binder = BinderData.from_json(
                json.loads(p.read_text(encoding="utf-8"))
            )
            print("load", self._binder.rows, self._binder.cols)
            self._set_grid_size(self._binder.rows, self._binder.cols)
            self._filepath = p
            self._unsaved = False
            self._spread_idx = 0
            self._binder._sel_page = None
            self._binder._sel_slot = None
            self.setWindowTitle(f"Binder Layout Maker — {p.name}")
            #self._check_for_missing_images()
            self._history_tree.clear()
            self._custom_tree.clear()
            self._refresh_rowcol_actions()
            try:
                self._repopulate_custom_groups()
            except Warning as exc:
                self._binder._custom_groups = {}
                # print(f"Error repopulating custom groups: {exc}")
                QMessageBox.warning(self, "Custom groups error", str(exc))
            self._update_status(f"Opened {p.name} ({len(self._binder.pages)} pages)")
            
        except Warning as exc:
            # print(f"Error loading file {p}: {exc}")
            pass
            # QMessageBox.critical(self, "File load Error", str(exc))
        self._refresh_binder()
        # self._goto_page(self._binder._sel_page or 0)
    
    def _refresh_rowcol_actions(self):
        """Refresh row/column menu actions to reflect current grid size."""
        for action in self.row_actions:
            action.setChecked(action.text().startswith(f"{self._binder.rows} row"))
        for action in self.col_actions:
            action.setChecked(action.text().startswith(f"{self._binder.cols} column"))
    def _check_for_missing_images(self) -> None:
        """Check for missing images in the binder and fetch them if needed."""
        missing_urls = set()
        for url, group in self._binder._custom_groups.items():
            if url.startswith("custom://"):
                if Path(url.replace("custom://", "")).exists():
                    continue
                else:
                    missing_urls.add(url)
        for page in self._binder.pages:
            for slot in page.slots:
                if not slot.empty and slot.url.startswith("custom://"):
                    if Path(slot.url.replace("custom://", "")).exists():
                        continue
                    else:
                        # print(f"Missing image for slot: {slot.url}")
                        missing_urls.add(slot.url)
                        slot.url = None  # Clear the slot if the image is missing
        # print(f"Custom groups before checking for missing images: {self._binder._custom_groups}")
        self._binder._custom_groups.update({url: group for url, group in self._binder._custom_groups.items() if url in missing_urls})
        self._binder.custom_groups = {url: group for url, group in self._binder._custom_groups.items() if url not in missing_urls}
        if missing_urls:
            self._update_status("Fetching missing images...")
            for url in missing_urls:
                if url.startswith("custom://"):
                    local_path = Path(url.replace("custom://", ""))
                    if not local_path.exists():
                        print(f"Missing custom image: {local_path}")
                        # Optionally, you could prompt the user to locate the missing file
                        # or handle it in some other way.
    def _export_pdf(self) -> None:
        """Export binder to PDF."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Binder as PDF",
            str(WORKSPACE / "binder.pdf"),
            "PDF (*.pdf)",
        )
        if not path:
            return

        self._update_status("Exporting PDF — fetching any missing images...")
        threading.Thread(
            target=self._do_export_pdf, args=(Path(path),), daemon=True
        ).start()

    def _do_export_pdf(self, out_path: Path) -> None:
        """Generate PDF with card images."""

        def mm(x: float) -> int:
            return round(x / 25.4 * DPI)

        cw, ch = mm(CARD_W_MM), mm(CARD_H_MM)
        pw, ph = mm(PAGE_W_MM), mm(PAGE_H_MM)
        cols, rows = self._binder.cols, self._binder.rows
        ox = (pw - cols * cw) // 2
        oy = (ph - rows * ch) // 2

        pdf_pages: list[PILImage.Image] = []

        for pg in self._binder.pages:
            page_img = PILImage.new("RGBA", (pw, ph), (255, 255, 255, 255))
            for idx, slot in enumerate(pg.slots):
                if slot.empty:
                    continue
                col = idx % cols
                row = idx // cols
                img = self._image_cache.get_cached_pil(slot.url)
                if not img:
                    continue
                thumb = img.resize((cw, ch), PILImage.Resampling.NEAREST)
                page_img.paste(thumb, (ox + col * cw, oy + row * ch))
            pdf_pages.append(page_img)

        if not pdf_pages:
            self._update_status("No card images available to export.")
            return

        try:
            pdf_pages[0].save(
                str(out_path),
                format="PDF",
                save_all=True,
                append_images=pdf_pages[1:],
                resolution=DPI,
            )
            self._update_status(f"PDF exported > {out_path.name}")
        except Exception as exc:
            self._update_status(f"Export failed: {exc}")

    def _export_card_spreadsheet(self) -> None:
        """Export binder to XLSX spreadsheet."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Binder as XLSX",
            str(WORKSPACE / "binder.xlsx"),
            "Excel (*.xlsx)",
        )
        if not path:
            return

        try:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.append(
                [
                    "Image",
                    "Card Name",
                    "Page",
                    "Slot",
                    "Card URL",
                    "Set",
                    "Card Number",
                    "Release Date",
                ]
            )
            for page_idx, pg in enumerate(self._binder.pages):
                for slot_idx, slot in enumerate(pg.slots):
                    if not slot.empty and not slot.url.startswith("custom:"):
                        info = (
                            self._pool_info[slot.url]
                            if slot.url in self._pool_info
                            else None
                        )
                        img = self._image_cache.get_cached_pil(slot.url)
                        if info:
                            ws.append(
                                [
                                    "",
                                    info.get("card_name"),
                                    page_idx + 1,
                                    slot_idx + 1,
                                    slot.url,
                                    f"{info.get('set_name')} ({info.get('set_code')})",
                                    info.get("number"),
                                    info.get("release_date"),
                                ]
                            )
                        else:
                            ws.append(
                                [
                                    "",
                                    "",
                                    page_idx + 1,
                                    slot_idx + 1,
                                    slot.url,
                                    "",
                                    "",
                                    "",
                                ]
                            )
                        # insert image into first cell
                        if img:
                            from openpyxl.drawing.image import Image as OpenPyXLImage
                            from io import BytesIO

                            img_byte_arr = BytesIO()
                            img.save(img_byte_arr, format="PNG")
                            img_byte_arr.seek(0)
                            openpyxl_img = OpenPyXLImage(img_byte_arr)
                            openpyxl_img.width = 40  # Set desired width
                            openpyxl_img.height = 66  # Set desired height
                            ws.add_image(openpyxl_img, f"A{ws.max_row}")
            # for col in ws.cols:
            #     ws.column_dimensions[col[0].column_letter].auto_width()
            for row in ws.rows:
                ws.row_dimensions[row[0].row].height = 75  # Set header row height
            wb.save(path)
            self._update_status(f"XLSX exported > {Path(path).name}")
        except Exception as exc:
            self._update_status(f"Export failed: {exc}")

    def resizeEvent(self, event) -> None:
        """Handle window resize event."""
        super().resizeEvent(event)
        # self._update_viewable_slots()
        self._refresh_binder()
    class CustomCardExportDialog(QMainWindow):
        def __init__(self, parent=None, callback=None):
            super().__init__(parent)
            self._callback = callback
            self.setWindowTitle("Export Custom Cards")

            central_widget = QWidget()
            self.setCentralWidget(central_widget)
            layout = QVBoxLayout(central_widget)
            panel_label = QLabel("Select custom groups to export:")
            panel_label.setStyleSheet("font-weight: bold; color: #666;")
            layout.addWidget(panel_label)
            selector_panel = QScrollArea()
            selector_panel.setObjectName("binder_panel")
            selector_widget = QWidget()
            selector_layout = QGridLayout(selector_widget)
            selector_layout.setAlignment(
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter
            )
            # selector_layout.setContentsMargins(4, 4, 4, 4)
            self._binder = parent._binder
            self._image_cache = parent._image_cache
            self._custom_groups = self._binder._custom_groups
            card_w = 120
            card_h = 199
            max_card_h = 0
            curr_slot = 0
            self._group_widgets: dict[str, QLabel] = {}
            for custom_group in self._custom_groups.values():
                img_label = QLabel()

                img_label.setAlignment(
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter
                )

                img = self._image_cache.get_cached_pixmap(custom_group["url"])
                if img:
                    group_w = (
                        custom_group["cols"] * card_w + (custom_group["cols"] - 1) * 4
                    )
                    group_h = (
                        custom_group["rows"] * card_h + (custom_group["rows"] - 1) * 4
                    )
                    img = img.scaled(
                        group_w,
                        group_h,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    img_label.setPixmap(img)
                    if group_h > max_card_h:
                        max_card_h = group_h
                img_label.setToolTip(
                    f"{custom_group['card_name']} ({custom_group['cols']}x{custom_group['rows']})"
                )
                img_label.mousePressEvent = lambda event, group_name=custom_group[
                    "url"
                ]: self._on_group_clicked(group_name)
                img_label.setSizePolicy(
                    QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
                )
                selector_layout.addWidget(
                    img_label, 0, curr_slot, custom_group["rows"], custom_group["cols"]
                )
                curr_slot += custom_group["cols"]
                self._group_widgets[custom_group["url"]] = img_label
            selector_panel.setWidgetResizable(True)
            selector_panel.setMinimumHeight(max_card_h + 20)
            selector_panel.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOn
            )
            selector_panel.setVerticalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            )
            selector_panel.setAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
            )
            selector_panel.setFixedHeight(max_card_h + 20)
            # selector_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            selector_panel.setWidget(selector_widget)
            layout.addWidget(selector_panel)
            # self.setLayout(layout)
            self._selected_custom_groups: dict[str, dict] = {}
            bottom_bar_layout = QHBoxLayout()
            self._split_checkbox = QCheckBox(
                "Split each custom group into individual cards"
            )
            self._split_checkbox.setChecked(False)
            self._split_checkbox.stateChanged.connect(self._on_split_changed)
            bottom_bar_layout.addWidget(self._split_checkbox)
            export_btn = QPushButton("Export Selected to PDF")
            export_btn.clicked.connect(self._on_export_clicked)
            bottom_bar_layout.addWidget(export_btn)
            layout.addLayout(bottom_bar_layout)
            self._split = False
            self.setGeometry(100, 100, 720, max_card_h + 100)
            self.setMinimumSize(500, max_card_h + 100)
            self._update_selected_groups()
            # self._refresh_binder()

        def _on_split_changed(self, state: int) -> None:
            self._split = self._split_checkbox.isChecked()
            # print(f"Split checkbox changed: {self._split}")

        def _on_export_clicked(self) -> None:
            """Handle export button click."""
            if self._callback:
                self._callback(
                    selected_groups=self._selected_custom_groups, split=self._split
                )
            self.close()

        def _on_group_clicked(self, group_name: str) -> None:
            """Handle custom group click."""
            if group_name in self._selected_custom_groups:
                del self._selected_custom_groups[group_name]

            else:
                self._selected_custom_groups[group_name] = self._custom_groups[
                    group_name
                ]
            self._update_selected_groups()

        def _update_selected_groups(self) -> None:
            """Update the display of selected custom groups."""
            for group_url, img_label in self._group_widgets.items():
                if group_url in self._selected_custom_groups:
                    img_label.setStyleSheet("border: 2px solid blue;")
                else:
                    img_label.setStyleSheet("border: 2px solid black;")

    def _on_export_custom_cards(self) -> None:
        """Export custom cards to PDF."""
        dialog = self.CustomCardExportDialog(
            self, callback=self._export_custom_cards_pdf
        )
        dialog.show()
        # self._export_custom_cards_pdf()

    def _export_custom_cards_pdf(self, selected_groups: dict, split=False) -> None:
        """Generate PDF with card images."""
        
        def mm(x: float) -> int:
            return round(x / 25.4 * DPI)
        margin = mm(6.35)
        cw, ch = mm(CARD_W_MM), mm(CARD_H_MM)
        pw, ph = mm(PAGE_W_MM)-margin, mm(PAGE_H_MM)-margin
        cols, rows = self._binder.cols, self._binder.rows
        ox = margin
        oy = margin
        h_spacing = pw - margin*2 - ((pw - ox*2) // cw) * cw
        v_spacing = ph - margin*2 -((ph - oy*2) // ch) * ch
        curr_row_w = 0
        curr_col_h = 0
        out_path = QFileDialog.getSaveFileName(
            self,
            "Export Custom Cards as PDF",
            str(WORKSPACE / "custom_cards.pdf"),
            "PDF (*.pdf)",
        )[0]
        if not out_path:
            return

        pdf_pages: list[PILImage.Image] = []
        page_img = PILImage.new("RGBA", (pw, ph), (255, 255, 255, 255))
        if split:
            for url, custom_group in selected_groups.items():
                print(
                    len(custom_group.get("children", {})),
                    "cards in custom group:",
                    custom_group["card_name"],
                )
                for child_url, child in custom_group.get("children", {}).items():
                    # print("Exporting custom group child:", child_url)
                    thumb = self._image_cache.get_cached_pil(f"{child_url}")
                    if not thumb:
                        print("Skipping custom group with missing image:", child_url)
                        continue
                    # Calculate resize to match slot sizes + pad
                    total_w_mm = CARD_W_MM + SLOT_PAD_H_MM
                    total_h_mm = CARD_H_MM + SLOT_PAD_V_MM
                    total_w_px = mm(total_w_mm)
                    total_h_px = mm(total_h_mm)
                    thumb = thumb.resize(
                        (total_w_px, total_h_px), PILImage.Resampling.LANCZOS
                    )

                    if curr_row_w + thumb.width + h_spacing + ox > pw:
                        curr_row_w = 0
                        oy += thumb.height + v_spacing
                        if thumb.height + oy > ph:
                            pdf_pages.append(page_img)
                            page_img = PILImage.new(
                                "RGBA", (pw, ph), (255, 255, 255, 255)
                            )
                            curr_row_w = 0
                            curr_col_h = 0
                            oy = (ph - total_h_px) // 4

                    page_img.paste(thumb, (ox + curr_row_w, oy))
                    curr_row_w += thumb.width + h_spacing

            pdf_pages.append(page_img)
        else:
            # for url, custom_group in selected_groups.items():
            #     # print(f"Exporting custom group: {custom_group['name']}")
            #     thumb = self._image_cache.get_cached_pil(f"{url}")
            #     if not thumb:
            #         print("Skipping custom group with missing image:", url)
            #         continue
            #     # Calculate resize to match slot sizes + pad
            #     total_w_mm = SLOT_W_MM * custom_group["cols"] + SLOT_PAD_H_MM * (
            #         custom_group["cols"] - 1
            #     )
            #     total_h_mm = SLOT_H_MM * custom_group["rows"] + SLOT_PAD_V_MM * (
            #         custom_group["rows"] - 1
            #     )
            #     total_w_px = mm(total_w_mm)
            #     total_h_px = mm(total_h_mm)
            #     thumb = thumb.resize(
            #         (total_w_px, total_h_px), PILImage.Resampling.LANCZOS
            #     )
            #     if thumb.width + ox > pw or thumb.height + oy > ph:
            #         print(
            #             f"Skipping custom group with image too large for page: {custom_group['card_name']} ({thumb.width}x{thumb.height})"
            #         )
            #         continue
                
            #     for url, custom_group in selected_groups.items():
            #         if curr_row_w + thumb.width + ox > pw:
            #             curr_row_w = 0
            #             oy += thumb.height + v_spacing
            #         if curr_col_h + v_spacing + oy > ph:
            #             pdf_pages.append(page_img)
            #             page_img = PILImage.new("RGBA", (pw, ph), (255, 255, 255, 255))
            #             curr_row_w = 0
            #             curr_col_h = 0
            #             oy = (ph - total_h_px) // 4

            #     page_img.paste(thumb, (ox, oy))
            #     from PIL import ImageDraw

            #     draw = ImageDraw.Draw(page_img)
            #     # Draw a border around the pasted image
            #     border_color = (0, 0, 0, 255)  # Black
            #     # border_width = 2  # Adjust as needed
            #     bold_border_width = 10
            #     # draw.rectangle([ox, oy, ox + total_w_px - 1, oy + total_h_px - 1], outline=border_color, width=border_width)
            #     # Draw lines with thicker border where slot divisions are

            #     for r in range(custom_group["rows"]):
            #         for c in range(custom_group["cols"]):
            #             slot_x = ox + c * (mm(SLOT_W_MM) + mm(SLOT_PAD_H_MM))
            #             slot_y = oy + r * (mm(SLOT_H_MM) + mm(SLOT_PAD_V_MM))
            #             if r == 0:
            #                 draw.line(
            #                     [(slot_x, slot_y), (slot_x + mm(SLOT_W_MM), slot_y)],
            #                     fill=border_color,
            #                     width=bold_border_width,
            #                 )  # Horizontal
            #             if r == custom_group["rows"] - 1:
            #                 draw.line(
            #                     [
            #                         (slot_x, slot_y + mm(SLOT_H_MM)),
            #                         (slot_x + mm(SLOT_W_MM), slot_y + mm(SLOT_H_MM)),
            #                     ],
            #                     fill=border_color,
            #                     width=bold_border_width,
            #                 )  # Horizontal underside
            #             if c == 0:
            #                 draw.line(
            #                     [(slot_x, slot_y), (slot_x, slot_y + mm(SLOT_H_MM))],
            #                     fill=border_color,
            #                     width=bold_border_width,
            #                 )  # Vertical left
            #             if c == custom_group["cols"] - 1:
            #                 draw.line(
            #                     [
            #                         (slot_x + mm(SLOT_W_MM), slot_y),
            #                         (slot_x + mm(SLOT_W_MM), slot_y + mm(SLOT_H_MM)),
            #                     ],
            #                     fill=border_color,
            #                     width=bold_border_width,
            #                 )  # Vertical right
            #     curr_row_w += thumb.width
            #     curr_col_h += thumb.height

            # pdf_pages.append(page_img)
            group_set = set(selected_groups.keys())
            page_img = PILImage.new("RGBA", (pw, ph), (255, 255, 255, 255))
            while group_set:
                curr_row_w = 0
                curr_col_h = 0
                # print(len(group_set))
                for group_url in list(group_set):
                    # print(len(group_set))
                    custom_group = selected_groups[group_url]
                    thumb = self._image_cache.get_cached_pil(f"{group_url}")
                    if not thumb:
                        print("Skipping custom group with missing image:", group_url)
                        group_set.remove(group_url)
                        continue
                    total_w_mm = SLOT_W_MM * custom_group["cols"] + SLOT_PAD_H_MM * (
                        custom_group["cols"] - 1
                    )
                    total_h_mm = SLOT_H_MM * custom_group["rows"] + SLOT_PAD_V_MM * (
                        custom_group["rows"] - 1
                    )
                    total_w_px = mm(total_w_mm)
                    total_h_px = mm(total_h_mm)
                    thumb = thumb.resize(
                        (total_w_px, total_h_px), PILImage.Resampling.LANCZOS
                    )
                    if curr_row_w + thumb.width + ox > pw:
                        curr_row_w = 0
                        oy += thumb.height + v_spacing
                        if thumb.height + oy > ph:
                            pdf_pages.append(page_img)
                            page_img = PILImage.new(
                                "RGBA", (pw, ph), (255, 255, 255, 255)
                            )
                            curr_row_w = 0
                            curr_col_h = 0
                            oy = margin
                    
                    page_img.paste(thumb, (ox + curr_row_w, oy))
                    
                    group_set.remove(group_url)

                    from PIL import ImageDraw
                
                    draw = ImageDraw.Draw(page_img)
                    # Draw a border around the pasted image
                    border_color = (0, 0, 0, 255)  # Black
                    # border_width = 2  # Adjust as needed
                    bold_border_width = 10
                    # draw.rectangle([ox, oy, ox + total_w_px - 1, oy + total_h_px - 1], outline=border_color, width=border_width)
                    # Draw lines with thicker border where slot divisions are
    
                    for r in range(custom_group["rows"]):
                        for c in range(custom_group["cols"]):
                            slot_x = ox + curr_row_w + c * (mm(SLOT_W_MM) + mm(SLOT_PAD_H_MM))
                            slot_y = oy + r * (mm(SLOT_H_MM) + mm(SLOT_PAD_V_MM))
                            if r == 0:
                                draw.line(
                                    [(slot_x, slot_y), (slot_x + mm(SLOT_W_MM), slot_y)],
                                    fill=border_color,
                                    width=bold_border_width,
                                )  # Horizontal
                            if r == custom_group["rows"] - 1:
                                draw.line(
                                    [
                                        (slot_x, slot_y + mm(SLOT_H_MM)),
                                        (slot_x + mm(SLOT_W_MM), slot_y + mm(SLOT_H_MM)),
                                    ],
                                    fill=border_color,
                                    width=bold_border_width,
                                )  # Horizontal underside
                            if c == 0:
                                draw.line(
                                    [(slot_x, slot_y), (slot_x, slot_y + mm(SLOT_H_MM))],
                                    fill=border_color,
                                    width=bold_border_width,
                                )  # Vertical left
                            if c == custom_group["cols"] - 1:
                                draw.line(
                                    [
                                        (slot_x + mm(SLOT_W_MM), slot_y),
                                        (slot_x + mm(SLOT_W_MM), slot_y + mm(SLOT_H_MM)),
                                    ],
                                    fill=border_color,
                                    width=bold_border_width,
                                )  # Vertical right
                    curr_row_w += thumb.width + h_spacing
            pdf_pages.append(page_img)
                    
            if not pdf_pages:
                self._update_status("No card images available to export.")
                return

        try:
            pdf_pages[0].save(
                str(out_path),
                format="PDF",
                save_all=True,
                append_images=pdf_pages[1:],
                resolution=DPI,
            )
            self._update_status(f"PDF exported > {out_path}")
        except Exception as exc:
            self._update_status(f"Export failed: {exc}")
    
    def _on_preview_click(self, event, url = None) -> None:
        """Open full screen preview of selected card."""
       
        if self._picked_url or url:
            if url is not None:
                self._picked_url = url
            if url is not None or (event and event.button() == Qt.MouseButton.LeftButton):
                img = self._image_cache.get_cached_pil(self._picked_url)
                if img:
                    if self._picked_url.startswith("custom://"):
                        info = self._pool_info.get(self._picked_url, {})
                        preview = CardPreviewDialog(
                            self, self._picked_url, info=info, custom=True
                        )
                    else:
                        preview = CardPreviewDialog(
                            self,
                            self._picked_url,
                            info=self._pool_info.get(self._picked_url),
                        )
                    preview.show()
            if event.button() == Qt.MouseButton.RightButton:
                ctx_menu = QMenu(self)
                ctx_menu.addAction("Open in Browser", lambda: self._ctx_open_in_browser(url or self._picked_url))
                ctx_menu.exec(event.globalPos())

    def _on_slot_dragged(
        self, src_page: int, src_slot: int, dst_page: int, dst_slot: int
    ) -> None:
        """Handle card drag-and-drop between slots."""
        if src_page == dst_page and src_slot == dst_slot:
            return  # No change

        if src_page < 0 or src_page >= len(self._binder.pages):
            return
        if dst_page < 0 or dst_page >= len(self._binder.pages):
            return
        
        src_pg = self._binder.pages[src_page]
        dst_pg = self._binder.pages[dst_page]

        if src_slot < 0 or src_slot >= len(src_pg.slots):
            return
        if dst_slot < 0 or dst_slot >= len(dst_pg.slots):
            return

        # Swap the slots
        src_card = src_pg.slots[src_slot]
        dst_card = dst_pg.slots[dst_slot]
        src_pg.slots[src_slot] = dst_card
        dst_pg.slots[dst_slot] = src_card

        self._unsaved = True
        self._binder._sel_page = dst_page
        self._binder._sel_slot = dst_slot
        self._refresh_binder(slots=[(src_page, src_slot), (dst_page, dst_slot)])
        self._update_status(
            f"Moved card from page {src_page + 1}, slot {src_slot + 1} to page {dst_page + 1}, slot {dst_slot + 1}"
        )

    def keyReleaseEvent(self, event) -> None:
        """Handle key release events for undo/redo."""
        if event.isAutoRepeat():
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.key() == Qt.Key.Key_Z:
                self._undo()
                return
            if event.key() == Qt.Key.Key_Y:
                self._redo()
                return
            if event.key() == Qt.Key.Key_C:
                self._copy_slot()
                return
            if event.key() == Qt.Key.Key_V:
                self._paste_slot()
                return
            if event.key() == Qt.Key.Key_S:
                self._save_file()
                return
            if event.key() == Qt.Key.Key_O:
                self._open_file()
                return
            if event.key() == Qt.Key.Key_N:
                self._new_binder()
                return

            if event.key() == Qt.Key.Key_Left:
                if (
                    self._binder._sel_page is not None
                    and self._binder._sel_slot is not None
                    and self._binder.pages[self._binder._sel_page]
                    .slots[self._binder._sel_slot]
                    .url
                ):
                    page = self._binder.pages[self._binder._sel_page]
                    col = self._binder._sel_slot % self._binder.cols
                    row = self._binder._sel_slot // self._binder.cols
                    if col > 0:
                        col -= 1
                    else:
                        col = self._binder.cols - 1
                        if row > 0:
                            row -= 1
                        else:
                            row = self._binder.rows - 1
                    new_slot = row * self._binder.cols + col
                    if (
                        not self._binder.pages[self._binder._sel_page]
                        .slots[new_slot]
                        .url
                    ):
                        self._binder.pages[self._binder._sel_page].slots[
                            new_slot
                        ].url = (
                            self._binder.pages[self._binder._sel_page]
                            .slots[self._binder._sel_slot]
                            .url
                        )
                        self._binder.pages[self._binder._sel_page].slots[
                            self._binder._sel_slot
                        ].url = ""
                        self._binder._sel_slot = new_slot
                    else:
                        return
                    # self._refresh_binder()
                    self._update_status(
                        f"Moved left to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                    )
                return
            if event.key() == Qt.Key.Key_Right:
                if (
                    self._binder._sel_page is not None
                    and self._binder._sel_slot is not None
                    and self._binder.pages[self._binder._sel_page]
                    .slots[self._binder._sel_slot]
                    .url
                ):
                    page = self._binder.pages[self._binder._sel_page]
                    col = self._binder._sel_slot % self._binder.cols
                    row = self._binder._sel_slot // self._binder.cols
                    if col < self._binder.cols - 1:
                        col += 1
                    else:
                        col = 0
                        if row < self._binder.rows - 1:
                            row += 1
                        else:
                            row = 0
                    new_slot = row * self._binder.cols + col
                    if (
                        not self._binder.pages[self._binder._sel_page]
                        .slots[new_slot]
                        .url
                    ):
                        self._binder.pages[self._binder._sel_page].slots[
                            new_slot
                        ].url = (
                            self._binder.pages[self._binder._sel_page]
                            .slots[self._binder._sel_slot]
                            .url
                        )
                        self._binder.pages[self._binder._sel_page].slots[
                            self._binder._sel_slot
                        ].url = ""
                        self._binder._sel_slot = new_slot
                    else:
                        return
                    # self._refresh_binder()
                    self._update_status(
                        f"Moved right to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                    )
                return

            if event.key() == Qt.Key.Key_Up:
                if (
                    self._binder._sel_page is not None
                    and self._binder._sel_slot is not None
                    and self._binder.pages[self._binder._sel_page]
                    .slots[self._binder._sel_slot]
                    .url
                ):
                    page = self._binder.pages[self._binder._sel_page]
                    col = self._binder._sel_slot % self._binder.cols
                    row = self._binder._sel_slot // self._binder.cols
                    if row > 0:
                        row -= 1
                    else:
                        row = self._binder.rows - 1
                    new_slot = row * self._binder.cols + col
                    if (
                        not self._binder.pages[self._binder._sel_page]
                        .slots[new_slot]
                        .url
                    ):
                        self._binder.pages[self._binder._sel_page].slots[
                            new_slot
                        ].url = (
                            self._binder.pages[self._binder._sel_page]
                            .slots[self._binder._sel_slot]
                            .url
                        )
                        self._binder.pages[self._binder._sel_page].slots[
                            self._binder._sel_slot
                        ].url = ""
                        self._binder._sel_slot = new_slot
                    else:
                        return
                    # self._refresh_binder()
                    self._update_status(
                        f"Moved up to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                    )

                return
            if event.key() == Qt.Key.Key_Down:
                if (
                    self._binder._sel_page is not None
                    and self._binder._sel_slot is not None
                    and self._binder.pages[self._binder._sel_page]
                    .slots[self._binder._sel_slot]
                    .url
                ):
                    page = self._binder.pages[self._binder._sel_page]
                    col = self._binder._sel_slot % self._binder.cols
                    row = self._binder._sel_slot // self._binder.cols
                    if row < self._binder.rows - 1:
                        row += 1
                    else:
                        row = 0
                    new_slot = row * self._binder.cols + col
                    if (
                        not self._binder.pages[self._binder._sel_page]
                        .slots[new_slot]
                        .url
                    ):
                        self._binder.pages[self._binder._sel_page].slots[
                            new_slot
                        ].url = (
                            self._binder.pages[self._binder._sel_page]
                            .slots[self._binder._sel_slot]
                            .url
                        )
                        self._binder.pages[self._binder._sel_page].slots[
                            self._binder._sel_slot
                        ].url = ""
                        self._binder._sel_slot = new_slot
                    else:
                        return
                    # self._refresh_binder()
                    self._update_status(
                        f"Moved down to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                    )

                return
        if event.key() == Qt.Key.Key_Return or event.key() == Qt.Key.Key_Enter:
            print("Enter key pressed")
            if self._search_entry.hasFocus():
                self._do_search()
            elif (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                slot = self._binder.pages[self._binder._sel_page].slots[
                    self._binder._sel_slot
                ]
                selected_item = self._current_tree.currentItem()
                if selected_item and not slot.url:
                    self._place_selected()

            return

        if event.key() == Qt.Key.Key_Delete:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                slot = page.slots[self._binder._sel_slot]
                if not slot.empty:
                    slot.url = ""
                    self._unsaved = True
                    #    self._refresh_binder()
                    self._update_status(
                        f"Cleared card from page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                    )
            return

        if event.key() == Qt.Key.Key_Escape:
            if self._canvas.hasFocus() or any(
                widget.hasFocus() for widget in self._canvas._slot_widgets.values()
            ):
                self._binder._sel_page = None
                self._binder._sel_slot = None
                # self._refresh_binder()
            if self._current_tree.hasFocus():
                self._deselect_tree()
            self._update_status("Selection cleared.")
            return

        if event.key() == Qt.Key.Key_Tab:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                next_slot = (self._binder._sel_slot + 1) % len(page.slots)
                old_slot = self._binder._sel_slot
                self._binder._sel_slot = next_slot
                self._refresh_binder(slots=[(self._binder._sel_page, next_slot), (self._binder._sel_page, old_slot)])
                self._update_status(
                    f"Moved to next slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                )
            return
        if event.key() == Qt.Key.Key_Backtab:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                prev_slot = (self._binder._sel_slot - 1) % len(page.slots)
                curr_slot = self._binder._sel_slot
                self._binder._sel_slot = prev_slot
                self._refresh_binder(slots=[(self._binder._sel_page, prev_slot), (self._binder._sel_page, curr_slot)])
                self._update_status(
                    f"Moved to previous slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                )
            return
        if event.key() == Qt.Key.Key_Left:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                page_idx = self._binder._sel_page
                slot_idx = self._binder._sel_slot
                col = self._binder._sel_slot % self._binder.cols
                row = self._binder._sel_slot // self._binder.cols
                if col == 0:
                    if self._binder._sel_page > 0:
                        self._binder._sel_page -= 1

                        self._goto_page(self._binder._sel_page)
                        self._binder._sel_slot = (
                            self._binder.cols - 1
                        ) + row * self._binder.cols
                        self._refresh_binder(slots=[(page_idx, slot_idx), (self._binder._sel_page, self._binder._sel_slot)])
                        self._update_status(
                            f"Moved to previous page: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                        )
                        return
                    else:
                        col = self._binder.cols - 1

                elif col > 0:
                    col -= 1

                new_slot = row * self._binder.cols + col
                self._binder._sel_slot = new_slot
                self._refresh_binder()
                self._update_status(
                    f"Moved left to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                )
            return
        if event.key() == Qt.Key.Key_Right:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                col = self._binder._sel_slot % self._binder.cols
                row = self._binder._sel_slot // self._binder.cols
                if col == self._binder.cols - 1:
                    print(
                        f"At last column of page {self._binder._sel_page + 1}, moving to next page if available."
                    )
                    if self._binder._sel_page < len(self._binder.pages) - 1:

                        self._binder._sel_page += 1

                        self._goto_page(self._binder._sel_page)
                        self._binder._sel_slot = row * self._binder.cols
                        self._refresh_binder()
                        self._update_status(
                            f"Moved to next page: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                        )
                        return
                    else:
                        col = 0
                elif col < self._binder.cols - 1:
                    col += 1
                new_slot = row * self._binder.cols + col
                self._binder._sel_slot = new_slot
                self._refresh_binder()
                self._update_status(
                    f"Moved right to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                )
            return
        if event.key() == Qt.Key.Key_Up:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                col = self._binder._sel_slot % self._binder.cols
                row = self._binder._sel_slot // self._binder.cols
                if row > 0:
                    row -= 1
                else:
                    row = self._binder.rows - 1
                new_slot = row * self._binder.cols + col
                self._binder._sel_slot = new_slot
                self._refresh_binder()
                self._update_status(
                    f"Moved up to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                )
            return
        if event.key() == Qt.Key.Key_Down:
            if (
                self._binder._sel_page is not None
                and self._binder._sel_slot is not None
            ):
                page = self._binder.pages[self._binder._sel_page]
                col = self._binder._sel_slot % self._binder.cols
                row = self._binder._sel_slot // self._binder.cols
                if row < self._binder.rows - 1:
                    row += 1
                else:
                    row = 0
                new_slot = row * self._binder.cols + col
                self._binder._sel_slot = new_slot
                self._refresh_binder()
                self._update_status(
                    f"Moved down to slot: page {self._binder._sel_page + 1}, slot {self._binder._sel_slot + 1}"
                )
            return

        super().keyReleaseEvent(event)

    @Slot()
    def _on_grid_change(self) -> None:
        """Handle grid size change."""
        cols = self._grid_h_spinbox.value()
        rows = self._grid_v_spinbox.value()

        if cols == self._binder.cols and rows == self._binder.rows:
            return

        reply = QMessageBox.question(
            self,
            "Change Grid Size",
            f"Resize all pages to {cols}x{rows}?\nCards beyond the new size will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            self._grid_h_spinbox.setValue(self._binder.cols)
            self._grid_v_spinbox.setValue(self._binder.rows)
            return

        new_data = BinderData(cols, rows)
        new_data.pages.clear()
        for old_page in self._binder.pages:
            new_page = BinderPage(cols, rows, old_page.label)
            cap = min(len(old_page.slots), len(new_page.slots))
            for i in range(cap):
                new_page.slots[i] = old_page.slots[i]
            new_data.pages.append(new_page)

        self._binder = new_data
        self._spread_idx = 0
        self._binder._sel_page = None
        self._binder._sel_slot = None
        self._unsaved = True
        self._image_cache.clear_pixmap_cache()
        self._refresh_binder("")
        self._update_status("Grid resized.")

    def _repopulate_custom_groups(self) -> None:
        """Re-insert all custom group nodes after a pool clear."""
        for url, grp in list(self._binder._custom_groups.items()):
            for child in grp.get("children", {}):
                print("url", url, "child", child)
                if Path(CUSTOM_DIR / url.replace("custom://", "")).exists():
                    self._on_custom_card_added(grp)
                    break
                elif url.startswith("custom://"):
                    for page in self._binder.pages:
                        for slot in page.slots:
                            if slot.url == child:
                                slot.url = None
                                # self._update_slot_widgets(slots=[(self._binder.pages.index(page), page.slots.index(slot))], url=None)
                    

    def _on_tab_change(self, event) -> None:
        last_tab = self.tab_state
        tab_index = self._tab_widget.currentIndex()
        self.tab_state = TABS[tab_index]
        # self._pool_iid_by_url.clear()
        # self._picker_tree.clear()
        # print(self._pool_info)
        print(f"Switched from {last_tab} to {self.tab_state} tab")
        if self.tab_state == "History":
            self._current_tree = self._history_tree
        if self.tab_state == "Custom Cards":
            self._current_tree = self._custom_tree
        if self.tab_state == "Search":
            self._current_tree = self._picker_tree
        # self._refresh_binder()

    @Slot(int)
    def _on_slider_change(self, val: int) -> None:
        """Handle spread slider change."""
        self._spread_idx = val
        self._refresh_binder()
        self._update_status(f"Navigated to spread {val + 1}")

    @Slot()
    def _add_page(self) -> None:
        """Add new page to binder."""
        insert_idx = (
            (self._binder._sel_page + 1)
            if self._binder._sel_page is not None
            else len(self._binder.pages)
        )
        new_page = BinderPage(
            self._binder.cols, self._binder.rows, f"Page {insert_idx + 1}"
        )
        self._binder.pages.insert(insert_idx, new_page)
        self._unsaved = True
        self._binder._sel_page = None
        self._spread_idx = insert_idx // 2
        self._refresh_binder(pages=[])
        self._update_status(f"Added page at position {insert_idx + 1}")

    @Slot()
    def _remove_page(self) -> None:
        """Remove current page from binder."""
        if len(self._binder.pages) <= 1:
            self._update_status("Cannot remove the last page.")
            return

        page_idx = (
            self._binder._sel_page
            if self._binder._sel_page is not None
            else len(self._binder.pages) - 1
        )

        if page_idx >= len(self._binder.pages):
            page_idx = len(self._binder.pages) - 1

        page = self._binder.pages[page_idx]
        if any(not s.empty for s in page.slots):
            reply = QMessageBox.question(
                self,
                "Remove Page",
                f"Page {page_idx + 1} has cards in it. Remove anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._binder.pages.pop(page_idx)
        self._unsaved = True

        if self._binder._sel_page is not None:
            if page_idx < len(self._binder.pages):
                self._binder._sel_page = page_idx
            else:
                self._binder._sel_page = len(self._binder.pages) - 1

        self._spread_idx = min(
            self._spread_idx, max(0, (len(self._binder.pages) - 1) // 2)
        )
        self._refresh_binder(pages=[range(len(self._binder_pages))[page_idx:]])
        self._update_status(f"Removed page {page_idx + 1}")

    def _show_page_menu(self, pos: QPoint, idx: int) -> None:
        """Show context menu for page."""
        menu = QMenu(self)
        menu.addAction("Go to Page", lambda: self._goto_page(idx))
        menu.addAction("Rename...", lambda: self._rename_page(idx))
        menu.popup((pos))

    def _rename_page(self, idx: int) -> None:
        """Rename a page."""
        new_name, ok = QInputDialog.getText(
            self, "Rename Page", f"Enter new name for Page {idx + 1}:"
        )
        if ok and new_name:
            self._binder.pages[idx].label = new_name
            self._unsaved = True
            self._refresh_binder(pages=[idx])
            self._update_status(f"Renamed Page {idx + 1} to '{new_name}'")
        pass

    def _goto_page(self, page_idx: int) -> None:
        """Navigate to page."""
        self._spread_idx = page_idx // 2
        self._binder._sel_page = page_idx
        self._binder._sel_slot = None
        # self._update_viewable_slots()
        self._refresh_binder(pages = [])
        self._update_status(f"Navigated to Page {page_idx + 1}")

    def _on_empty_space_clicked(self):
        prev_sel_page = int(self._binder._sel_page) if self._binder._sel_page is not None else None
        prev_sel_slot = int(self._binder._sel_slot) if self._binder._sel_slot is not None else None
        if prev_sel_page is not None:
            self._binder._sel_page = None
        if prev_sel_slot is not None:
            self._binder._sel_slot = None
        print(prev_sel_page, prev_sel_slot)
        self._refresh_binder(slots=[prev_sel_slot] if prev_sel_slot else None)
        self._refresh_binder(pages=[prev_sel_page] if prev_sel_page else None)
    @Slot(int, int)
    def _on_slot_widget_double_clicked(self, page_idx: int, slot_idx: int) -> None:
        """Handle slot widget double-click."""
        vw_pgs = self._get_viewable_pages()
        self._binder._sel_page = page_idx
        self._binder._sel_slot = slot_idx
        slot = self._binder.pages[page_idx].slots[slot_idx]
        print(
            f"Double-clicked slot {slot_idx} on page {page_idx}, current item: {self._current_tree.currentItem()}"
        )
        if self._current_tree.currentItem() is not None:
            url = self._current_tree.currentItem().data(4, Qt.ItemDataRole.UserRole + 1)
            if url:
                slot.url = url
                self._unsaved = True
                self._add_to_history_pool(url)
                self._refresh_binder(slots=[(page_idx, slot_idx)])
                self._update_status(
                    f"Placed card in slot {slot_idx + 1} / page {page_idx + 1}"
                )
            self._on_slot_widget_clicked(page_idx, slot_idx)

    @Slot(int, int)
    def _on_slot_widget_clicked(self, page_idx: int, slot_idx: int) -> None:
        """Handle slot widget click."""
        vw_pgs = self._get_viewable_pages()
        self._binder._sel_page = page_idx
        self._binder._sel_slot = slot_idx
        print("new slot selected,", page_idx, slot_idx, vw_pgs)
        # self._binder._sel_slots.append((page_idx, slot_idx))
        slot = self._binder.pages[page_idx].slots[slot_idx]
        self._picked_url = slot.url if not slot.empty else None
        if slot.url:
            self._on_tree_select(url=slot.url, info=self._pool_info.get(slot.url))
        self._update_status(
            f"Slot {slot_idx + 1} / Page {page_idx + 1}"
            + (f" — {slot.display_name}" if not slot.empty else " (empty)")
        )

        self._refresh_binder(slots=[(page_idx, slot_idx)], diffs={})

    @Slot(int, int)
    def _on_slot_widget_right_clicked(self, page_idx: int, slot_idx: int) -> None:
        """Handle slot widget right-click."""
        vw_pgs = self._get_viewable_pages()
        self._binder._sel_page = page_idx
        self._binder._sel_slot = slot_idx
        self._refresh_binder(slots=[(page_idx, slot_idx)], diffs={})
        menu = QMenu(self)
        if self._binder.pages[self._binder._sel_page].slots[self._binder._sel_slot].url:
            menu.addAction("Open in Browser", self._ctx_open_in_browser)
            menu.addAction("Remove Card", self._ctx_remove)
            menu.addAction("Copy slot", self._copy_slot)
            menu.addAction("Paste", self._paste_slot)
            menu.addSeparator()
            menu.addAction("Shift Left", lambda: self._ctx_shift(-1))
            menu.addAction("Shift Right", lambda: self._ctx_shift(+1))
            menu.addSeparator()
            sleeve_action = menu.addAction(f"Sleeve", self._toggle_sleeve)
            sleeve_action.setCheckable(True)
            sleeve_action.setChecked(
                self._binder.pages[self._binder._sel_page]
                .slots[self._binder._sel_slot]
                ._sleeved
            )
            menu.addAction("Edit sleeve color...", self._edit_sleeve_color)
        else:
            menu.addAction("Paste", self._paste_slot)

            if self._picked_url:
                menu.addAction("Place Card from Pool", self._place_selected)
            menu.addSeparator()
            sleeve_action = menu.addAction(f"Sleeve", self._toggle_sleeve)
            sleeve_action.setCheckable(True)
            sleeve_action.setChecked(
                self._binder.pages[self._binder._sel_page]
                .slots[self._binder._sel_slot]
                ._sleeved
            )
            sleeve_color_action = menu.addAction(
                "Edit sleeve color...", self._edit_sleeve_color
            )
            icon = self._create_color_icon(
                self._binder.pages[self._binder._sel_page]
                .slots[self._binder._sel_slot]
                ._sleeve_color
            )
            sleeve_color_action.setIcon(icon)
        menu.popup(QCursor.pos())

    def _create_color_icon(self, color_hex=None) -> QIcon:
        """Create a QIcon with the given color."""
        pixmap = QPixmap(32, 16)
        if color_hex:
            color = QColor(color_hex)
        else:
            color = QColor(255, 255, 255)  # Default to white if no color is set
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(QColor(0, 0, 0))  # Black border
        painter.setBrush(QColor(color_hex) if color_hex else QColor(255, 255, 255))
        painter.drawRect(16, 0, 15, 15)  # Draw border rectangle
        painter.end()
        return QIcon(pixmap)

    def _toggle_sleeve(self) -> None:
        """Toggle sleeve for selected slot."""
        if self._binder._sel_page is None or self._binder._sel_slot is None:
            return
        slot = self._binder.pages[self._binder._sel_page].slots[self._binder._sel_slot]
        slot._sleeved = not slot._sleeved
        self._unsaved = True
        self._refresh_binder(slots=[(self._binder._sel_page, self._binder._sel_slot)])
        self._update_status(
            f"Sleeve {'enabled' if slot._sleeved else 'disabled'} for slot {self._binder._sel_slot + 1} / page {self._binder._sel_page + 1}"
        )

    def _edit_sleeve_color(self) -> None:
        """Edit sleeve color for selected slot."""
        if self._binder._sel_page is None or self._binder._sel_slot is None:
            return
        slot = self._binder.pages[self._binder._sel_page].slots[self._binder._sel_slot]
        initial_color = (
            QColor(slot._sleeve_color).name() if slot._sleeve_color else QColor(255, 255, 255).name()
        )
        color = QColorDialog.getColor(initial_color, self, "Select sleeve color")
        if color.isValid():
            slot._sleeve_color = color.name()
            self._unsaved = True
            self._refresh_binder(slots=[(self._binder._sel_page, self._binder._sel_slot)])
            self._update_status(
                f"Sleeve color updated for slot {self._binder._sel_slot + 1} / page {self._binder._sel_page + 1}"
            )

    @Slot(int, int, int, int)
    def _on_slot_widget_dropped(
        self, src_page: int, src_slot: int, dst_page: int, dst_slot: int
    ) -> None:
        """Handle card drop between slots."""
        print(
            f"Dropped from page {src_page}, slot {src_slot} to page {dst_page}, slot {dst_slot}"
        )
        self._on_slot_dragged(src_page, src_slot, dst_page, dst_slot)

    # @Slot(int, int)
    # def _on_slot_clicked(self, page_idx: int, slot_idx: int) -> None:
    #     """Handle slot click."""
    #     vw_pgs = self._get_viewable_pages()
    #     self._binder._sel_page = page_idx
    #     self._binder._sel_slot = slot_idx
    #     print(page_idx, slot_idx, "click data")
    #     print("new slot select")
    #     slot = self._binder.pages[page_idx].slots[slot_idx]
    #     if slot.url:
    #         self._on_tree_select(url=slot.url, info=self._pool_info.get(slot.url))
    #     self._update_status(
    #         f"Slot {slot_idx + 1} / Page {page_idx + 1}"
    #         + (f" — {slot.display_name}" if not slot.empty else " (empty)")
    #     )
    #     self._refresh_binder()

    @Slot(int, int)
    def _on_slot_right_clicked(self, page_idx: int, slot_idx: int) -> None:
        """Handle slot right-click."""
        vw_pgs = self._get_viewable_pages()
        self._binder._sel_page = page_idx
        self._binder._sel_slot = slot_idx
        menu = QMenu(self)
        menu.addAction("Remove Card", self._ctx_remove)
        menu.addAction("Open in Browser", self._ctx_open_in_browser)
        menu.addSeparator()
        menu.addAction("Shift Left", lambda: self._ctx_shift(-1))
        menu.addAction("Shift Right", lambda: self._ctx_shift(+1))
        menu.popup(QCursor.pos())

    def _ctx_remove(self) -> None:
        """Remove card from slot."""
        if self._binder._sel_page is not None and self._binder._sel_slot is not None:
            self._binder.pages[self._binder._sel_page].slots[
                self._binder._sel_slot
            ] = BinderSlot()
            self._unsaved = True
            self._refresh_binder(slots=[(self._binder._sel_page, self._binder._sel_slot)])
            self._update_status("Card removed.")

    def _ctx_open_in_browser(self, url: str = None) -> None:
        """Open card URL in browser."""
        if url is None:
            if self._binder._sel_page is None or self._binder._sel_slot is None:
                return
            slot = self._binder.pages[self._binder._sel_page].slots[self._binder._sel_slot]
            if slot.url:
                url = slot.url
        if url:
            QDesktopServices.openUrl(QUrl(url))
            self._update_status("Opened in browser.")

    def _ctx_shift(self, delta: int) -> None:
        """Shift card left/right."""
        if self._binder._sel_page is None or self._binder._sel_slot is None:
            return
        page = self._binder.pages[self._binder._sel_page]
        si = self._binder._sel_slot
        ti = si + delta
        if 0 <= ti < len(page.slots):
            page.slots[si], page.slots[ti] = page.slots[ti], page.slots[si]
            self._binder._sel_slot = ti
            self._unsaved = True
            self._refresh_binder(slots=[(self._binder._sel_page, si), (self._binder._sel_page, ti)])
            self._update_status(f"Card shifted {'left' if delta < 0 else 'right'}.")

    def _bg_fetch_url(self, url: str, picker=True) -> None:
        """Background: fetch and cache card info. Runs off the main thread —
        must not touch GUI objects directly; emit signals instead."""
        info = None

        # print(f"Fetching card info for {url}...")
        if not Path(database.DB_DIR / "binder.db").exists():
            database.init_db()
        db = SessionLocal()
        # print(f"Database session opened for {url}.")
        try:

            cached = db.query(CachedCard).filter_by(card_url=url).first()
            if cached and cached.card_url == url:
                info = cached.__dict__
            # print(f"Queried database for {url}: {info}")
            if not info:
                # print("Failed to query database for cached card info.")
                info = fetch_card_info(url, fetch_prices=True, fetch_images=True)
                price_data = {
                    "tcgplayer_price_data": info.pop("tcgplayer_price_data", None),
                    "cm_price_data": info.pop("cm_price_data", None),
                }

                # print(f"Fetched info for {url}: {info.keys() if info else 'None'}")
                if info:
                    cached_card = CachedCard(
                        **info,
                    )
                    db.add(cached_card)
                    price_data = CardPrices(
                        card_url=url,
                        tcgplayer_price_data=price_data["tcgplayer_price_data"],
                        cm_price_data=price_data["cm_price_data"],
                    )
                    db.commit()
                else:
                    self.search_status.emit(f"Failed to fetch card info for {url}")
                    return
        except Exception as e:
            db.close()
            self.search_status.emit(f"Error fetching card info for {url}")
            return

        self.card_info_fetched.emit(url, info, picker)

    @Slot(str, object)
    def _on_card_info_fetched(self, url: str, info: dict, picker: bool) -> None:
        """Main-thread handler for card info fetched in the background."""
        self._pool_info[url] = info
        print(f"Card info fetched for {url}: {list(info.keys() if info else 'None')}")
        self.image_from_cache_async(url, info.get("image_url"))
        if picker:
            self._add_to_pool(url, info)
        self._update_status(f"Fetched: {info.get('card_name', 'Unknown')}")

    def image_from_cache_async(self, url: str, image_url: str | None) -> None:
        """Background: fetch and cache card image."""
        if not image_url:
            self._update_status(f"No image URL for {url}")
            return
        if self._image_cache.is_cached(image_url):
            self._on_image_cached(image_url)
            return
        print(f"STARTING IMAGE CACHE GRAB FOR {image_url}")
        worker = ImageCacherWorker(url, self._thread_pool_signals_cache, self._fetching)
        try:
            worker.signals.image_cached.disconnect()
        except TypeError:
            pass  # Signal was not connected yet
        worker.signals.image_cached.connect(self._on_image_from_cache)
        # worker.signals.fetch_failed.connect(self._on_cache_failed)
        self._thread_pool.start(worker)
        self._fetching.add(image_url)

    def _on_image_from_cache(self, url: str, img: QPixmap) -> None:
        """Main-thread handler for image cached in the background."""
        # print(f"on_image_from_cache: {url}")
        self._image_cache.cache_pil_image(url, img)
        self._image_cache.create_fullsize_pixmap(url)
        self._on_image_loaded(url)
        self._update_status(f"Image cached for {url}")

    def _load_card_file(self) -> None:
        """Load card list from file."""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open card list",
            str(WORKSPACE),
            "Text files (*.txt);;All files (*)",
        )
        if not path:
            return
        self._update_status(f"Loading cards from {Path(path).name}...")
        self._picker_tree.clear()
        threading.Thread(
            target=self._bg_load_card_file, args=(path,), daemon=True
        ).start()

    def _bg_load_card_file(self, path: str) -> None:
        """Background: load card URLs from file."""
        urls = []
        try:
            for line in Path(path).read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.strip().startswith(("#")):
                    continue
                line = line.strip().lstrip("!*#").strip()
                if line and "limitlesstcg.com" in line:
                    url = line.split()[0]
                    if url not in urls:
                        urls.append(url)
        except Exception as e:
            self.search_status.emit(f"Error loading file")
            return

        if urls:
            self.search_status.emit(f"Loaded {len(urls)} card(s) — fetching info...")
            self._batch_fetch_info(urls)
        else:
            self.search_status.emit("No valid URLs found in file.")

    def _do_search(self) -> None:
        """Search for cards on Limitless TCG."""
        q = self._search_entry.text().strip()
        if not q:
            return
        self._update_status("Searching...")
        # self._search_entry.clear()
        self._picker_tree.clear()
        self._pool_iid_by_url.clear()
        threading.Thread(target=self._bg_search, args=(q,), daemon=True).start()

    def _bg_search(self, query: str) -> None:
        """Background: search Limitless TCG."""
        try:
            url = f"https://limitlesstcg.com/cards?q={quote(query, safe='')}&show=all"
            resp = requests.get(url, headers=HEADERS, timeout=0.5)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            seen, hrefs = set(), []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if _CARD_PATH_RE.match(href):
                    full = "https://limitlesstcg.com" + href
                    if full not in seen:
                        seen.add(full)
                        hrefs.append(full)
            if hrefs:
                self.search_status.emit(f"Found {len(hrefs)} cards — fetching info...")
                self._batch_fetch_info(hrefs)
            else:
                self.search_status.emit("No cards found.")
        except Exception as e:
            self.search_status.emit(f"Search failed: {e}")
            return
    def _batch_fetch_info(self, urls: list[str]) -> None:
        """Fetch card info for a list of URLs."""
        for url in urls:
            self._bg_fetch_url(url)

    def _add_custom_to_pool(self, custom_group) -> None:
        """Add a custom card group to the pool."""
        main_item = QTreeWidgetItem()
        main_item.setText(
            0,
            custom_group.get(
                "card_name", f"Custom Card {len(self._binder._custom_groups) + 1}"
            ),
        )
        main_item.setText(1, f"{custom_group['rows']}x{custom_group['cols']}")
        main_item.setData(4, Qt.ItemDataRole.UserRole + 1, custom_group["url"])
        main_item.setExpanded(True)
        main_item.setIcon(
            0,
            (
                QIcon(self._image_cache.get_cached_pixmap(custom_group["url"]))
                if self._image_cache.get_cached_pixmap(custom_group["url"])
                else QIcon()
            ),
        )
        # 'card_url', 'card_name', 'image_url', 'id', 'card_type', 'lang', 'image_data', 'rarity', 'created_at', 'set_code', 'usd_price', 'tcgplayer_set_code', 'eur_price', 'set_name', 'tcgplayer_url', 'release_date', 'cardmarket_url', 'number', 'symbol_bytes
        # main_dim = custom_group["main_dim"]
        # print(main_dim    )
        self._custom_tree.addTopLevelItem(main_item)
        subgroup = {}
        if isinstance(custom_group["children"], list):
            to_parse = custom_group["children"]
        if isinstance(custom_group["children"], dict):
            to_parse = []
            for url, info in custom_group["children"].items():
                self._image_cache.get_cached_pil_image(url)  # Ensure image is cached
                img = self._image_cache.get_cached_pixmap(url)
                to_parse.append((url, img))
        for idx, (url, img) in enumerate(to_parse):
            subgroup[url] = {
                "card_name": f"Custom Card ({idx + 1})",
                "set_code": "",
                "number": "",
                "release_date": "",
            }
            item = QTreeWidgetItem()
            # item.setText(0, url)
            item.setIcon(0, QIcon(img))
            item.setText(
                1,
                f"({idx // custom_group['cols'] + 1}, {idx % custom_group['cols'] + 1})",
            )
            # item.setText(2, "")
            item.setData(4, Qt.ItemDataRole.UserRole + 1, url)
            main_item.addChild(item)
        self._binder._custom_groups[custom_group["url"]] = {
            "url": custom_group["url"],
            "card_name": custom_group.get("card_name", f"Custom Card {len(self._binder._custom_groups) + 1}"),
            "set_code": "",
            "number": "",
            "release_date": "",
            "children": subgroup,
            "cols": custom_group["cols"],
            "rows": custom_group["rows"],
        }
        self._add_to_history_pool(custom_group["url"])

    def _add_to_pool(self, url: str, info: dict | None, draw: bool = True) -> None:
        """Add/update card in picker pool."""
        # print(f"Adding card to pool: {url} — {info.get('card_name', '')}")
        # if url.startswith("custom://"):
        # Custom cards are not cached in the database, so we just add them directly
        # self._pool_info[url] = info or {}
        iid = self._pool_iid_by_url.get(url)
        # print(f"Pool item ID for {url}: {iid}")
        urls = [slt.url for page in self._binder.pages for slt in page.slots if slt.url]
        if iid:

            # Update existing item
            name = f"{info.get('card_name', '')}{' ☑' if url in urls else ''}" if info else ""
            set_code = info.get("set_code", "") if info else ""
            number = info.get("number", "") if info else ""
            date = info.get("release_date", "") if info else ""
            # iid.setIcon(0, QIcon(self._image_cache.get_cached_pixmap(url) or QPixmap()))
            iid.setText(0, name or f"{set_code} #{number}")
            iid.setText(1, set_code)
            iid.setText(2, number)
            iid.setText(
                3, date.strftime("%Y-%m-%d") if isinstance(date, datetime) else date
            )
            self._current_tree.update()
        else:
            
            name = f"{info.get('card_name', '')}{' ☑' if url in urls else ''}" if info else ""
            set_code = info.get("set_code", "") if info else ""
            number = info.get("number", "") if info else ""
            date = info.get("release_date", "") if info else ""
            print(f"Adding new item to pool: {url} — {name or f'{set_code} #{number}'}")
            m = _URL_RE.search(url)
            if not set_code:
                set_code = m.group(1).upper() if m else ""
            if not number:
                number = m.group(2) if m else ""

            item = QTreeWidgetItem()
            # item.setIcon(0, QIcon(self._image_cache.get_cached_pixmap(url) or QPixmap()))
            item.setText(0, name or f"{set_code} #{number}")
            item.setText(1, set_code)
            item.setText(2, str(number))
            item.setText(
                3, date.strftime("%Y-%m-%d") if isinstance(date, datetime) else date
            )
            item.setData(4, Qt.ItemDataRole.UserRole + 1, url)
            self._pool_iid_by_url[url] = item
            self._current_tree.insertTopLevelItem(0, item)
            print(f"Added card to pool: {url} — {name or f'{set_code} #{number}'}")
            self._current_tree.update()
        # if draw:
        #     self._refresh_binder()
        if url == self._picked_url:
            self._on_tree_select(url)

    def _deselect_tree(self):
        self._current_tree.clearSelection()
        self._picked_url = None
        self._preview_name_label.setText("— no card selected —")
        self._preview_image_label.setPixmap(QPixmap())
        return

    @Slot()
    def _on_tree_select(self, url=None, info=None):
        if url is None:
            sel = self._current_tree.selectedItems()
            if not sel:
                self._picked_url = None
                self._preview_name_label.setText("— no card selected —")
                self._preview_image_label.setPixmap(QPixmap())
                return
            item = sel[0]
            url = item.data(4, Qt.ItemDataRole.UserRole + 1)
        print(f"Selected card in pool: {url}")
        if info is None:
            info = self._pool_info.get(url, {})
        self._picked_url = url
        name = info.get("card_name", "Unknown Card")
        for key, value in info.items():
            if key == "image_data":
                continue
            print(f"{key}: {value}")
        self._preview_name_label.setText(name)
        pixmap = self._image_cache.get_cached_pixmap(url)
        print(f"Preview pixmap for {url}: {'found' if pixmap else 'not found'}")
        if pixmap:
            self._preview_image_label.setPixmap(
                pixmap.scaled(
                    self._preview_image_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )

        else:
            self._preview_image_label.setPixmap(QPixmap())
            self._fetch_image_async(url)

    def _on_custom_tree_click(self, event) -> None:
        """Handle click in custom card tree."""
        # On right-click, show context menu for custom group or card
        if event.button() == Qt.MouseButton.RightButton:
            item = self._custom_tree.itemAt(event.pos())
            if item:
                menu = QMenu(self)
                url = item.data(4, Qt.ItemDataRole.UserRole + 1)
                if url in self._binder._custom_groups:
                    menu.addAction(
                        "Remove Custom Group", lambda: self._remove_custom_group(url)
                    )
                    menu.popup(QCursor.pos())
                else:

                    menu.addAction(
                        "Remove Custom Card", lambda: self._remove_custom_card(url)
                    )
                    menu.popup(QCursor.pos())
                menu.addAction(
                    "Rename Custom Card", lambda: self._rename_custom_group(url)
                )
                menu.popup(QCursor.pos())
        else:
            super(QTreeWidget, self._custom_tree).mousePressEvent(event)

    def _remove_custom_group(self, main_url: str) -> None:
        """Remove a custom card group."""
        if main_url in self._binder._custom_groups:
            del self._binder._custom_groups[main_url]
            for i in range(self._custom_tree.topLevelItemCount()):
                item = self._custom_tree.topLevelItem(i)
                if item.data(4, Qt.ItemDataRole.UserRole + 1) == main_url:
                    self._custom_tree.takeTopLevelItem(i)
                    break
            self._unsaved = True
            self._update_status(f"Removed custom group {main_url}")

    def _remove_custom_card(self, url: str) -> None:
        """Remove a custom card from its group."""
        for main_url, group in self._binder._custom_groups.items():
            if url in group["children"]:
                del group["children"][url]
                for i in range(self._custom_tree.topLevelItemCount()):
                    main_item = self._custom_tree.topLevelItem(i)
                    if main_item.data(4, Qt.ItemDataRole.UserRole + 1) == main_url:
                        for j in range(main_item.childCount()):
                            child_item = main_item.child(j)
                            if child_item.data(4, Qt.ItemDataRole.UserRole + 1) == url:
                                main_item.removeChild(child_item)
                                break
                        break
                self._unsaved = True
                self._update_status(f"Removed custom card {url}")
                return

    def _rename_custom_group(self, url: str) -> None:
        """Rename a custom group."""
        for main_url, group in self._binder._custom_groups.items():
            if url == main_url or url in group["children"]:
                new_name, ok = QInputDialog.getText(
                    self, "Rename Custom Group", f"Enter new name for {url}:"
                )
                if ok and new_name:
                    group["card_name"] = new_name
                    for i in range(self._custom_tree.topLevelItemCount()):
                        item = self._custom_tree.topLevelItem(i)
                        if item.data(4, Qt.ItemDataRole.UserRole + 1) == main_url:
                            item.setText(0, new_name)
                            break
                    self._unsaved = True
                    self._update_status(f"Renamed custom group {url} to '{new_name}'")
                return

    @Slot()
    def _on_tree_double(self, item):
        url = item.data(4, Qt.ItemDataRole.UserRole + 1)
        self._picked_url = url
        self._place_selected()

    @Slot()
    def _place_selected(self):
        if (
            not self._picked_url
            or self._binder._sel_page is None
            or self._binder._sel_slot is None
        ):
            self._update_status("Select a card and a slot to place.")
            return
        slot = self._binder.pages[self._binder._sel_page].slots[self._binder._sel_slot]
        slot.url = self._picked_url
        slot.info = self._pool_info.get(self._picked_url)
        self._unsaved = True
        self._add_to_history_pool(self._picked_url)
        print(f"History updated: {self._card_history}")
        self._refresh_binder(slots = [(self._binder._sel_page, self._binder._sel_slot)])
        self._update_status(
            f"Placed in slot {self._binder._sel_slot + 1} / Page {self._binder._sel_page + 1}"
        )

    def _find_custom_group_from_child_url(self, child_url: str) -> str | None:
        """Find the main URL of a custom group given a child URL."""
        for main_url, group in self._binder._custom_groups.items():
            if child_url in group["children"]:
                return main_url
        return None

    def _is_custom_child(self, url: str) -> bool:
        """Check if the URL belongs to a custom child card."""
        return self._find_custom_group_from_child_url(url) is not None

    def _add_to_history_pool(self, url: str) -> None:
        """Add card to history pool."""
        # print(f"Adding {url} to history pool")
        if url in self._card_history:
            return
        else:
            self._binder._card_history = self._card_history.copy()
            if self._picker_tree.selectedItems():
                picker_item = self._picker_tree.selectedItems()[0].clone()
            if url.startswith("custom://"):
                if self._is_custom_child(url):
                    main_url = self._find_custom_group_from_child_url(url)
                else:
                    main_url = url
                if main_url and main_url not in self._card_history:
                    grp = self._binder._custom_groups.get(main_url)
                    # print(f"Adding custom group {main_url} to history pool, {grp}")
                    item_to_insert = QTreeWidgetItem()
                    item_to_insert.setText(
                        0,
                        grp.get(
                            "card_name", "Unknown Card"
                        ),
                    )
                    item_to_insert.setText(
                        1, grp.get("set_code", "")
                    )
                    item_to_insert.setText(
                        2, grp.get("number", "")
                    )
                    date = grp.get("release_date", "")
                    item_to_insert.setText(
                        3,
                        date.strftime("%Y-%m-%d")
                        if isinstance(date, datetime)
                        else date,
                    )
                    item_to_insert.setData(4, Qt.ItemDataRole.UserRole + 1, main_url)
                    item_to_insert.setIcon(
                        0,
                        QIcon(
                            self._image_cache.get_cached_pixmap(main_url)
                            or QPixmap()
                        ),
                    )
                    self._card_history_iid_by_url[main_url] = item_to_insert
                    for child_url in self._binder._custom_groups[main_url]["children"]:
                        child_item_to_insert = QTreeWidgetItem()
                        child_item_to_insert.setText(
                            0,
                            self._binder._custom_groups[main_url]["children"].get(child_url, {}).get(
                                "card_name", "Unknown Card"
                            ),
                        )
                        child_item_to_insert.setText(
                            1,
                            self._binder._custom_groups[main_url]["children"].get(child_url, {}).get(
                                "set_code", ""
                            ),
                        )
                        child_item_to_insert.setText(
                            2,
                            self._binder._custom_groups[main_url]["children"].get(child_url, {}).get(
                                "number", ""
                            ),
                        )
                        date = self._binder._custom_groups[main_url]["children"].get(child_url, {}).get(
                            "release_date", ""
                        )
                        child_item_to_insert.setText(
                            3,
                            date.strftime("%Y-%m-%d")
                            if isinstance(date, datetime)
                            else date,
                        )
                        child_item_to_insert.setData(
                            4, Qt.ItemDataRole.UserRole + 1, child_url
                        )
                        child_item_to_insert.setIcon(
                            0,
                            QIcon(
                                self._image_cache.get_cached_pixmap(child_url)
                                or QPixmap()
                            ),
                        )
                        item_to_insert.addChild(child_item_to_insert)   
                        # self._card_history_iid_by_url[child_url] = item_to_insert
                    item_to_insert.setExpanded(False)
                    self._history_tree.insertTopLevelItem(0, item_to_insert)
                    if url not in self._card_history:
                        self._card_history.insert(0, url)
            else:
                item_to_insert = QTreeWidgetItem()
                item_to_insert.setText(0, self._pool_info.get(url, {}).get("card_name", "Unknown Card"))
                item_to_insert.setText(1, self._pool_info.get(url, {}).get("set_code", ""))
                item_to_insert.setText(2, self._pool_info.get(url, {}).get("number", ""))
                date = self._pool_info.get(url, {}).get("release_date", "")
                item_to_insert.setText(3, date.strftime("%Y-%m-%d") if isinstance(date, datetime) else date)
                item_to_insert.setData(4, Qt.ItemDataRole.UserRole + 1, url)
                item_to_insert.setIcon(0, QIcon(self._image_cache.get_cached_pixmap(url) or QPixmap()))
                self._history_tree.insertTopLevelItem(
                    0, item_to_insert
                )
                self._card_history_iid_by_url[url] = item_to_insert
                if url not in self._card_history:
                    self._card_history.insert(0, url)
        if len(self._card_history) > MAX_HISTORY:
            self._history_tree.takeTopLevelItem(MAX_HISTORY)
            self._card_history.pop()

        # self._refresh_history_pool()

    def _fetch_image_async(self, url, priority=True):
        # print("tjread" , QThread.Priority.LowestPriority)
        if url in self._fetching:
            # print(f"Already fetching image for {url}, skipping.")
            return
        
        self._fetching.add(url)
        worker = ImageFetcherWorker(url, self._thread_pool_signals)
        try:
            worker.signals.image_loaded.disconnect()
        except TypeError:
                pass  # Signal was not connected yet
        try:
            worker.signals.fetch_failed.disconnect()
        except TypeError:
                pass  # Signal was not connected yet
        worker.signals.image_loaded.connect(self._on_image_from_cache)
        worker.signals.fetch_failed.connect(self._on_fetch_failed)
        self._thread_pool.start(worker, priority=0 if not priority else 1)

    @Slot(str)
    def _on_image_loaded(self, url):
        # print(f"_on_image_loaded called for {url} by {inspect.stack()[1].function}")
        # print(f"Image loaded for {url}")
        self._fetching.discard(url)
        if url in self._pool_iid_by_url:
            item = self._pool_iid_by_url[url]
            item.setIcon(0, QIcon(self._image_cache.get_cached_pixmap(url)))
            if (
                self._current_tree.selectedItems()
                and self._current_tree.selectedItems()[0] == item
            ):
                pixmap = self._image_cache.get_cached_pixmap(url)
                if pixmap:
                    self._preview_image_label.setPixmap(
                        pixmap.scaled(
                            self._preview_image_label.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.SmoothTransformation,
                        )
                    )
        if url in self._card_history_iid_by_url:
            item = self._card_history_iid_by_url[url]
            item.setIcon(0, QIcon(self._image_cache.get_cached_pixmap(url)))
        self._update_slot_widgets(url=url)

        # self._refresh_binder()

    @Slot(str, str)
    def _on_fetch_failed(self, url, error):
        print(f"Image fetch failed for {url}: {error}")
        self._fetching.discard(url)

    @Slot()
    def _clear_history(self):
        self._history_tree.clear()
        self._card_history.clear()
        self._card_history_iid_by_url.clear()
    
    @Slot()
    def _clear_custom_groups(self):
        self._binder._custom_groups = {}
        self._custom_info = {}
        self._custom_tree.clear()
    @Slot()
    def _add_custom_card(self):
        self.setEnabled(False)
        dialog = CustomCardDialog(self, self._on_custom_card_added)
        dialog.closeEvent = lambda event: (self.setEnabled(True))
        dialog.show()

    def _on_custom_card_added(self, group: dict[str, list[str]]):
        self.setEnabled(True)
        group_id = f"custom_{uuid.uuid4().hex[:8]}"
        url = group["url"]
        path = CUSTOM_DIR / url.replace("custom://", "")
        try:
            img = PILImage.open(path).convert("RGBA")
        except Exception as e:
            print(url, str(e))
            return
        self._image_cache.cache_pil_image(f"{url}", img)
        self._image_cache.create_fullsize_pixmap(f"{url}")
        # self._binder.custom_groups[group_id] = {"main_url": f"custom://{url}", "children": [(url, self._image_cache.get_cached_pixmap(url)) for url in group["children"]], "cols": cols, "rows": rows}

        for url in group["children"]:
            img = PILImage.open(CUSTOM_DIR / url.replace("custom://", "")).convert(
                "RGBA"
            )
            self._image_cache.cache_pil_image(url, img)
            self._image_cache.create_fullsize_pixmap(url)
        # self._custom_groups[group_id] = {"label": f"Custom Group {len(self._custom_groups) + 1}", "tiles": [(url, self._image_cache.get_cached_pixmap(url)) for url in group["children"]], "cols": cols, "rows": rows}
        self._add_custom_to_pool(
            {
                "url": f"{group["url"]}",
                "card_name":group.get("card_name", "Custom group"),
                "children": [
                    (url, self._image_cache.get_cached_pixmap(url))
                    for url in group["children"]
                ],
                "cols": group["cols"],
                "rows": group["rows"],
            }
        )
        self._unsaved = True
        self._update_status(
            f"Added custom card group with {len(group['children'])} tile(s)"
        )

    def _clear_slot_widgets(self) -> None:
        """Clear cached slot widgets when layout changes."""
        self._renderer.clear_slot_widgets()

    def _redraw(self, to_redraw=None):
        """Apply a pending redraw request (delegates to BinderRenderer)."""
        self._renderer.redraw(to_redraw)

    def _get_viewable_pages(self):
        """Return the list of page indices currently visible on the canvas."""
        return self._renderer.get_viewable_pages()

    def _get_page_scale(self):
        """Return the scale factor that fits page(s) into the canvas."""
        return self._renderer.get_page_scale()

    def _get_page_pos(self, spread_pos):
        """Return the top-left (x, y) position for a page in the spread."""
        return self._renderer.get_page_pos(spread_pos)

    def _place_slots(self):
        """Move/resize/show slot widgets according to current page layout."""
        self._renderer.place_slots()

    def _update_slot_widgets(self, slots=None, url=None):
        """Create/refresh/prune CardSlotWidget instances for binder slots."""
        self._renderer.update_slot_widgets(slots=slots, url=url)
