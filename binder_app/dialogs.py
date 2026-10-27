"""Standalone dialog windows: slot color picker, custom card creation, card
preview (with a sparkle effect)."""


import math
import random
import uuid
from PIL import Image as PILImage
from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFileDialog, QGraphicsEffect, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget

from .constants import CUSTOM_DIR, PAGE_PAD_MM, SLOT_PAD_H_MM, SLOT_PAD_V_MM, WORKSPACE, _CARD_H_MM, _CARD_W_MM
from .image_cache import ImageCacheManager
from .widgets import RoundedCornerCanvas


class SlotColorsDialog(QMainWindow):
    def __init__(self, parent=None, title="Slot Colors"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setGeometry(150, 150, 400, 300)
        self.setMinimumSize(300, 200)
        self._parent_app = parent
        self._build_ui()

    def _build_ui(self):
        """Build UI for slot colors dialog."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        self._sleeve_color_button = QPushButton("Set sleeve color")
        self._sleeve_color_button.clicked.connect(self._choose_sleeve_color)
        icon = self._create_color_icon(
                self._binder.pages[self._binder._sel_page]
                .slots[self._binder._sel_slot]
                ._sleeve_color
            )
        self._sleeve_color_button.setIcon(icon)
        layout.addWidget(self._sleeve_color_button)

        self._border_color_button = QPushButton("Set border color")
        self._border_color_button.clicked.connect(self._choose_border_color)
        icon = self._create_color_icon(
                self._binder.pages[self._binder._sel_page]
                .slots[self._binder._sel_slot]
                ._border_color
            )
        self._border_color_button.setIcon(icon)
        layout.addWidget(self._border_color_button)

        self._sel_color_button = QPushButton("Set selection color")
        self._sel_color_button.clicked.connect(self._choose_sel_color)
        icon = self._create_color_icon(
                self._binder.pages[self._binder._sel_page]
                .slots[self._binder._sel_slot]
                ._sel_color
            )
        self._sel_color_button.setIcon(icon)
        layout.addWidget(self._sel_color_button)

        central_widget.setLayout(layout)

class CustomCardDialog(QMainWindow):
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.setWindowTitle("Add Custom Card")
        self.setGeometry(100, 100, 720, 600)
        self.setMinimumSize(500, 450)
        self._callback = callback
        self._src_img = None
        self._crop_x, self._crop_y, self._crop_w, self._crop_h = 0, 0, 0, 0
        self._scale = 1.0
        self._cols, self._rows = 1, 1
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        top_layout = QHBoxLayout()
        browse_btn = QPushButton("Browse Image...")
        browse_btn.clicked.connect(self._browse)
        top_layout.addWidget(browse_btn)
        top_layout.addSpacing(12)
        top_layout.addWidget(QLabel("Grid:"))
        cols_spin = QSpinBox()
        cols_spin.setRange(1, 8)
        cols_spin.setValue(1)
        cols_spin.setMaximumWidth(50)
        cols_spin.valueChanged.connect(lambda v: self._set_grid(v, self._rows))
        top_layout.addWidget(cols_spin)
        top_layout.addWidget(QLabel("x"))
        rows_spin = QSpinBox()
        rows_spin.setRange(1, 8)
        rows_spin.setValue(1)
        rows_spin.setMaximumWidth(50)
        rows_spin.valueChanged.connect(lambda v: self._set_grid(self._cols, v))
        top_layout.addWidget(rows_spin)
        top_layout.addStretch()
        layout.addLayout(top_layout)
        self._canvas = RoundedCornerCanvas()
        # self._canvas.setStyleSheet("background-color: #1a1a1a;")
        self._canvas.setMinimumHeight(300)
        self._canvas.paintEvent = lambda e: self._draw_crop_preview()
        layout.addWidget(self._canvas, 1)
        bottom_layout = QHBoxLayout()
        bottom_layout.addWidget(
            QLabel("Drag crop box to reposition. Grid shows tile divisions.")
        )
        bottom_layout.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.close)
        bottom_layout.addWidget(cancel_btn)
        self._confirm_btn = QPushButton("Add to Binder (1 tile)")
        self._confirm_btn.clicked.connect(self._confirm)
        bottom_layout.addWidget(self._confirm_btn)
        layout.addLayout(bottom_layout)
        self._altered_img = None
        self._angle = 0
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def mousePressEvent(self, event):
        if not self._src_img:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            global_pos = self.mapToGlobal(event.pos())
            # print(f"Mouse pressed at {global_pos}")
            # Check if the click is on a corner handle for resizing\
            # print(f"Mouse pressed at {self._canvas.mapToGlobal(event.pos())}")
            # print(f"Corner handles: {self._corner_handles}")
            for i, (hx, hy) in enumerate(self._corner_handles):
                if (
                    abs(global_pos.x() - hx) <= 6
                    and abs(global_pos.y() - hy) <= 6
                ):
                    self._resize_start = event.pos()
                    self._resize_corner = i
                    self._dragging = False
                    # print(f"Started resizing from corner {i} at {event.pos()}")
                    return
            # Otherwise, start dragging the crop box
            self._dragging = True
            self._drag_start = event.pos()
            self._crop_start_x, self._crop_start_y = self._crop_x, self._crop_y

    def mouseMoveEvent(self, event):
        if not self._src_img:
            return
        if hasattr(self, "_dragging") and self._dragging:
            dx = round((event.x() - self._drag_start.x()) / self._scale)
            dy = round((event.y() - self._drag_start.y()) / self._scale)
            new_x = max(0, min(self._crop_start_x + dx, self._altered_img.width - self._crop_w))
            new_y = max(0, min(self._crop_start_y + dy, self._altered_img.height - self._crop_h))
            if new_x != self._crop_x or new_y != self._crop_y:
                self._crop_x, self._crop_y = new_x, new_y
                self._canvas.update()
        elif hasattr(self, "_resize_corner"):
            # Handle resizing logic 
            dx = round((event.x() - self._resize_start.x()) / self._scale)
            dy = round((event.y() - self._resize_start.y()) / self._scale)
            # Maintain aspect ratio based on grid
            target_ratio = (_CARD_W_MM * self._cols + SLOT_PAD_H_MM * (self._cols - 1)) / (_CARD_H_MM * self._rows + SLOT_PAD_V_MM * (self._rows - 1))

            if self._resize_corner == 0:  # Top-left
                new_w = self._crop_w - dx
                new_h = round(new_w / target_ratio)
                new_x = self._crop_x + dx
                new_y = self._crop_y + (self._crop_h - new_h)
            elif self._resize_corner == 1:  # Top-right
                new_w = self._crop_w + dx
                new_h = round(new_w / target_ratio)
                new_x = self._crop_x
                new_y = self._crop_y + (self._crop_h - new_h)
            elif self._resize_corner == 2:  # Bottom-left
                new_w = self._crop_w - dx
                new_h = round(new_w / target_ratio)
                new_x = self._crop_x + dx
                new_y = self._crop_y
            elif self._resize_corner == 3:  # Bottom-right
                new_w = self._crop_w + dx
                new_h = round(new_w / target_ratio)
                new_x = self._crop_x
                new_y = self._crop_y
            # Ensure new dimensions are within bounds
            if new_w > 0 and new_h > 0 and new_x >= 0 and new_y >= 0 and new_x + new_w <= self._altered_img.width and new_y + new_h <= self._altered_img.height:
                self._crop_x, self._crop_y, self._crop_w, self._crop_h = new_x, new_y, new_w, new_h
                
            self._canvas.update()
            self._resize_start = event.pos()  # Update start position for continuous resizing
    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y() / 120  # 1 for each notch
            self.rotate(delta)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Left:
            # print(f"Key pressed: {event.key()}, modifiers: {event.modifiers()}")
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.rotate(1)  # Rotate clockwise by 5 degrees
            else:
                self.rotate(9)  # Rotate clockwise
        elif event.key() == Qt.Key.Key_Right:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.rotate(-1)  # Rotate counter-clockwise by 5 degrees
            else:
                self.rotate(-9)  # Rotate counter-clockwise
        else:
            super().keyPressEvent(event)

    def rotate(self, delta):
        if not self._src_img:
            return
        self._angle = (self._angle + delta * 5) % 360
        self._altered_img = self._src_img.rotate(self._angle)
        # self._init_crop()
        self._canvas.update()


    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Image",
            str(WORKSPACE / "custom_cards"),
            "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)",
        )
        if path:
            try:
                self._src_img = PILImage.open(path).convert("RGBA")
                self._altered_img = self._src_img.copy()
                self._init_crop()
                self._canvas.update()
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not open image:\n{e}")

    def _set_grid(self, cols, rows):
        self._cols, self._rows = cols, rows
        self._init_crop()
        self._confirm_btn.setText(
            f"Add to Binder ({cols * rows} tile{'s' if cols * rows != 1 else ''})"
        )
        self._canvas.update()

    def _init_crop(self):
        if not self._altered_img:
            return
        sw, sh = self._altered_img.size
        ratio = (_CARD_W_MM * self._cols + SLOT_PAD_H_MM * (self._cols - 1)) / (
            _CARD_H_MM * self._rows + SLOT_PAD_V_MM * (self._rows - 1)
        )
        if sw / sh > ratio:
            ch, cw = sh, round(sh * ratio)
        else:
            cw, ch = sw, round(sw / ratio)
        self._crop_w, self._crop_h = cw, ch
        self._crop_x, self._crop_y = (sw - cw) // 2, (sh - ch) // 2
    
    def _draw_crop_preview(self):
        c = self._canvas
        painter = QPainter(c)
        painter.fillRect(c.rect(), QColor("#1a1a1a"))
        if not self._altered_img:
            painter.setPen(QColor("#888888"))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(c.rect(), Qt.AlignmentFlag.AlignCenter, "Browse Image...")
            painter.end()
            return
        cw, ch = c.width(), c.height()
        sw, sh = self._altered_img.size
        pad = 16
        scale = min((cw - 2 * pad) / sw, (ch - 2 * pad) / sh)
        self._scale = scale
        disp_w, disp_h = max(1, round(sw * scale)), max(1, round(sh * scale))
        img_ox, img_oy = (cw - disp_w) // 2, (ch - disp_h) // 2
        flat = self._altered_img.copy()
        if flat.mode == "RGBA":
            bg = PILImage.new("RGB", flat.size, (255, 255, 255))
            bg.paste(flat, mask=flat.split()[3])
            flat = bg
        scaled = flat.resize((disp_w, disp_h), PILImage.Resampling.NEAREST)
        cache_mgr = ImageCacheManager()
        pixmap = cache_mgr.pil_to_qpixmap(scaled)
        painter.drawPixmap(img_ox, img_oy, pixmap)
        cx1 = img_ox + round(self._crop_x * scale) # top-left corner of crop box in display coordinates
        cy1 = img_oy + round(self._crop_y * scale) # top-left corner of crop box in display coordinates
        cx2 = cx1 + max(1, round(self._crop_w * scale)) # bottom-right corner of crop box in display coordinates
        cy2 = cy1 + max(1, round(self._crop_h * scale)) # bottom-right corner of crop box in display coordinates
        painter.fillRect(img_ox, img_oy, disp_w, cy1 - img_oy, QColor(0, 0, 0, 128))
        painter.fillRect(
            img_ox, cy2, disp_w, img_oy + disp_h - cy2, QColor(0, 0, 0, 128)
        )
        painter.fillRect(img_ox, cy1, cx1 - img_ox, cy2 - cy1, QColor(0, 0, 0, 128))
        painter.fillRect(
            cx2, cy1, img_ox + disp_w - cx2, cy2 - cy1, QColor(0, 0, 0, 128)
        )
        pen = QPen(QColor("#00ccff"))
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRect(cx1, cy1, cx2 - cx1, cy2 - cy1)
        pen.setWidth(1)
        pen.setDashPattern([4, 3])
        painter.setPen(pen)
        cbox_w, cbox_h = cx2 - cx1, cy2 - cy1
        for col in range(1, self._cols):
            lx = cx1 + round(cbox_w * col / self._cols)
            painter.drawLine(lx, cy1, lx, cy2)
        for row in range(1, self._rows):
            ly = cy1 + round(cbox_h * row / self._rows)
            painter.drawLine(cx1, ly, cx2, ly)
        hs = 6
        for hx, hy in [(cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)]:
            painter.fillRect(hx - hs, hy - hs, 2 * hs, 2 * hs, QColor("#ff5500"))
        painter.end()
        corner_handles = [(cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)]
        # convert to global coordinates
        self._corner_handles = [
            (c.mapToGlobal(QPointF(hx, hy)).x(), c.mapToGlobal(QPointF(hx, hy)).y())
            for hx, hy in corner_handles
        ]

    def _confirm(self):
        if not self._altered_img:
            QMessageBox.warning(self, "No Image", "Please select an image first.")
            return
        cropped = self._altered_img.crop(
            (
                self._crop_x,
                self._crop_y,
                self._crop_x + self._crop_w,
                self._crop_y + self._crop_h,
            )
        )

        CUSTOM_DIR.mkdir(exist_ok=True)
        total_w_mm = _CARD_W_MM * self._cols + SLOT_PAD_H_MM * (self._cols - 1)
        total_h_mm = _CARD_H_MM * self._rows + SLOT_PAD_V_MM * (self._rows - 1)
        card_px_w = round(self._crop_w * _CARD_W_MM / total_w_mm)
        card_px_h = round(self._crop_h * _CARD_H_MM / total_h_mm)
        pad_px_w = round(self._crop_w * SLOT_PAD_H_MM / total_w_mm)
        pad_px_h = round(self._crop_h * SLOT_PAD_V_MM / total_h_mm)
        page_pad_px_w = round(self._crop_w * PAGE_PAD_MM / total_w_mm)
        page_pad_px_h = round(self._crop_h * PAGE_PAD_MM / total_h_mm)
        # total_px_h =
        group_img = cropped

        main_filename = f"{uuid.uuid4().hex}.png"
        # print("Saving custom group image to", CUSTOM_DIR / main_filename)
        group_img.save(str(CUSTOM_DIR / main_filename))
        group = {
            "url": f"custom://{main_filename}",
            "children": [],
            "cols": self._cols,
            "rows": self._rows,
        }
        for row in range(self._rows):
            for col in range(self._cols):
                tx = col * (card_px_w + pad_px_w/2)
                ty = row * (card_px_h + pad_px_h/2)
                tile_img = cropped.crop((tx, ty, tx + card_px_w + pad_px_w/2, ty + card_px_h+pad_px_h/2))
                filename = f"{uuid.uuid4().hex}.png"
                tile_img.save(str(CUSTOM_DIR / filename))
                group["children"].append(f"custom://{filename}")
                # print(f"Saved tile {row},{col} to {filename}")
        # self._add_custom_to_pool(main_filename, f"custom://{main_filename}", group[main_filename])
        self._callback(group)
        self.close()

class SparkleEffect(QGraphicsEffect):
    def __init__(
        self, parent=None, sparkle_density=0.5, polygon_sides=5, sparkle_speed=1
    ):
        super().__init__(parent)
        self.sparkle_density = sparkle_density
        self.polygon_sides = polygon_sides
        self.timer = QTimer()
        self.timer.timeout.connect(self.update)
        self.sparkle_speed = sparkle_speed
        self.timer.start(
            int(1000 * self.sparkle_speed)
        )  # Update based on sparkle_speed

    def draw(self, painter):
        # Draw the source pixmap
        pixmap = self.sourcePixmap(Qt.LogicalCoordinates)
        painter.drawPixmap(0, 0, pixmap)

        # Draw sparkles
        for _ in range(
            int(pixmap.width() * pixmap.height() * self.sparkle_density / 1000)
        ):
            x = random.randint(0, pixmap.width())
            y = random.randint(0, pixmap.height())
            size = random.randint(2, 6)
            color = QColor(
                random.randint(200, 255),
                random.randint(200, 255),
                random.randint(200, 255),
                random.randint(100, 200),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            polygon = QPolygonF()
            for i in range(self.polygon_sides):
                angle = 2 * math.pi * i / self.polygon_sides
                polygon.append(
                    QPointF(x + size * math.cos(angle), y + size * math.sin(angle))
                )
            painter.drawPolygon(polygon)

class CardPreviewDialog(QMainWindow):
    def __init__(self, parent, url, info, custom=False):
        effect_timer = QTimer()
        effect_timer.timeout.connect(self.update)
        effect_timer.start(100)  # Update every 100 ms
        super().__init__(parent)
        self.url = url
        self.info = info
        self.custom = custom
        self.setWindowTitle("Card Preview")
        parent_geom = parent.geometry()
        # print(f"Parent geometry: {parent_geom}")
        self.pixmap = parent._image_cache.get_cached_pixmap(url)
        self.pixmap = (
            self.pixmap.scaled(
                450,
                600,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            if self.pixmap
            else None
        )
        self.setGeometry(
            parent_geom.x(),
            parent_geom.y(),
            self.pixmap.width() if self.pixmap else 300,
            self.pixmap.height() if self.pixmap else 400,
        )
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if self.pixmap:
            self.image_label.setPixmap(
                self.pixmap.scaled(
                    300,
                    400,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        else:
            self.image_label.setText("Loading...")
        layout.addWidget(self.image_label)
        name_label = QLabel(info.get("card_name", "Unknown Card"))
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)
        # print(f"Preview dialog geometry: {self.geometry()}")
        self.update_pixmap()

    def update_pixmap(self):
        pixmap = self.parent()._image_cache.get_cached_pixmap(self.url)
        if pixmap:
            scaled = pixmap.scaled(
                self.width() - 40,
                self.height() - 100,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.image_label.setPixmap(scaled)
        else:
            # print(f"No pixmap cached for {self.url}, fetching...")
            self.parent()._fetch_image_async(self.url)

    def resizeEvent(self, event):
        self.update_pixmap()
        super().resizeEvent(event)
