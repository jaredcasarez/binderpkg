"""Layout/UI constants, filesystem paths, and regexes shared across the app."""


import platform
import re
from pathlib import Path


WORKSPACE = Path(__file__).parent.parent

CUSTOM_DIR = WORKSPACE / "custom_cards"

THEME_DIR = WORKSPACE / "theme"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}

_URL_RE = re.compile(
    r"/cards/(?:[a-z]{2}(?:-[a-z]+)?/)?([A-Za-z0-9]+)/([A-Za-z0-9]+)$",
    re.IGNORECASE,
)

_CARD_PATH_RE = re.compile(r"^/cards/([A-Za-z0-9]+)/([A-Za-z0-9\-]+)$", re.I)

_CARD_W_MM = 63.0

_CARD_H_MM = 88.0

_REF_PX_PER_MM = 1.508

def _mm(mm: float) -> int:
    """Convert mm to reference canvas pixels."""
    return round(mm * _REF_PX_PER_MM)

CARD_W = _mm(_CARD_W_MM)

CARD_H = _mm(_CARD_H_MM)

SLEEVE_W_MM = 67.0

SLEEVE_W = _mm(SLEEVE_W_MM)

SLEEVE_H_MM = 92.0

SLEEVE_H = _mm(SLEEVE_H_MM)

SLOT_W_MM = 70.0

SLOT_W = _mm(SLOT_W_MM)

SLOT_H_MM = 97.0

SLOT_H = _mm(SLOT_H_MM)

SLOT_PAD_V_MM = 0.0

SLOT_PAD_H_MM = 4.0

PAGE_PAD_MM = 2

LABEL_H_MM = 0.0

GUTTER_MM = 25.0

OUTER_PAD_MM = 5.0

SLOT_PAD_V = _mm(SLOT_PAD_V_MM)

SLOT_PAD_H = _mm(SLOT_PAD_H_MM)

PAGE_PAD = _mm(PAGE_PAD_MM)

LABEL_H = _mm(LABEL_H_MM)

GUTTER = _mm(GUTTER_MM)

OUTER_PAD = _mm(OUTER_PAD_MM)

PAGE_CORNER_RADIUS = 6

SLOT_CORNER_RADIUS = 6

MATCH_OS_THEME = (
    True  # If True, automatically switch between light/dark based on OS theme
)

DPI = 300

CARD_W_MM = 63

CARD_H_MM = 88

PAGE_W_MM = 210

PAGE_H_MM = 297

DEFAULT_COLS = 3

DEFAULT_ROWS = 3

TABS = {
    0: "Search",
    1: "History",
    2: "Custom Cards",
}

MAX_HISTORY = 50

MAX_WORKERS = 10

PLATFORM = platform.system().lower()
