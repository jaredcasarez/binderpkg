"""PIL <-> QPixmap image caching plus background thread-pool workers that fetch
or cache card images."""


import io
from PIL import Image as PILImage
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtGui import QImage, QPixmap
from data.database import SessionLocal
from data.models import CachedCard
from pathlib import Path

from .constants import SLOT_H, SLOT_W, WORKSPACE


class ImageCacheManager:
    """Manages PIL Image ↔ QPixmap conversions with caching."""

    def __init__(self, thumb_size: tuple[int, int] = (SLOT_W - 4, SLOT_H - 4)):
        self._img_cache: dict[str, PILImage.Image] = {}
        self._pixmap_cache: dict[str, QPixmap] = {}
        self.thumb_size = thumb_size

    def pil_to_qpixmap(self, pil_image: PILImage.Image) -> QPixmap:
        """Convert PIL Image to QPixmap."""
        if pil_image.mode == "RGBA":
            data = pil_image.tobytes("raw", "RGBA")
            qimage = QImage(
                data,
                pil_image.width,
                pil_image.height,
                pil_image.width * 4,
                QImage.Format.Format_RGBA8888,
            )
        else:
            data = pil_image.tobytes("raw", "RGB")
            qimage = QImage(
                data,
                pil_image.width,
                pil_image.height,
                pil_image.width * 3,
                QImage.Format.Format_RGB888,
            )
        return QPixmap.fromImage(qimage)

    def cache_pil_image(self, url: str, pil_image: PILImage.Image) -> None:
        """Store PIL image in cache."""
        self._img_cache[url] = pil_image
        self._pixmap_cache.pop(url, None)

    def create_fullsize_pixmap(self, url: str) -> QPixmap | None:
        """Create full-size QPixmap from cached PIL image."""
        pil_img = self._img_cache.get(url)
        if not pil_img:
            return None
        pixmap = self.pil_to_qpixmap(pil_img)
        self._pixmap_cache[url] = pixmap
        return pixmap

    def create_thumbnail(self, url: str) -> QPixmap | None:
        """Create scaled thumbnail at thumb_size. Returns cached if available."""
        if url in self._pixmap_cache:
            return self._pixmap_cache[url]

        pil_img = self._img_cache.get(url)
        if not pil_img:
            return None

        if pil_img.mode == "RGBA":
            bg = PILImage.new("RGB", pil_img.size, (255, 255, 255))
            bg.paste(pil_img, mask=pil_img.split()[3])
            pil_img = bg

        tw, th = self.thumb_size
        img_ratio = pil_img.width / pil_img.height
        target_ratio = tw / th

        if img_ratio > target_ratio:
            new_height = th
            new_width = int(new_height * img_ratio)
        else:
            new_width = tw
            new_height = int(new_width / img_ratio)

        pil_img = pil_img.resize((new_width, new_height), PILImage.Resampling.LANCZOS)

        crop_x = (new_width - tw) // 2
        crop_y = (new_height - th) // 2
        pil_img = pil_img.crop((crop_x, crop_y, crop_x + tw, crop_y + th))

        pixmap = self.pil_to_qpixmap(pil_img)
        self._pixmap_cache[url] = pixmap
        return pixmap

    def get_cached_pil(self, url: str) -> PILImage.Image | None:
        """Get cached PIL image."""
        return self._img_cache.get(url)

    def get_cached_pixmap(self, url: str) -> QPixmap | None:
        """Get cached QPixmap thumbnail."""
        return self._pixmap_cache.get(url)

    def clear_pixmap_cache(self) -> None:
        """Clear QPixmap cache (use when thumb size changes)."""
        self._pixmap_cache.clear()

    def is_cached(self, url: str) -> bool:
        """Check if image is cached."""
        return url in self._img_cache

class ImageFetcherWorker(QRunnable):
    """Worker for fetching card images in thread pool."""

    class Signals(QObject):
        image_loaded = Signal(str, object)
        fetch_failed = Signal(str, str)

    def __init__(self, url: str, signals: "ImageFetcherWorker.Signals"):
        super().__init__()
        self.url = url
        self.signals = signals

    def run(self):
        """Runs in thread pool."""
        db = SessionLocal()
        img = None
        # # print(f"Fetching image for URL: {self.url}")
        if self.url.startswith("custom://"):
            if Path(self.url.replace("custom://", "")).exists():
                filename = self.url.replace("custom://", "")
                local = WORKSPACE / "custom_cards" / filename
                img = PILImage.open(local).convert("RGBA")
                self.signals.image_loaded.emit(self.url, img)
                return
        elif not self.url.startswith("custom://"):
            cached = db.query(CachedCard).filter_by(card_url=self.url).first()
            # # print(
            #     f"Checking cache for {self.url}: {'found' if cached else 'not found'}"
            # )
            if cached and cached.image_data:
                img = PILImage.open(io.BytesIO(cached.image_data)).convert("RGBA")
                # self._img_cache[self.url] = img
                # self._fetching.discard(self.url)
                self.signals.image_loaded.emit(self.url, img)
                return
        self.signals.fetch_failed.emit(self.url, "No cached image found")
        return

class ImageCacherWorker(QRunnable):
    """Worker for caching card images in thread pool."""

    class Signals(QObject):
        image_cached = Signal(str, object)
        cache_failed = Signal(str, str)

    def __init__(
        self, url: str, signals: "ImageCacherWorker.Signals", fetching_set: set[str]
    ):
        super().__init__()
        self.url = url
        self.signals = signals
        self.fetching_set = fetching_set
        self.setAutoDelete(True)

    def run(self):
        """Runs in thread pool."""
        db = SessionLocal()
        img = None
        # # print(f"Caching image for URL: {self.url}")
        # check if already cached or requested
        if self.url in self.fetching_set:
            # # print(f"Image for {self.url} already cached or failed; skipping.")
            return
        if self.url.startswith("custom://"):
            filename = self.url[len("custom://") :]
            local = WORKSPACE / "custom_cards" / filename
            img = PILImage.open(local).convert("RGBA")
            self.signals.image_cached.emit(self.url, img)
            return
        else:
            cached = db.query(CachedCard).filter_by(card_url=self.url).first()
            # # print(
            #     f"Checking cache for {self.url}: {'found' if cached else 'not found'}"
            # )
            if cached and cached.image_data:
                img = PILImage.open(io.BytesIO(cached.image_data)).convert("RGBA")
                self.signals.image_cached.emit(self.url, img)
                return
            else:
                self.signals.cache_failed.emit(self.url, "No cached image found")
                return
