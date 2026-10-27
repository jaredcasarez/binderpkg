"""Pure(ish) data model for a binder: slots, pages, and the overall binder state."""


import darkdetect
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor
from painter_colors import get_colors

from . import constants
from .constants import DEFAULT_COLS, DEFAULT_ROWS, _URL_RE


class BinderSlot(QObject):
    """One card position in a binder page."""

    slot_changed = Signal()

    def __init__(self, url: str = "", sleeved: bool = False) -> None:
        super().__init__()
        self._url = url
        self._info: dict | None = None
        self._sleeved = sleeved
        self._sleeve_color: str | None = None

    @property
    def sleeved(self) -> bool:
        return self._sleeved

    @property
    def empty(self) -> bool:
        return not self.url

    @property
    def set_code(self) -> str:
        if self.info:
            return self.info.get("set_code", "")
        m = _URL_RE.search(self.url)
        return m.group(1).upper() if m else ""

    @property
    def number(self) -> str:
        if self.info:
            return self.info.get("number", "")
        m = _URL_RE.search(self.url)
        return m.group(2) if m else ""

    @property
    def display_name(self) -> str:
        if self.info and self.info.get("card_name"):
            return self.info["card_name"]
        sc, num = self.set_code, self.number
        return f"{sc} #{num}" if sc and num else (self.url or "(empty)")

    @property
    def url(self) -> str:
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        if self._url != value:
            self._url = value
            self.slot_changed.emit()

    @property
    def info(self) -> dict | None:
        return self._info

    @info.setter
    def info(self, value: dict | None) -> None:
        self._info = value
        self.slot_changed.emit()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BinderSlot):
            return NotImplemented
        return (
            self.url == other.url
            and self.info == other.info
            and self.sleeved == other.sleeved
        )

class BinderPage(QObject):
    """One binder page holding cols × rows card slots."""

    page_changed = Signal()

    def __init__(self, cols: int, rows: int, label: str = "") -> None:
        super().__init__()
        self._border_color: str | None = None
        self._sel_color: str | None = None
        self.cols = cols
        self.rows = rows
        self.label = label
        self.slots: list[BinderSlot] = [BinderSlot() for _ in range(cols * rows)]
        for slot in self.slots:
            slot.slot_changed.connect(self._on_slot_changed)
        # self.page_changed = Signal()

    def slot_index(self, col: int, row: int) -> int:
        return row * self.cols + col

    def __iter__(self):
        return iter(self.slots)

    def _on_slot_changed(self):
        self.page_changed.emit()

class BinderData(QObject):
    """Full binder layout: a list of pages sharing the same grid dimensions."""

    data_changed = Signal(str, object)

    def __init__(self, cols: int = DEFAULT_COLS, rows: int = DEFAULT_ROWS) -> None:
        super().__init__()
        self.cols = cols
        self.rows = rows
        self.pages: list[BinderPage] = [BinderPage(cols, rows, "Page 1")]
        for page in self.pages:
            page.page_changed.connect(self._on_page_changed)
        self._custom_groups: dict = {}
        self._sel_page: int | None = None
        self._sel_slot: int | None = None
        self._sleeved = False
        self._theme = "dark" if darkdetect.isDark() ^ (not constants.MATCH_OS_THEME) else "light"
        self._sleeve_color: str = QColor(get_colors(self._theme)["default_sleeve"]).name()
        self._slot_color: str = QColor(get_colors(self._theme)["slot_empty"]).name()
        self._sel_color: str = QColor(get_colors(self._theme)["border_sel"]).name()
        self._border_color: str = QColor(get_colors(self._theme)["border"]).name()
        self._page_color: str = QColor(get_colors(self._theme)["page_bg"]).name()
        self._card_history: list[str] = []

    def add_page(self) -> BinderPage:
        p = BinderPage(self.cols, self.rows, f"Page {len(self.pages) + 1}")
        self.pages.append(p)
        return p

    def to_json(self) -> dict:
        for grp in self._custom_groups:
            self._custom_groups[grp].pop("img", None)
        return {
            "cols": self.cols,
            "rows": self.rows,
            "pages": [
                {"label": p.label, "slots": [s.url for s in p.slots]}
                for p in self.pages
            ],
            "custom_groups": self._custom_groups,
            "card_history": self._card_history,
            "sleeved": self._sleeved,
            "sleeve_color": self._sleeve_color,
            "slot_color": self._slot_color,
            "sel_color": self._sel_color,
            "border_color": self._border_color,
            "sel_page":self._sel_page,
            "sel_slot":self._sel_slot
        }

    @classmethod
    def from_json(cls, data: dict) -> "BinderData":
        b = cls(data.get("cols", DEFAULT_COLS), data.get("rows", DEFAULT_ROWS))
        b.pages.clear()
        for pd in data.get("pages", []):
            p = BinderPage(b.cols, b.rows, pd.get("label", ""))
            for i, url in enumerate(pd.get("slots", [])):
                if i < len(p.slots):
                    p.slots[i] = BinderSlot(url)
            b.pages.append(p)
        if not b.pages:
            b.pages.append(BinderPage(b.cols, b.rows, "Page 1"))
        b._custom_groups = data.get("custom_groups", {})
        b._sleeved = data.get("sleeved", False)
        b._sleeve_color = data.get("sleeve_color", None)
        b._slot_color = data.get("slot_color", None)
        b._sel_color = data.get("sel_color", None)
        b._border_color = data.get("border_color", None)
        b._sel_page = None
        b._sel_slot = None
        return b

    def __iter__(self):
        return iter(self.pages)

    def _on_page_changed(self):
        self.data_changed.emit("slot", (self._sel_page, self._sel_slot))

    @property
    def sel_slot(self):
        return self._sel_slot

    @sel_slot.setter
    def sel_slot(self, value):
            print("new_slot", value)
            self._sel_slot = value
            self.data_changed.emit("slot", (self._sel_page, self._sel_slot))
            

    @property
    def sel_page(self):
        return self._sel_page
    @sel_page.setter
    def sel_page(self, value):
        if self._sel_page == value:
            return
        else:
            print("new_page_")
            self._sel_page = value
            self.data_changed.emit("page", value)
