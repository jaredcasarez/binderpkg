"""Binder/page/grid layout and rendering logic, bridging ``BinderData`` and the
``RoundedCornerCanvas`` it is painted onto."""


from typing import TYPE_CHECKING

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter, QPen
from painter_colors import get_colors

from .constants import GUTTER, OUTER_PAD, PAGE_PAD, SLOT_CORNER_RADIUS, SLOT_H, SLOT_W
from .widgets import CardSlotWidget

if TYPE_CHECKING:
    from .main_window import BinderApp
    from .widgets import RoundedCornerCanvas


class BinderRenderer:
    """Owns all binder/page/grid layout & rendering logic.

    This class is the single source of truth for how a binder is laid out
    onto a :class:`RoundedCornerCanvas`: which pages are currently viewable,
    where each page frame is drawn, where each slot widget is positioned,
    and how/when slot widgets are created, updated, or torn down.

    ``BinderApp`` owns the data (``BinderData``) and reacts to user
    interaction; ``RoundedCornerCanvas`` owns the paint surface and input
    events. This renderer bridges the two: it is created by ``BinderApp``,
    handed a reference to the canvas it draws onto, and is the object whose
    ``paint`` method the canvas calls from its ``paintEvent``.
    """

    def __init__(self, app: "BinderApp", canvas: "RoundedCornerCanvas"):
        self._app = app
        self._canvas = canvas
        self._page_view = "double"  # "single" or "double"
        self._spread_idx = 0
        self._slot_widgets: dict[tuple[int, int], "CardSlotWidget"] = {}
        
    # ── binder/data access ──────────────────────────────────────────────
    @property
    def _binder(self):
        return self._app._binder

    # ── page-view / spread navigation state ─────────────────────────────
    @property
    def page_view(self) -> str:
        return self._page_view

    @page_view.setter
    def page_view(self, view: str) -> None:
        self._page_view = view

    @property
    def spread_idx(self) -> int:
        return self._spread_idx

    @spread_idx.setter
    def spread_idx(self, idx: int) -> None:
        self._spread_idx = idx

    @property
    def slot_widgets(self) -> dict[tuple[int, int], "CardSlotWidget"]:
        return self._slot_widgets

    # ── layout / geometry ────────────────────────────────────────────────
    def get_viewable_pages(self) -> list[int]:
        """Return the list of page indices currently visible in the canvas."""
        n_pages = len(self._binder.pages)
        if self._page_view == "single":
            idx = (
                self._binder._sel_page
                if self._binder._sel_page is not None
                else self._spread_idx * 2
            )
            if idx < n_pages:
                return [idx]
            return []
        else:
            left_idx = self._spread_idx * 2
            right_idx = left_idx + 1
            pgs = []
            if left_idx < n_pages:
                pgs.append(left_idx)
            if right_idx < n_pages:
                pgs.append(right_idx)
            return pgs

    def get_page_scale(self) -> float:
        """Compute the scale factor that fits page(s) into the canvas."""
        c = self._canvas
        cw, ch = c.width(), c.height()
        page_w = SLOT_W * self._binder.cols + PAGE_PAD * 2
        page_h = SLOT_H * self._binder.rows + PAGE_PAD * 2
        if self._page_view == "single":
            available_w = cw - 2 * OUTER_PAD
        else:
            available_w = ((cw - GUTTER) // 2) - 2 * OUTER_PAD
        available_h = ch - 2 * OUTER_PAD - PAGE_PAD * 2
        return min((available_w) / page_w, (available_h) / page_h)

    def get_page_pos(self, spread_pos: int) -> tuple[int, int]:
        """Return the top-left (x, y) position for the page at ``spread_pos``
        (0 = left/only page of the spread, 1 = right page)."""
        page_w = SLOT_W * self._binder.cols + PAGE_PAD * 2
        page_h = SLOT_H * self._binder.rows + PAGE_PAD * 2
        scale = self.get_page_scale()
        cw, ch = self._canvas.width(), self._canvas.height()
        scaled_page_w = round(page_w * scale)
        if spread_pos == 0:
            if self._page_view == "single":
                page_x = (cw - scaled_page_w) // 2
            else:
                page_x = OUTER_PAD # + (cw - 2 * OUTER_PAD - scaled_page_w) // 2
        else:
            page_x = (
                cw - 2 * OUTER_PAD - scaled_page_w  
            )
        page_y = OUTER_PAD
        return page_x, page_y

    # ── painting ─────────────────────────────────────────────────────────
    def paint(self, painter: QPainter) -> None:
        """Paint all currently-viewable page frames onto ``painter``.

        Called from ``RoundedCornerCanvas.paintEvent`` after the canvas
        background has been drawn. Slot widgets are separate child widgets
        and paint themselves; this only draws the page frames behind them.
        """
        self._canvas.slot_rects = []
        for spread_pos, page_idx in enumerate(self.get_viewable_pages()):
            scale = self.get_page_scale()
            page_x, page_y = self.get_page_pos(spread_pos)
            self._paint_page(painter, page_idx, page_x, page_y, scale)

    def _paint_page(self, painter: QPainter, page_idx: int, x: int, y: int, scale: float) -> None:
        """Draw a single page frame (rounded rect, border, stack shadows)."""
        cols, rows = self._binder.cols, self._binder.rows
        slot_w = round(SLOT_W * scale)
        slot_h = round(SLOT_H * scale)
        page_pad = round(PAGE_PAD * scale)
        page_w = cols * slot_w + 2 * page_pad
        page_h = rows * slot_h + 2 * page_pad
        shadow_offset = round(4 * scale)
        theme = self._binder._theme
        border_color = QColor(
            self._binder._border_color
            if self._binder._border_color is not None
            else get_colors(theme=theme)["border"]
        )
        page_bg_color = QColor(
            self._binder._page_color
            if self._binder._page_color is not None
            else get_colors(theme=theme)["page_bg"]
        )

        # Shadows of pages "ahead" (upcoming pages stacked visually behind this one)
        n_pages = len(self._binder.pages)
        if self._page_view == "double" and page_idx % 2 != 0:
            for page_ahead in range(((n_pages - page_idx) // 2), 0, -1):
                painter.setPen(QPen(border_color, 2))
                painter.setBrush(page_bg_color)
                painter.drawRoundedRect(
                    QRect(
                        x + shadow_offset * page_ahead,
                        y + shadow_offset * page_ahead,
                        page_w,
                        page_h,
                    ),
                    SLOT_CORNER_RADIUS,
                    SLOT_CORNER_RADIUS,
                )

        # Shadows of pages "behind" (earlier pages stacked visually behind this one)
        elif self._page_view == "double" and page_idx % 2 == 0:
            for page_behind in range((page_idx + 2) // 2 - 1, -1, -1):
                painter.setPen(QPen(border_color, 2))
                painter.setBrush(page_bg_color)
                painter.drawRoundedRect(
                    QRect(
                        x - shadow_offset * page_behind,
                        y + shadow_offset * page_behind,
                        page_w,
                        page_h,
                    ),
                    SLOT_CORNER_RADIUS,
                    SLOT_CORNER_RADIUS,
                )

        # The page itself (selected pages get the selection-colored border)
        is_selected = self._binder._sel_page == page_idx
        sel_color = QColor(
            self._binder._sel_color
            if self._binder._sel_color is not None
            else get_colors(theme=theme)["border_sel"]
        )
        painter.setPen(QPen(sel_color, 2) if is_selected else QPen(border_color, 2))
        painter.setBrush(page_bg_color)
        painter.drawRoundedRect(
            QRect(x, y, page_w, page_h), SLOT_CORNER_RADIUS, SLOT_CORNER_RADIUS
        )

    # ── slot widget layout ───────────────────────────────────────────────
    def place_slots(self) -> None:
        """Move/resize/show slot widgets according to current page layout."""
        self._canvas.slot_rects = []
        scale = self.get_page_scale()
        page_pad = round(scale * PAGE_PAD)
        cols = self._binder.cols
        slot_w = round(scale * SLOT_W)
        slot_h = round(scale * SLOT_H)
        viewable_pages = self.get_viewable_pages()

        for (page_idx, slot_idx), widget in self._slot_widgets.items():
            if page_idx not in viewable_pages:
                widget.hide()
                continue
            else:
                widget.show()
                widget.raise_()
            spread_pos = viewable_pages.index(page_idx)
            x, y = self.get_page_pos(spread_pos)
            row = slot_idx // cols
            col = slot_idx % cols
            slot_x = x + page_pad + col * slot_w
            slot_y = y + page_pad + row * slot_h

            widget.move(int(slot_x), int(slot_y))
            widget.setFixedSize(slot_w, slot_h)
            widget.show()
            slot_rect = QRect(int(slot_x), int(slot_y), slot_w, slot_h)
            self._canvas.slot_rects.append((page_idx, slot_idx, slot_rect))
            is_selected = (
                self._binder._sel_slot == slot_idx
                and self._binder._sel_page == page_idx
            )
            widget.set_selected(is_selected)
            # widget.update()

    def update_viewable_slots(self) -> None:
        """Mark each slot widget's ``_draw`` (interactive) flag based on
        whether its page is currently viewable."""
        viewable_pages = self.get_viewable_pages()
        for (page_idx, _slot_idx), widget in self._slot_widgets.items():
            widget._draw = page_idx in viewable_pages

    def update_slot_widgets(self, slots=None, url: str | None = None) -> None:
        """Create/refresh/prune the ``CardSlotWidget`` instances backing the
        binder's slots. ``slots`` limits which (page_idx, slot_idx) pairs get
        their visual state refreshed; ``url`` limits it to slots holding that
        card URL. With both omitted, all existing widgets are refreshed."""
        app = self._app
        url_slots = (
            [
                (pgx, slx)
                for pgx, pg in enumerate(self._binder.pages)
                for slx, sl in enumerate(pg.slots)
                if sl.url == url
            ]
            if url
            else []
        )
        render_page_idxs = self.get_viewable_pages()
        slots_set = set(slots) if slots else set(self._slot_widgets.keys())

        for page_idx, page in enumerate(self._binder.pages):
            for slot_idx, slot in enumerate(page.slots):
                slot_key = (page_idx, slot_idx)

                if slot_key not in self._slot_widgets:
                    widget = CardSlotWidget(
                        self._canvas,
                        page_idx,
                        slot_idx,
                        (
                            self._binder.pages[page_idx].slots[slot_idx]._sleeved
                            or self._binder._sleeved
                        ),
                        (
                            self._binder.pages[page_idx].slots[slot_idx]._sleeve_color
                            if not self._binder._sleeved
                            else self._binder._sleeve_color
                        ),
                        self._binder._slot_color,
                        self._binder._sel_color,
                        self._binder._border_color,
                        draw=page_idx in render_page_idxs,
                    )
                    widget.clicked.connect(app._on_slot_widget_clicked)
                    widget.right_clicked.connect(app._on_slot_widget_right_clicked)
                    widget.doubleclicked.connect(app._on_slot_widget_double_clicked)
                    widget.dropped.connect(app._on_slot_widget_dropped)
                    self._slot_widgets[slot_key] = widget
                    widget.card_url = self._binder.pages[page_idx].slots[slot_idx].url
                    self._refresh_widget_image(widget, page_idx, render_page_idxs)
                    widget._redraw = True
                    widget._dragging=False

                if (
                    (slots is not None and slot_key in slots)
                    or (url_slots and slot_key in url_slots)
                    or (slots is None and url is None)
                ):
                    widget = self._slot_widgets[slot_key]
                    widget._sleeved = (
                        self._binder.pages[page_idx].slots[slot_idx]._sleeved
                        or self._binder._sleeved
                    )
                    widget._sleeve_color = (
                        self._binder.pages[page_idx].slots[slot_idx]._sleeve_color
                        if not self._binder._sleeved
                        else self._binder._sleeve_color
                    )
                    widget._slot_color = self._binder._slot_color
                    widget._sel_color = self._binder._sel_color
                    widget._border_color = self._binder._border_color
                    widget._draw = page_idx in render_page_idxs
                    widget.card_url = self._binder.pages[page_idx].slots[slot_idx].url
                    if widget.card_url:
                        self._refresh_widget_image(widget, page_idx, render_page_idxs)
                    else:
                        widget.card_url = None
                        widget._pixmap = None
                    widget._redraw = True
                else:
                    self._slot_widgets[slot_key]._dragging = False
                self._slot_widgets[slot_key].update()
                slots_set.discard(slot_key)

        # Remove any slot widgets that are no longer needed (e.g. grid shrunk)
        for slot_key in list(slots_set):
            if slot_key in self._slot_widgets:
                self._slot_widgets[slot_key].deleteLater()
                del self._slot_widgets[slot_key]

    def _refresh_widget_image(self, widget: "CardSlotWidget", page_idx: int, render_page_idxs: list[int]) -> None:
        """Load (or kick off a fetch for) the image backing ``widget``."""
        app = self._app
        if not widget.card_url:
            return
        img = app._image_cache.get_cached_pixmap(widget.card_url)
        if img:
            widget.set_card(widget.card_url, img)
        else:
            widget.set_card(widget.card_url, None)
            app._fetch_image_async(widget.card_url, priority=page_idx in render_page_idxs)
        if widget.card_url not in app._pool_info:
            app._bg_fetch_url(widget.card_url, picker=False)
            app._add_to_history_pool(widget.card_url)

    def clear_slot_widgets(self) -> None:
        """Destroy all cached slot widgets (e.g. on binder replace/load)."""
        for widget in self._slot_widgets.values():
            widget.setParent(None)
            widget.deleteLater()
        self._slot_widgets.clear()

    # ── top-level refresh entrypoint ─────────────────────────────────────
    def redraw(self, to_redraw: dict | None = None) -> None:
        """Apply a redraw request produced by ``BinderApp._refresh_binder``.

        ``to_redraw`` may contain any of:
          - ``"colored"``: only colors changed, just repaint.
          - ``"pages"``: list of page indices to refresh (empty = all viewable).
          - ``"slots"``: list of (page_idx, slot_idx) to refresh (empty = all viewable).
        """
        if not to_redraw:
            return
        if "colored" in to_redraw:
            self.update_slot_widgets()
        if "pages" in to_redraw:
            self._app._update_page_panel()
        if "slots" in to_redraw:
            slots = to_redraw["slots"]
            if slots == []:
                print("No slots specified for redraw; defaulting to all viewable slots.")
                slots = [
                    (p, s)
                    for p in self.get_viewable_pages()
                    for s in range(len(self._binder.pages[p].slots))
                ]
            self.place_slots()
            self.update_slot_widgets(slots=slots)
            self.update_viewable_slots()
            
            
        self._canvas.update()
