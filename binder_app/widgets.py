"""Custom widgets: the binder canvas (``RoundedCornerCanvas``) and the individual
card slot widget (``CardSlotWidget``)."""


from typing import TYPE_CHECKING

import darkdetect
from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget
from painter_colors import get_colors

from . import constants
from .constants import CARD_H, CARD_W, SLEEVE_H, SLEEVE_W, SLOT_CORNER_RADIUS, SLOT_H, SLOT_W

if TYPE_CHECKING:
    from .renderer import BinderRenderer


class RoundedCornerCanvas(QWidget):
    """Custom canvas for rendering binder spreads with rounded corners."""

    slot_clicked = Signal(int, int)
    slot_right_clicked = Signal(int, int)
    slot_dragged = Signal(int, int, int, int)
    slot_dragging = Signal(QPoint, int, int)
    empty_space_clicked = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        # self.setStyleSheet(f"background-color: {COLOR_CANVAS_BG};")
        self.slot_rects: list[tuple[int, int, int]] = []  # (page_idx, slot_idx, QRect)
        self.setAcceptDrops(True)
        self._drag_start: QPoint | None = None
        self._dragging = False
        self._parent_app = parent
        self._slot_rects = []
        # The renderer is assigned by BinderApp right after construction; it
        # owns page layout state and slot widget bookkeeping.
        self._renderer: BinderRenderer | None = None

    @property
    def _slot_widgets(self) -> dict[tuple[int, int], "CardSlotWidget"]:
        """Kept for backwards-compatible read access; slot widgets are
        actually owned by the BinderRenderer."""
        return self._renderer.slot_widgets if self._renderer else {}

    def paintEvent(self, event):
        """Render canvas background and delegate page-frame painting."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QColor(
                get_colors(
                    "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
                )["border"]
            )
        )
        painter.setBrush(
            QColor(
                get_colors(
                    "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
                )["canvas_bg"]
            )
        )
        # print(f"darkdetect.isDark() = {darkdetect.isDark()}, MATCH_OS_THEME = {constants.MATCH_OS_THEME}, theme = {'dark' if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else 'light'}")
        painter.drawRoundedRect(self.rect(), SLOT_CORNER_RADIUS, SLOT_CORNER_RADIUS)
        if self._renderer is not None:
            self._renderer.paint(painter)
        painter.end()


    def draw_text(
        self,
        painter: QPainter,
        x: int,
        y: int,
        text: str,
        font_size: int = 10,
        color: str = "#000000",
        anchor: str = "center",
    ) -> None:
        """Helper to draw text."""
        font = QFont("Arial", font_size)
        painter.setFont(font)
        painter.setPen(QColor(color))
        rect = QRect(x - 100, y - 20, 200, 40)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event):
        """Handle mouse press."""
        if event.button() == Qt.MouseButton.LeftButton:
            # print(f"Mouse press at {event.localPos().x()}, {event.localPos().y()}")
            self._drag_start = event.localPos()
            self._dragging = False
            x, y = event.localPos().x(), event.localPos().y()
            hit = self._hit_test(x, y)
            self._drag_src_page, self._drag_src_slot = hit if hit else (None, None)
            if hit:
                page_idx, slot_idx = hit
                # print(f"Mouse press hits slot {hit}")
                self.slot_clicked.emit(page_idx, slot_idx)
            else:
                self.empty_space_clicked.emit()
        if event.button() == Qt.MouseButton.RightButton:
            x, y = event.localPos().x(), event.localPos().y()
            hit = self._hit_test(x, y)
            if hit:
                page_idx, slot_idx = hit
                self.slot_right_clicked.emit(page_idx, slot_idx)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """Handle mouse move."""
        if self._drag_start:
            delta = (event.localPos() - self._drag_start).manhattanLength()
            if delta > 5:
                self._dragging = True
                # print('dragging')
                self.slot_dragging.emit(
                    event.pos(), self._drag_src_page, self._drag_src_slot
                )
                # print(f"Dragging from {self._drag_start} to {event.localPos()}")
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start:
            self._drag_start = None
            self._dragging = False
            hit = self._hit_test(event.localPos().x(), event.localPos().y())
            if hit:
                dst_page, dst_slot = hit
                self.slot_dragged.emit(
                    self._drag_src_page,
                    self._drag_src_slot,
                    dst_page,
                    dst_slot,
                )
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        """Handle right-click."""
        x, y = event.pos().x(), event.pos().y()
        hit = self._hit_test(x, y)
        if hit:
            page_idx, slot_idx = hit
            self.slot_right_clicked.emit(page_idx, slot_idx)
        super().contextMenuEvent(event)

    # def dragEnterEvent(self, event):
    #     """Accept drag enter if it has a URL."""
    #     if event.mimeData().hasText():
    #         event.acceptProposedAction()
    #     else:
    #         event.ignore()

    # def dropEvent(self, event):
    #     """Handle drop event."""
    #     if event.mimeData().hasText():
    #         event.acceptProposedAction()
    #     else:
    #         event.ignore()

    def _hit_test(self, x: int, y: int) -> tuple[int, int] | None:
        """Test if click hits a slot."""
        for page_idx, slot_idx, rect in self.slot_rects:
            x1, y1, x2, y2 = rect.left(), rect.top(), rect.right(), rect.bottom()
            if x1 <= x <= x2 and y1 <= y <= y2:
                return (page_idx, slot_idx)
        return None

    def clear_slot_widgets(self) -> None:
        """Clear cached slot widgets (delegates to the renderer)."""
        if self._renderer is not None:
            self._renderer.clear_slot_widgets()

    def set_page_view(self, view: str = "double") -> None:
        """View either one page or two at the same time"""
        if self._renderer is not None:
            self._renderer.page_view = view
        if self._parent_app is not None:
            self._parent_app.update_request.emit()

class CardSlotWidget(QWidget):
    """Widget for displaying a single card slot with image and label."""

    # Signals
    dropped = Signal(int, int, int, int)  # src_page, src_slot, dst_page, dst_slot
    clicked = Signal(int, int)  # page_idx, slot_idx
    right_clicked = Signal(int, int)  # page_idx, slot_idx
    doubleclicked = Signal(int, int)  # page_idx, slot_idx

    def __init__(
        self,
        parent=None,
        page_idx: int = 0,
        slot_idx: int = 0,
        sleeved=False,
        sleeve_color: str = None,
        slot_color: str = None,
        sel_color: str = None,
        border_color: str = None,
        draw: bool = False
    ):
        self._fill_size = False
        super().__init__(parent)
        self._draw = draw
        self._redraw = True
        self._dragging = False
        self.setFixedSize(SLOT_W, SLOT_H)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._pixmap: QPixmap | None = None
        self._is_selected = False
        self.page_idx = page_idx
        self.slot_idx = slot_idx
        self.card_url = ""
        self.empty = True
        self._drag_start: QPoint | None = None
        self._sleeved = sleeved
        if sleeve_color is None:
            self._sleeve_color = QColor(get_colors(
                "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
            )["default_sleeve"]).name()
        else:
            self._sleeve_color = sleeve_color
        if slot_color is None:
            self._slot_color = QColor(get_colors(
                "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
            )["slot_empty"]).name()
        else:
            self._slot_color = slot_color
        if sel_color is None:
            self._sel_color = QColor(get_colors(
                "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
            )["border_sel"]).name()
        else:
            self._sel_color = sel_color
        if border_color is None:
            self._border_color = QColor(get_colors(
                "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
            )["border"]).name()
        else:
            self._border_color = border_color

    def set_card(self, url: str = "", img: QPixmap | None = None) -> None:
        """Set the card image and URL."""
        self._pixmap = img
        self.card_url = url
        if url and url.startswith("custom://"):
            self.setToolTip(f"Custom card: {url.replace('custom://', '')}")
            self._fill_size = True
        else:
            self.setToolTip(f"Card URL: {url}")
            self._fill_size = False
        self.empty = img is None and not url
        self.update()

    def set_selected(self, selected: bool) -> None:
        """Set selection state."""
        self._is_selected = selected
        self.update()

    def paintEvent(self, event):
        """Paint the slot."""

        
        # print("painting")
        self.raise_()
        # if self._dragg
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        w, h = self.width(), self.height()
        self._theme = "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
        bg_color = self._slot_color if self._slot_color is not None else get_colors(self._theme)["slot_empty"]
        painter.setBrush(QColor(bg_color))
        # painter.setBrush(Qt.BrushStyle.NoBrush)
        sel_pen = QPen(QColor(self._sel_color if self._sel_color is not None else get_colors(self._theme)["border_sel"]), 3)
        sel_pen.setDashPattern([1, 2, 1, 2])
        reg_pen = QPen(QColor(self._border_color if self._border_color is not None else get_colors(self._theme)["border"]), 1)
        pen = sel_pen if self._is_selected else reg_pen
        painter.setPen(pen if not self._is_selected else sel_pen)
        painter.drawRoundedRect(
            QRectF(0, 0, w, h), SLOT_CORNER_RADIUS, SLOT_CORNER_RADIUS
        )
        # painter.fillRect(0, 0, w, h, QColor(bg_color))

        # border_color = COLOR_BORDER_SEL if self._is_selected else COLOR_BORDER
        # pen = QPen(QColor(border_color))
        # pen.setWidth(2 if self._is_selected else 1)
        # painter.setPen(pen)
        # self.parent().draw_rounded_rect(painter, 0, 0, w, h, SLOT_CORNER_RADIUS, fill_color=None, border_color=border_color, border_width=2 if self._is_selected else 1)
        scale = w / SLOT_W if SLOT_W > 0 else 1.0
        if self._sleeved and self._pixmap and not self.card_url.startswith("custom://"):
            x = (w - SLEEVE_W * scale) / 2
            y = (h - SLEEVE_H * scale) / 2
            sleeve_color = self._sleeve_color
            painter.setBrush(QColor(sleeve_color))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(
                QRectF(x, y, SLEEVE_W * scale, SLEEVE_H * scale), 2, 2
            )

        if self._pixmap:
            # Scale pixmap to fit slot while maintaining aspect ratio
            if self._fill_size:
                scaled_pixmap = self._pixmap.scaled(
                    w - 4,
                    h - 4,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            else:
                scaled_pixmap = self._pixmap.scaled(
                    CARD_W * scale,
                    CARD_H * scale,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

            x = (w - scaled_pixmap.width()) // 2
            y = (h - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)
        painter.end()
        

    def mousePressEvent(self, event):
        """Handle mouse press to start drag."""
        print(
            f"CardSlotWidget mousePressEvent: button={event.button()}, pos={event.pos()}"
        )
        if not self._draw:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
            if not self._is_selected:
                self.clicked.emit(self.page_idx, self.slot_idx)
        elif event.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(self.page_idx, self.slot_idx)

    def mouseDoubleClickEvent(self, event):
        """Handle double click."""
        if not self._draw:
            return
        print(
            f"CardSlotWidget mouseDoubleClickEvent: button={event.button()}, pos={event.pos()}"
        )
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleclicked.emit(self.page_idx, self.slot_idx)

    def mouseMoveEvent(self, event):
        """Handle mouse move to start dragging."""
        if not self._draw:
            return
        # print(f"CardSlotWidget mouseMoveEvent: buttons={event.buttons()}, pos={event.pos()}")
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if not self._drag_start:
            return
        if not self.card_url:
            return  # Don't drag empty slots

        # Only start drag if moved far enough
        # print(f"Mouse move: start {self._drag_start}, current {event.pos()}, delta {(event.pos() - self._drag_start).manhattanLength()}")
        # if (event.pos() - self._drag_start).manhattanLength() > 1:
        if self._dragging:
            return
        self._dragging = True
        # Create drag operation
        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setText(self.card_url)
        mime_data.setData(
            "application/x-card-slot", f"{self.page_idx},{self.slot_idx}".encode()
        )
        drag.setMimeData(mime_data)
        drag.setHotSpot(event.position().toPoint() - self.rect().topLeft())
        
        if self._pixmap:
            drag_pixmap = QPixmap(self.size())
            self.render(drag_pixmap)
            drag.setPixmap(drag_pixmap)
        else:
            pixmap = QPixmap(SLOT_W, SLOT_H)
            pixmap.fill(
                QColor(
                    get_colors(
                        "dark"
                        if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME)
                        else "light"
                    )["slot_empty"]
                )
            )
            drag.setPixmap(pixmap)
        
        result = drag.exec(Qt.DropAction.MoveAction)
        self._dragging = False

    def dragEnterEvent(self, event):
        """Accept drag enter."""
        if not self._draw:
            return
        if event.mimeData().hasFormat("application/x-card-slot"):
            event.acceptProposedAction()
        # super().dragEnterEvent(event)
    
    def dropEvent(self, event):
        """Handle drop event."""
        if not self._draw:
            return
        mime_data = event.mimeData()
        if mime_data.hasFormat("application/x-card-slot"):
            src_data = mime_data.data("application/x-card-slot")
            src_page, src_slot = map(int, src_data.data().decode().split(","))
            self.dropped.emit(src_page, src_slot, self.page_idx, self.slot_idx)
            event.acceptProposedAction()
            
        self._dragging = False
        # super().dropEvent(event)
    @property
    def scale(self) -> float:
        """Get current scale factor for drawing."""
        return SLOT_W / self.width() if self.width() > 0 else 1.0
