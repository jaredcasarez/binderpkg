# Binder Layout Maker — Architecture & Feature Outline

Reference document for porting/reimplementing the Pokémon TCG "Binder Layout
Maker" desktop app. Scope is limited to `binder_app/`, `data/`, and `theme/`.
Two external symbols are referenced but live outside this scope and must be
re-implemented or stubbed by a porter: `painter_colors.get_colors(theme)`
(returns a dict of hex colors keyed by role, described in §0.3) and the
`WORKSPACE/custom_cards/` directory for user-uploaded card images.

This is a **single-process desktop application**, not a client/server system.
"Backend" below means the data-model / persistence / business-logic layer
(plain Python + SQLAlchemy + scraping), and "Frontend" means the PySide6 (Qt)
UI layer (windows, widgets, painting, dialogs). They run in the same process
and communicate via direct method calls and Qt signals/slots — there is no
network or IPC boundary between them. Some modules (e.g. `image_cache.py`)
straddle the line (background I/O workers feeding UI widgets); they're
classified below by their primary responsibility and cross-referenced.

## 0. Cross-cutting basics

### 0.1 Tech stack & entry point
- **Python**, **PySide6** (Qt6) for UI, **PIL/Pillow** for image manipulation,
  **SQLAlchemy** (SQLite) for the persistent card cache, **requests** +
  **BeautifulSoup** for scraping limitlesstcg.com, **darkdetect** for OS theme
  detection, **openpyxl** for XLSX export.
- Entry point: `binder_app/app.py` `main()` — creates `QApplication`,
  instantiates `BinderApp`, applies `dark.qss`/`light.qss` from `theme/` based
  on `darkdetect.isDark()` XOR `constants.MATCH_OS_THEME`, shows window, runs
  the event loop. Accepts an optional CLI arg = initial binder JSON file to
  load.
- Module map (`binder_app/__init__.py`): `constants`, `history`, `models`,
  `image_cache`, `renderer`, `widgets`, `dialogs`, `main_window`, `app`.

### 0.2 Purpose / feature summary
A desktop app for designing physical trading-card **binder page layouts**: a
grid (cols×rows configurable 1–5) of "card slots" per page, multiple pages
viewed as single-page or two-page "spreads". Users can:
- Search limitlesstcg.com for real cards (scraped + cached in SQLite), or
  load a list of card URLs from a text file.
- Drag/drop, click-to-place, or keyboard-navigate cards into slots; reorder
  via drag-and-drop or shift left/right.
- Create **custom cards** from any local image (crop/rotate/tile into an
  N×M grid of custom "tiles") for proxies/dividers.
- Maintain placement **history** (recently placed cards) and full
  **undo/redo**.
- Theme the binder (border/page/slot/selection/sleeve colors; light/dark app
  theme), toggle card sleeves (per-slot or globally) with custom sleeve
  colors.
- Save/load binder layouts as JSON; export to **PDF** (print-ready card
  sheet), **XLSX** (spreadsheet with thumbnails + card metadata), or a
  **custom-cards-only PDF** (print sheet of just the custom card tiles,
  optionally split).

### 0.3 Shared constants & configuration — `binder_app/constants.py`
Consumed by *both* layers (data-model defaults and UI rendering). All layout
math is mm-based, converted to reference pixels via
`_REF_PX_PER_MM = 1.508` (helper `_mm(mm) = round(mm * 1.508)`), then scaled
at render time to fit the canvas.

| Constant | Value | Meaning |
|---|---|---|
| `WORKSPACE` | repo root (parent of `binder_app/`) | base path |
| `CUSTOM_DIR` | `WORKSPACE/custom_cards` | saved custom-card tile PNGs |
| `THEME_DIR` | `WORKSPACE/theme` | qss stylesheets |
| `_CARD_W_MM` / `_CARD_H_MM` | 63.0 / 88.0 | physical card size → `CARD_W`/`CARD_H` px |
| `SLEEVE_W_MM` / `SLEEVE_H_MM` | 67.0 / 92.0 | sleeve overlay size → `SLEEVE_W`/`SLEEVE_H` |
| `SLOT_W_MM` / `SLOT_H_MM` | 70.0 / 97.0 | per-slot bounding box → `SLOT_W`/`SLOT_H` |
| `SLOT_PAD_H_MM` / `SLOT_PAD_V_MM` | 4.0 / 0.0 | spacing between custom-card tiles |
| `PAGE_PAD_MM` | 2 | padding inside page border before first slot |
| `GUTTER_MM` | 25.0 | gap between two pages in double-page view |
| `OUTER_PAD_MM` | 5.0 | margin between canvas edge and page(s) |
| `PAGE_CORNER_RADIUS`/`SLOT_CORNER_RADIUS` | 6 | rounded-rect radius (px, unscaled) |
| `MATCH_OS_THEME` | `True` | if true, app theme follows OS dark/light |
| `DPI` | 300 | export resolution |
| `CARD_W_MM`/`CARD_H_MM` | 63/88 | export card size |
| `PAGE_W_MM`/`PAGE_H_MM` | 210/297 (A4) | export page size |
| `DEFAULT_COLS`/`DEFAULT_ROWS` | 3/3 | new binder grid |
| `TABS` | `{0:"Search",1:"History",2:"Custom Cards"}` | sidebar tab names |
| `MAX_HISTORY` | 50 | history pool cap |
| `MAX_WORKERS` | 10 | (declared, thread pool actually capped to 1 — see §A.8) |
| `HEADERS` | Chrome UA string | used for all scraping HTTP requests |
| `_URL_RE` | `/cards/(lang/)?(SET)/(NUM)$` | parses set code & number from a Limitless card URL |
| `_CARD_PATH_RE` | `^/cards/(SET)/(NUM)$` | matches search-result relative hrefs |

### 0.4 External theme-color contract
`painter_colors.get_colors(theme: "dark"|"light") -> dict` must supply at
least these keys, consumed throughout both layers: `default_sleeve`,
`slot_empty`, `border_sel`, `border`, `page_bg`, `canvas_bg`. The data layer
(`BinderData`) uses these as *default* color values (overridable per-binder);
the UI layer (`renderer.py`, `widgets.py`) uses them as paint-time fallbacks
whenever no override is set. A port must provide an equivalent provider with
this exact key set to preserve default-color behavior.

---

# PART A — Backend (data model, persistence, business logic)

## A.1 Directory structure — `data/`
```
data/
  __init__.py        (empty)
  models.py           SQLAlchemy ORM declarations
  database.py         engine/session/init_db setup
  services.py         scraping, parsing, price-history, search
  cache_cards.py       standalone bulk-cache CLI script
  data/
    binder.db          SQLite file (created at runtime)
    image_cache/        cache dir referenced by services.py
```

## A.2 SQLAlchemy ORM models — `data/models.py`
Declarative `Base` tables:
- **`BinderLayout`** — id, name, cols, rows, timestamps; relationships to
  `BinderPage`/`CustomCardGroup` (cascade delete). *Unused by the live
  app* — the desktop app persists purely to flat JSON files (see §A.6's
  `BinderData.to_json`/`from_json`), not this schema. Kept in the codebase as
  an alternate/future DB-backed persistence design (e.g. for a web backend).
- **`BinderPage`** (DB) — id, layout_id (FK), page_number, label;
  relationship to `BinderSlot` (DB). *Unused by the live app*, same caveat.
- **`BinderSlot`** (DB) — id, page_id (FK), slot_index, card_url, card_info
  (JSON). *Unused by the live app.*
- **`CardURL`** — id, unique `url`, created_at. Used as a queue/manifest of
  all known card URLs (populated by `cache_cards.py`'s crawl). **Actively
  used.**
- **`CachedCard`** — the actively-used cache table: `card_url` (unique),
  `image_data` (binary PNG/JPEG bytes), `set_code`, `tcgplayer_set_code`,
  `set_name`, `release_date`, `number`, `card_name`, `card_type`, `rarity`,
  `usd_price`, `eur_price`, `tcgplayer_url`, `cardmarket_url`,
  `symbol_bytes` (set-symbol icon image), `image_url`, `lang`, `created_at`.
  **Actively used** by both the GUI's fetch pipeline (§A.8) and the CLI
  bulk-cacher (§A.5).
- **`CardPrices`** — `card_url` (unique), `tcgplayer_price_data` (JSON:
  date→price map), `cm_price_data` (JSON). **Actively used.**
- **`CustomCardGroup`** (DB) — id, layout_id (FK), group_id, label, tiles
  (JSON list of urls), cols, rows. *Unused by the live app* — custom groups
  are instead stored inline in the binder JSON (`BinderData._custom_groups`,
  §A.6).

## A.3 Database setup — `data/database.py`
- SQLite at `data/data/binder.db` (`DB_DIR = data/data`, auto-created).
  Engine via `create_engine(..., connect_args={"check_same_thread": False},
  pool_size=0)` — the `check_same_thread=False` flag is required because
  both the UI thread and background `QRunnable` workers open their own
  sessions concurrently.
- `SessionLocal = sessionmaker(...)`; `init_db()` creates all tables;
  `get_db()` is a FastAPI-style dependency-injection generator (unused
  directly by the Qt app, which calls `SessionLocal()` ad hoc wherever a DB
  session is needed).

## A.4 Scraping / business logic — `data/services.py`
All scraping targets `limitlesstcg.com` card pages with a thread-local
`requests.Session` (retry policy: 3 retries, backoff 1s, on
`{429,500,502,503,504}`), and a global `RateLimiter` (default 30 req/s,
mutex + monotonic-clock spacing).
- **`fetch_card_info(url, fetch_images=False, fetch_prices=False)`** — the
  core scrape: validates URL against
  `https?://limitlesstcg\.com/cards/(lang/)?(SET)/(NUM)$`, fetches the page,
  and parses via BeautifulSoup:
  - `_extract_card_name`: from `<title>` tag, pattern
    `"Name - Set (CODE) #N – Limitless"`.
  - `_extract_card_type`: from `<p class="card-text-type">`, builds
    `"Pokémon: Stage X"` / `"Trainer: Y"` style strings.
  - `_extract_card_id`: numeric card id parsed out of an inline `<script>`
    inside `<section class="card-page-market">` (used for the price-history
    API).
  - `_extract_rarity`: parsed from the set-info link's text via regex
    `(.+?)\s*\(SET\)\s*#NUM\s*[·•]\s*(.+)`.
  - `_extract_set_info`: follows the set link to the set's own page, scrapes
    set name (`.infobox-heading`) and release date (`.infobox-line`,
    regex-extracted `"DDth Month YYYY"` → cleaned →
    `datetime.strptime("%d %B %Y")`).
  - `_extract_prices`: prefers the `<tr class="current">` row's
    `a.card-price.usd`/`.eur` links; falls back to scanning any `<table>`
    with a "prints" header column, matching the row whose first cell
    contains `#NUMBER`.
  - `_extract_symbol_bytes`: downloads the `<img class="set">` icon bytes.
  - `_extract_image_url`: finds an `<img>` whose `src` is from DigitalOcean
    CDN (`/tpci/` or `/tpc/`) or `images.pokemontcg.io` with `_hires` —
    returns that URL; `_get_image_bytes` downloads it (no rate limit — CDN,
    not the scraped site).
  - If `fetch_prices`: calls `fetch_price_history(card_id)` → hits
    `limitlesstcg.com/api/cards/{id}/prices` and returns
    `{tcgplayer: {date:price}, cardmarket: {date:price}}` dicts.
  - Returns a flat dict matching `CachedCard` column names plus
    `image_data`, `tcgplayer_price_data`, `cm_price_data`.
- **`search_limitless_tcg(query)`**: GETs the site search page, collects all
  `/cards/SET/NUM`-matching hrefs, dedups, returns absolute URLs.
- **`get_cached_cards(db, query, sort_by, sort_dir, offset, limit)`**:
  in-memory filter (substring match on name/set/number, case-insensitive) +
  sort (via `_get_sort_key`, numeric-aware for `number`) + paginate over the
  `CachedCard` table; returns `{"cards": [...], "total": N}`.
- Async wrappers (`fetch_card_info_async`, `fetch_image_async`) exist for a
  hypothetical FastAPI integration (not used by the desktop app) — DB-cache
  first, falls back to scrape/HTTP; run via a module-level
  `ThreadPoolExecutor(max_workers=5)`.
- `get_tcgplayer_set_code(url)` / `_price_change_pct(history, current,
  days)` helper utilities (percent change over N days from a date→price
  history).

## A.5 Bulk cache builder (CLI script) — `data/cache_cards.py`
Standalone multiprocessing crawler to pre-populate `CachedCard`/`CardPrices`
for the entire Limitless TCG catalog (not invoked by the GUI app at runtime,
but is the mechanism that fills the cache the GUI reads from).
- CLI flags: `--prices`, `--images`, `--rebuild` (clears `CachedCard` table),
  `--limit N`, `--workers N` (default `cpu_count()`), `--rps`
  (requests/sec, default `2×workers`).
- `get_all_card_urls`: scrapes
  `limitlesstcg.com/cards?q=%20unique:prints&show=all` (and all paginated
  result pages via a `ul.pagination[data-max]` attribute), collects every
  card link, persists each as a `CardURL` row (acts as a durable work
  queue/manifest).
- `cache_card_worker(args)`: per-URL worker (run in a `multiprocessing.Pool`)
  — skips if already cached (and price-cached when `--prices`), else calls
  `fetch_card_info(fetch_images=False, fetch_prices=...)`, splits off the
  price-history payload into a separate `CardPrices` insert, commits a
  `CachedCard` row.
- `cache_all_cards(...)`: builds a `(url, delay, fetch_images, fetch_prices)`
  tuple per URL (delay spread evenly to respect the global rate limit) and
  runs them via `pool.imap_unordered` with a `tqdm` progress bar.
- `rebuild_cache(db)`: truncates `CachedCard`.
- `main()`: wires argparse → `init_db()` → determine already-cached/priced
  URL sets → crawl any missing `CardURL`s if the table is empty → run
  `cache_all_cards` on the delta.

## A.6 In-app binder data model — `binder_app/models.py`
The "business object" layer for a binder layout. Built on Qt's `QObject` for
change-notification signals (so the frontend can react reactively), but the
state itself and its JSON serialization are pure data/business logic —
**not** tied to any specific rendering code. Persisted purely to flat JSON
files (the desktop app does **not** use the `data/models.py` SQLAlchemy
layout tables described in §A.2).

- **`BinderSlot`**: `url` (card URL or `custom://<filename>` or empty),
  `info` (dict of fetched card metadata), `sleeved` (bool), `sleeve_color`
  (optional hex override). Emits `slot_changed`. Derived props: `empty`,
  `set_code`/`number` (from `info` or regex-parsed from `url`),
  `display_name` (card name or `"SET #NUM"` or `"(empty)"`).
- **`BinderPage`**: `cols`, `rows`, `label`, `slots: list[BinderSlot]`
  (length `cols*rows`, index = `row*cols+col`). Emits `page_changed` when any
  child slot changes.
- **`BinderData`**: top-level model.
  - `cols`, `rows`, `pages: list[BinderPage]` (starts with one `"Page 1"`).
  - `_custom_groups: dict[url -> group]` — custom card metadata (see
    §B.5.14).
  - `_sel_page`, `_sel_slot` — current selection (properties with setters
    that emit `data_changed(str, object)` signal: `"slot"` with
    `(page, slot)` payload, or `"page"` with page index payload).
  - Color state (all optional hex strings, `None` = use theme default):
    `_sleeve_color`, `_slot_color`, `_sel_color`, `_border_color`,
    `_page_color`; `_sleeved` (global sleeve toggle bool).
  - `_theme` computed once at construction from `darkdetect`/
    `MATCH_OS_THEME`; defaults pulled via `painter_colors.get_colors(theme)`
    (see §0.4).
  - `_card_history: list[str]` (legacy; actual history now lives on
    `BinderApp`, see §B.5.9).
  - `add_page()` appends a new `BinderPage` labeled `"Page N"`.
  - `to_json()` / `from_json(dict)`: full round-trip serialization. Shape:
    ```json
    {
      "cols": int, "rows": int,
      "pages": [{"label": str, "slots": ["<url or ''>", ...]}],
      "custom_groups": {"<url>": {...}},
      "card_history": [str],
      "sleeved": bool,
      "sleeve_color": str|null, "slot_color": str|null,
      "sel_color": str|null, "border_color": str|null,
      "sel_page": int|null, "sel_slot": int|null
    }
    ```
    Note: `to_json` strips an `"img"` key from each custom group before
    serializing (PIL/pixmap handles aren't JSON-able). `from_json` always
    resets `sel_page`/`sel_slot` to `None`.

## A.7 History / undo-redo engine — `binder_app/history.py`
- **`BinderHistory`**: simple MRU list of URLs (`max_size`, `add()` dedups +
  front-inserts, `get_recent()`). (Superseded in practice by `BinderApp`'s
  own history-pool/tree bookkeeping, §B.5.9.)
- **`BinderUndoRedo`**: snapshot-based undo/redo over whole `BinderData`
  objects (deep-copied via `to_json`/`from_json` round trip each push — not a
  true command pattern).
  - `push_state_change(binder_data)`: diffs the incoming state against the
    last undo-stack entry; if no diff, skips (avoids duplicate states);
    otherwise deep-copies and appends, clears redo stack, returns the diff
    dict (or `None`).
  - `undo()` / `redo()`: pop/push between stacks, each time deep-copying via
    JSON round-trip; return the new `BinderData` or `None` if stack empty.
  - `diff(a, b)` (classmethod): compares two `BinderData.to_json()` outputs
    and returns `{"binder": [...], "pages": [...], "slots": [...]}` or
    `None` if identical:
    - `"grid"` added to `binder` list if `cols`/`rows` differ.
    - `"color"` added if any of `border_col, sel_color, slot_color, sleeved,
      sleeve_color` differ (note: checks key `"border_col"`, not
      `"border_color"` — likely a latent bug to preserve or fix when
      porting).
    - Per-page/per-slot diffs recorded as page indices / `(page_idx,
      slot_idx)` tuples. This diff feeds the frontend's
      `BinderApp._refresh_binder` selective-redraw system (§B.5.6).

## A.8 Image caching & background fetch workers — `binder_app/image_cache.py`
Bridges the data layer (SQLite blob cache, local filesystem) and the UI layer
(QPixmap display); classified here because its primary job is cache/data
management, but it is directly consumed by frontend widgets.
- **`ImageCacheManager`**: in-memory dict caches — `_img_cache: url→PIL.Image`,
  `_pixmap_cache: url→QPixmap` (thumbnail sized to `(SLOT_W-4, SLOT_H-4)` by
  default).
  - `pil_to_qpixmap`: manual RGBA/RGB buffer→`QImage`→`QPixmap` conversion.
  - `cache_pil_image(url, img)`: stores full image, invalidates stale
    pixmap.
  - `create_fullsize_pixmap(url)`: pixmap at native resolution (used for
    previews).
  - `create_thumbnail(url)`: flattens RGBA onto white background, resizes +
    letterbox-crops to fill `thumb_size` exactly (cover-fit then center-crop),
    caches result.
  - `get_cached_pil`/`get_cached_pixmap`/`is_cached`/`clear_pixmap_cache`
    (called on grid-size change since thumb geometry changes).
- **`ImageFetcherWorker(QRunnable)`**: given a URL, loads from local cache
  only (does **not** hit the network) — `custom://` → reads file from
  `custom_cards/`; else queries `CachedCard` table for `image_data` blob.
  Emits `image_loaded(url, PIL.Image)` or `fetch_failed(url, reason)`.
- **`ImageCacherWorker(QRunnable)`**: same cache-only lookup pattern but
  named for the "caching" path invoked after a card's full info (incl. image
  bytes) has just been fetched (`fetching_set` dedup guard to avoid repeat
  work); emits `image_cached`/`cache_failed`.
- Both workers open their own `SessionLocal()` DB session (thread-safe since
  SQLite `connect_args={"check_same_thread": False}`).
- The frontend (`BinderApp`) runs a single-thread `QThreadPool`
  (`setMaxThreadCount(1)`, `IdlePriority`) for these workers — i.e., image
  I/O is serialized, not parallel, to avoid hammering the SQLite file/UI
  thread.

---

# PART B — Frontend (Qt UI layer)

## B.1 Directory / module structure — `binder_app/` (UI-facing modules)
```
binder_app/
  app.py          QApplication bootstrap, theme application, main()
  renderer.py     Page/grid layout math + paint orchestration
  widgets.py      RoundedCornerCanvas (binder surface) + CardSlotWidget
  dialogs.py      SlotColorsDialog, CustomCardDialog, SparkleEffect,
                  CardPreviewDialog
  main_window.py  BinderApp (QMainWindow) — menus, docks, tabs, shortcuts,
                  export, all user-interaction wiring
theme/
  dark.qss, light.qss
```

## B.2 App bootstrap — `binder_app/app.py`
`main()`: creates `QApplication`, reads an optional CLI arg (initial binder
JSON path), instantiates `BinderApp(initial_file, app)`, computes theme name
from `darkdetect.isDark() ^ (not constants.MATCH_OS_THEME)`, applies the
corresponding `theme/<name>.qss` file as the app-wide stylesheet, shows the
window, runs `app.exec()`.

## B.3 Renderer — `binder_app/renderer.py`
`BinderRenderer` is the single source of truth for page/grid layout, created
by `BinderApp` and bound to the canvas (`RoundedCornerCanvas`) post-
construction. It reads binder state from the data layer (§A.6) but owns all
*presentation* math and Qt painting/widget bookkeeping.

- **View state**: `page_view` (`"single"` | `"double"`), `spread_idx` (which
  spread/page-pair is showing), `slot_widgets: dict[(page_idx, slot_idx) ->
  CardSlotWidget]`.
- **`get_viewable_pages()`**: single view → `[sel_page or spread_idx*2]` (if
  in range); double view → `[spread_idx*2, spread_idx*2+1]` (clipped to page
  count).
- **`get_page_scale()`**: fits the page(s)+padding into canvas size — single
  view uses full canvas width minus outer padding; double view divides
  remaining width (minus `GUTTER`) by 2. Returns `min(width_scale,
  height_scale)`.
- **`get_page_pos(spread_pos)`**: left page → `x=OUTER_PAD` (double) or
  horizontally centered (single); right page → `x = canvas_width -
  OUTER_PAD*2 - scaled_page_width` (pins to right edge, i.e. doesn't account
  for gutter explicitly — right page position is canvas-edge-relative, not
  left-page-relative).
- **`paint(painter)`**: for each viewable page (spread position 0/1),
  computes position+scale and calls `_paint_page`; resets `canvas.slot_rects`
  each call.
- **`_paint_page`**: draws rounded-rect page background/border (page color +
  border color, selection-color border + pen width 2 when that page is
  `_sel_page`). In double-page view, also draws **stacked "shadow" rounded
  rects** offset by `4*scale` px per page behind/ahead — odd pages get
  shadows of upcoming pages stacked down-right, even pages get shadows of
  preceding pages stacked down-left — simulating a flipped-through binder's
  page stack depth.
- **`place_slots()`**: positions/sizes every live `CardSlotWidget` for
  currently-viewable pages (hides/un-raises widgets on non-viewable pages),
  records `(page_idx, slot_idx, QRect)` into `canvas.slot_rects` for
  hit-testing, and marks selected-slot widgets via `set_selected`.
- **`update_viewable_slots()`**: toggles each widget's `_draw` flag (gates
  whether the widget responds to mouse/drag events) based on page
  visibility.
- **`update_slot_widgets(slots=None, url=None)`**: lazily creates a
  `CardSlotWidget` per `(page,slot)` the first time it's needed, wiring its
  `clicked`/`right_clicked`/`doubleclicked`/`dropped` signals to `BinderApp`
  handlers; then selectively refreshes widget state (sleeve flag/color,
  slot/sel/border colors, `card_url`, pixmap) either for all slots, a given
  `slots` list, or all slots holding a given `url`. Calls
  `_refresh_widget_image` to pull from `image_cache` or kick off an async
  fetch + background-cache-and-history-pool registration. Prunes widgets
  whose slot key no longer exists (e.g., grid shrink).
- **`redraw(to_redraw)`**: top-level dispatcher driven by a dict with
  optional keys `"colored"` (recolor all widgets), `"pages"` (rebuild page
  button panel), `"slots"` (list of `(p,s)` to refresh, or `[]` meaning "all
  currently viewable slots" — recomputes via `place_slots()` +
  `update_slot_widgets()` + `update_viewable_slots()`). Always finishes with
  `canvas.update()`.

## B.4 Widgets — `binder_app/widgets.py`

### `RoundedCornerCanvas(QWidget)`
- Signals: `slot_clicked`, `slot_right_clicked`,
  `slot_dragged(src_p,src_s,dst_p,dst_s)`, `slot_dragging`,
  `empty_space_clicked`.
- `paintEvent`: paints rounded-rect canvas background (theme
  `border`/`canvas_bg` colors) then delegates to
  `self._renderer.paint(painter)`.
- Hit-testing (`_hit_test`) against `slot_rects` recorded by the renderer;
  mouse press/release on a hit emits click/drag signals, on empty space
  emits `empty_space_clicked`; drag threshold = 5px manhattan length.
- `set_page_view(view)`: setter that also emits the parent app's
  `update_request` signal to trigger a re-layout.

### `CardSlotWidget(QWidget)` — one visual card slot
- Fixed size `SLOT_W × SLOT_H`; accepts drops; tracks `_draw`
  (interactive-enabled flag from renderer), `card_url`, `_pixmap`,
  `_is_selected`, per-widget color overrides (`_sleeved`, `_sleeve_color`,
  `_slot_color`, `_sel_color`, `_border_color`), `_fill_size` (true for
  `custom://` cards — fills slot via `KeepAspectRatioByExpanding` instead of
  normal `KeepAspectRatio`).
- `set_card(url, pixmap)`: sets tooltip (`"Custom card: <file>"` or
  `"Card URL: <url>"`), `empty` flag.
- `set_selected(bool)` triggers repaint.
- `paintEvent`: rounded-rect background in slot color; selection border =
  dashed pen `[1,2,1,2]` pattern width 3 in `sel_color`, else solid width-1
  `border_color`; if sleeved and not a custom card, draws a centered
  sleeve-color rounded rect sized `SLEEVE_W×SLEEVE_H*scale`; draws the card
  pixmap centered (scaled to fill for custom cards, else aspect-fit to
  `CARD_W×CARD_H*scale`).
- Mouse: left-click → `clicked` signal (only if not already selected);
  right-click → `right_clicked`; double-click → `doubleclicked`; drag (left-
  button move beyond threshold, non-empty slot) starts a `QDrag` with mime
  type `application/x-card-slot` = `"page_idx,slot_idx"` plus plain text =
  card URL; drag pixmap = rendered widget snapshot (or theme-colored blank if
  no image yet). `dropEvent` reads the mime payload and re-emits as
  `dropped(src_page, src_slot, self.page_idx, self.slot_idx)`.
- All interaction handlers early-return if `self._draw` is `False` (i.e., the
  widget belongs to a currently non-viewable page).

## B.5 Dialogs — `binder_app/dialogs.py`

### `SlotColorsDialog(QMainWindow)`
Small per-slot color-override panel: three buttons (sleeve/border/selection
color) each showing a small color-swatch `QIcon`, wired to color choosers on
the currently-selected slot. *(Note: referenced in the app but there's no
visible call site wiring it up in `main_window.py`'s current code path —
likely superseded by the right-click context menu's "Edit sleeve color..."
action; keep for completeness/porting parity.)*

### `CustomCardDialog(QMainWindow)` — create a custom card/tile-sheet from an image
- Controls: "Browse Image..." (file picker scoped to `custom_cards/`),
  cols/rows spinboxes (1–8 each), a live crop-preview canvas, Cancel/"Add to
  Binder (N tiles)" buttons.
- Loads image via PIL → RGBA; `_init_crop()` computes an initial centered
  crop box matching target aspect ratio `= (CARD_W_MM*cols +
  PAD_H_MM*(cols-1)) / (CARD_H_MM*rows + PAD_V_MM*(rows-1))`.
- `_draw_crop_preview()`: custom-paints the source image scaled to fit the
  canvas, dims everything outside the crop box (semi-transparent black
  overlay), draws crop-box outline (cyan) + dashed grid lines dividing it
  into the `cols×rows` tile grid, 4 orange corner-handle squares (6px
  half-size) used for resize.
- **Mouse interactions**: click-drag inside the crop box moves it (clamped
  to image bounds); click-drag on a corner handle resizes the crop box from
  that corner while preserving the target aspect ratio; mouse-move
  continuously recomputes handle positions in global coords for hit-testing.
- **Rotation**: `Ctrl+MouseWheel` rotates by wheel-notch count × 5°;
  `Left`/`Right` arrow keys rotate ±9° (±1° with Shift held) around a running
  `_angle` (mod 360), re-rotating the *original* source image each time
  (`self._src_img.rotate(angle)`), not cumulative on the already-rotated
  image.
- **Confirm (`_confirm`)**: crops the (possibly rotated) image to the box;
  computes per-tile pixel size from the mm ratios of card size vs. total
  grid+padding size; saves the **full crop** as `custom://<uuid>.png` (the
  group's `"url"`), then slices it into `cols*rows` individual tiles (each
  `card_px_w+pad_px_w/2` × `card_px_h+pad_px_h/2`, tiled with
  50%-overlap-of-padding offset per row/col — i.e. tile crops slightly
  overlap into the padding gap) saved as separate `custom://<uuid>.png`
  files listed in `group["children"]`. Invokes the provided
  `callback(group)` (wired to `BinderApp._on_custom_card_added`) then closes.

### `SparkleEffect(QGraphicsEffect)`
Decorative/unused-by-default `QGraphicsEffect` subclass: redraws the source
pixmap plus N random small colored polygons ("sparkles") on a timer
(`sparkle_density`, `polygon_sides`, `sparkle_speed` params).

### `CardPreviewDialog(QMainWindow)`
Standalone pop-up showing a large preview of a selected card: pixmap scaled
to fit (max 450×600, displayed scaled further to fit the label at up to
300×400 initially), card name label underneath, and a periodic 100ms timer
that calls `update()` (ties into `SparkleEffect`/live redraw).
`resizeEvent`/`update_pixmap()` re-scale or trigger an async fetch if not yet
cached.

## B.6 Main window — `binder_app/main_window.py`
`BinderApp(QMainWindow)` is the orchestrator owning the data layer
(`BinderData`, `BinderUndoRedo`), the frontend rendering layer
(`BinderRenderer`), the image cache/thread pool, and all UI construction and
event wiring.

### B.6.1 Window chrome & layout
- Title `"Binder Layout Maker — Pokémon TCG"`; initial geometry `1340×820`,
  min size `960×640`.
- Central widget: a `QSplitter` holding the **binder panel** (canvas + page
  strip) as the main pane. Left-side **dock widgets**: a card-picker dock
  (`_picker_widg`, holds the tabbed Search/History/Custom-Cards lists) and a
  preview dock (`_prev_widg`, shows selected card image + "Place in Selected
  Slot" button) — both dockable left/right, toggleable via View ▸ Windows
  menu, resettable to default position.
- Status bar with a single `QLabel` message area (`_update_status`).

### B.6.2 Menu bar
- **File**: New, Open..., Save, Save as...
- **Edit**: Undo, Redo, Copy, Paste
- **View**: Dark mode (checkable, toggles `constants.MATCH_OS_THEME` and
  re-applies the opposite qss file), Single page (checkable → toggles
  `page_view`), **Windows** submenu (Card picker / Preview visibility
  checkboxes; "Reset to default" re-docks both to the left area and shows
  them)
- **Pages**: Add page, Remove page, then a **Rows** section (radio-like
  checkable actions for 1–5 rows) and a **Columns** section (1–5 cols) —
  each wired to `_set_grid_size(rows, cols)`
- **Style**: Set border/page/slot/selection color (each opens
  `QColorDialog`, stores hex on `BinderData`, updates the action's swatch
  icon), Toggle all sleeves (checkable), Set default sleeve color, Reset
  colors to default (clears all overrides back to theme defaults and resets
  swatch icons)
- **Export**: Export spreadsheet... (XLSX), Export custom cards... (opens a
  group-selector dialog then produces PDF)

### B.6.3 Grid size change (`_set_grid_size`)
If any page has a populated slot **and** the new slot count differs from the
old **and** the change would shrink rows/cols, warns the user with
Ok/Cancel (rebuilds unchecked row/col menu state on cancel). On confirm:
updates `binder.cols/rows`, rebuilds every page's slot list preserving
existing `BinderSlot` objects by index (new slots beyond old length get
preserved too, truncation drops extras), refreshes row/col menu check-states,
triggers a `"grid"`-type refresh.
(There's also a parallel/legacy `_on_grid_change` path driven by spinboxes
that instead rebuilds a brand-new `BinderData`, capping slot copy to
`min(old,new)` length — both code paths coexist; port whichever the UI
actually wires to spinboxes if kept.)

### B.6.4 Card picker panel (left dock)
Three-tab `QTabWidget` (**Search**, **History**, **Custom Cards**), each
backed by its own `QTreeWidget` (4 columns: Name, Set, #, Date; icon = 40×66
thumbnail) except Custom Cards (2 columns: Image, Row/Column —
hierarchical: parent = custom group, children = individual tiles).
- **Search tab**: text entry + "Search" button (`_do_search` → `_bg_search`
  scrapes `limitlesstcg.com/cards?q=...&show=all` for `/cards/SET/NUM`
  links, then batch-fetches each card's info/pricing/image into the DB cache
  and the pool); "Load file..." button to bulk-import card URLs from a
  `.txt` file (one URL per line, `#`-prefixed lines ignored, `!*#` leading
  chars stripped).
- **History tab**: shows the MRU placed-card list (parent items can be a
  custom group with its child tiles nested); "Clear history" button.
- **Custom Cards tab**: lists all custom groups (parent) and their tiles
  (children); "+Custom" button opens `CustomCardDialog`.
- Context menu (right-click any tree row): Remove from pool, Open in
  Browser, Copy card, Preview card.
- Selecting a tree item (`_on_tree_select`) updates the preview dock (name
  label + scaled pixmap, fetching async if not cached) and sets
  `_picked_url`.
- Double-clicking a tree item or a slot places the picked card
  (`_place_selected`/`_on_slot_widget_double_clicked`): assigns `slot.url`/
  `slot.info`, marks unsaved, records to history pool.

### B.6.5 Binder panel (right/main pane)
- `RoundedCornerCanvas` (the binder render surface) sized to fill available
  space; resizing the panel triggers `_refresh_binder()`.
- **Pages strip** below the canvas: horizontally-scrolling row of page
  buttons (checkable, shows label + card count, e.g. `"Page 2\n(5)"`), `+`/`-`
  buttons to add/remove pages, each button's context menu offers "Go to
  Page" and "Rename...". Clicking a page button selects that page and jumps
  the spread to it.

### B.6.6 Selective redraw pipeline (`_refresh_binder`)
Central re-render coordinator, called after essentially every mutation, with
an optional `binder` mode string, `pages`/`slots` index lists, or
precomputed `diffs`:
- `diffs is None` → calls `undo_redo.push_state_change(binder)` to get a
  diff (also pushes undo history as a side effect — see §A.7).
- `binder="diff"` → redraw scope chosen from the diff (slots > pages >
  binder-level color/grid).
- `binder="grid"` → full widget rebuild + full pages/slots redraw.
- `binder="color"` → only recolor existing widgets (`"colored"` key).
- `binder="view"` → full pages/slots redraw (used for page-view toggle).
- else with `pages=[...]` or `slots=[...]` explicit → targeted partial
  refresh.
- else (no args) → full refresh of slots+pages+colors.
All paths end by calling `renderer.redraw(to_redraw_dict)`.
`_on_binder_data_changed` (bound to `BinderData.data_changed`) marks
`_unsaved=True` and refreshes just the current selection slot.

### B.6.7 Undo/redo (`_undo`/`_redo`)
Pop from the appropriate stack (§A.7), re-diff against the current binder,
swap `self._binder` to the restored snapshot if a diff exists, mark unsaved,
refresh using `binder="diff"` with the precomputed diff.

### B.6.8 File I/O
- **New** (`_new_binder`): prompts to discard unsaved changes; resets to a
  fresh `BinderData()`.
- **Open** (`_open_file`/`_load_file`): prompts to discard unsaved changes;
  `QFileDialog` filtered to `*.json`; on load: `BinderData.from_json`,
  re-applies grid size via `_set_grid_size`, resets spread/selection, clears
  History/Custom trees and repopulates custom groups from the loaded
  `_custom_groups` dict (`_repopulate_custom_groups`, which also nulls out
  slot URLs whose backing custom image files are missing — see
  `_check_for_missing_images`), updates window title to the filename.
- **Save / Save as** (`_save_file`/`_save_as`/`_write_file`): serializes
  `binder.to_json()` as pretty JSON (`indent=2, default=str`) to the tracked
  filepath (defaults to `WORKSPACE/binder.json` on first save).

### B.6.9 History pool bookkeeping (`_add_to_history_pool`)
On every card placement: if the URL is a custom child tile, resolves to its
parent group URL (`_find_custom_group_from_child_url`) and, if not already
present, inserts a **parent tree item** (the group) with **all of its child
tiles nested beneath it** (collapsed by default) into the History tab;
otherwise inserts a flat item for a regular card. Caps the History
tree/list at `MAX_HISTORY` (oldest entries evicted from both the tree and
the `_card_history` list).

### B.6.10 Card/image fetch pipeline (UI-facing orchestration)
- `_bg_fetch_url(url, picker=True)` (runs on a background `threading.Thread`):
  initializes the SQLite DB if missing, looks up `CachedCard` by URL; if
  absent, calls `data.services.fetch_card_info(url, fetch_prices=True,
  fetch_images=True)`, splits off `tcgplayer_price_data`/`cm_price_data`
  into a `CardPrices` row, inserts a `CachedCard` row, commits. Emits
  `card_info_fetched(url, info, picker)` back to the main thread.
- `_on_card_info_fetched`: stores in `_pool_info`, triggers
  `image_from_cache_async` (fetch the raw image bytes via
  `ImageCacherWorker`), and if `picker` is true, adds the card to the
  currently-active tree (`_add_to_pool`).
- `_fetch_image_async(url, priority=True)`: dedup-guarded (`_fetching` set)
  `ImageFetcherWorker` dispatch via the single-threaded `QThreadPool`
  (priority param maps to Qt thread-pool priority 0/1, used to prioritize
  visible-page images).
- `_on_image_loaded`/`_on_image_from_cache`: cache the PIL image + full
  pixmap, refresh any tree item icon currently showing that URL, refresh the
  preview pane if that item is selected, and call
  `renderer.update_slot_widgets(url=url)` so every slot displaying that card
  re-renders with the new thumbnail.

### B.6.11 Slot interaction handlers
- **Click** (`_on_slot_widget_clicked`): sets `_sel_page/_sel_slot`, syncs
  the tree selection to the slot's card (if any), updates status bar,
  redraws just that slot.
- **Double-click** (`_on_slot_widget_double_clicked`): if a tree item is
  currently selected, places its URL into the clicked slot (equivalent to
  "place selected" at that position), then also runs the single-click
  logic.
- **Right-click** (`_on_slot_widget_right_clicked`): builds a context menu —
  if the slot has a card: Open in Browser, Remove Card, Copy slot, Paste,
  Shift Left/Right, a checkable Sleeve toggle, Edit sleeve color...; if
  empty: Paste, (Place Card from Pool if something's picked), checkable
  Sleeve toggle + Edit sleeve color (with swatch icon).
- **Drag/drop** (`_on_slot_dragged`/`_on_slot_widget_dropped`): swaps the two
  `BinderSlot` objects between source/destination (full swap, not move — so
  dragging a card onto an occupied slot exchanges them), selects the
  destination slot, refreshes both slots.
- **Empty-space click** (`_on_empty_space_clicked`): clears `_sel_page`/
  `_sel_slot` and refreshes.
- **Context-menu-only actions**: `_ctx_remove` (resets slot to a fresh empty
  `BinderSlot`), `_ctx_open_in_browser` (via `QDesktopServices.openUrl`),
  `_ctx_shift(delta)` (swaps the selected slot with its ±1 neighbor within
  the same page's flat slot array — not grid-aware diagonally, pure linear
  index swap bounded to the page), `_toggle_sleeve`/`_edit_sleeve_color`
  (per-slot overrides via `QColorDialog`).

### B.6.12 Keyboard shortcuts (`keyReleaseEvent`, auto-repeat ignored)
With **Ctrl** held:
- `Z` undo, `Y` redo, `C` copy slot URL to clipboard, `V` paste clipboard URL
  into selected slot, `S` save, `O` open, `N` new.
- `Left/Right/Up/Down`: **move the card** out of the selected slot into the
  adjacent slot *within the same page*, wrapping at row/column edges (only
  succeeds if the destination slot is empty; otherwise no-op), and updates
  selection to follow the card.

Without Ctrl:
- `Enter/Return`: triggers search if the search box has focus, else places
  the currently-selected tree card into the selected empty slot.
- `Delete`: clears the selected slot's card.
- `Escape`: deselects the current slot/page (if canvas or a slot widget has
  focus) and/or deselects the active tree (if it has focus).
- `Tab`/`Shift+Tab` (`Key_Backtab`): moves selection to the next/previous
  slot index within the current page (wrapping), **without** moving any
  card — pure navigation.
- `Left`/`Right`/`Up`/`Down` (no modifier): **moves the selection cursor**
  (not the card) across the grid; `Left` at column 0 jumps to the previous
  page's rightmost column in the same row (if a previous page exists);
  `Right` at the last column presumably jumps forward similarly (symmetric
  logic); `Up`/`Down` wrap within the page only (no cross-page jump
  implemented for vertical nav).

`resizeEvent` on the main window also triggers `_refresh_binder()`.

### B.6.13 Theming & sleeves
- `_toggle_dark_mode(checked)`: flips `constants.MATCH_OS_THEME`, re-applies
  the opposite `.qss` stylesheet, forces a `"color"` refresh.
- `_choose_*_color` family (slot/page/border/selection/sleeve): opens
  `QColorDialog.getColor()`, stores the hex string on `BinderData`, triggers
  a `"color"` refresh, updates the triggering menu action's swatch icon
  (`_create_color_icon`: a 32×16 transparent `QPixmap` with a 15×15 colored,
  black-bordered rect drawn at x=16).
- `_reset_colors`: nulls every color override, re-reads theme defaults via
  `painter_colors.get_colors(theme)` for each swatch icon.
- `_on_sleeve_binder_toggled`: sets the binder-wide `_sleeved` flag (slots
  without a per-slot override follow this).

### B.6.14 Custom card groups (`_add_custom_card`, `_on_custom_card_added`, `_add_custom_to_pool`)
- "+Custom" disables the main window, opens `CustomCardDialog` (§B.5);
  re-enables the window on close.
- On confirm, `_on_custom_card_added` loads the saved main crop + every
  child tile PNG from `custom_cards/` into the `ImageCacheManager` (§A.8),
  then calls `_add_custom_to_pool` which:
  - Inserts a parent `QTreeWidgetItem` into the **Custom Cards** tab tree
    (icon = main crop thumbnail, text = card name or `"Custom Card N"`,
    column 2 = `"rows x cols"`), expanded by default.
  - Inserts one child item per tile (icon = tile thumbnail, text = `"(row,
    col)"` 1-indexed position), and stores the group into
    `BinderData._custom_groups[url] = {url, card_name, set_code:"",
    number:"", release_date:"", children: {tile_url: {card_name, set_code,
    number, release_date}}, cols, rows}`.
  - Calls `_add_to_history_pool(main_url)`.
- Group/tile management context menu (`_on_custom_tree_click`,
  right-click): Remove Custom Group (deletes the whole group + tree node),
  Remove Custom Card (deletes one tile from a group's children), Rename
  Custom Card (renames the group's display name via `QInputDialog`).

### B.6.15 Export features
- **PDF export of the full binder** (`_export_pdf`/`_do_export_pdf`,
  background thread): for each page, pastes each occupied slot's cached PIL
  image (resized to the mm-derived card pixel size at `DPI=300`, centered
  within an A4-sized `RGBA` canvas) at its grid position; saves all pages as
  a single multi-page PDF via Pillow (`save_all=True,
  append_images=...`).
- **XLSX export** (`_export_card_spreadsheet`): one row per occupied
  non-custom slot with columns Image (embedded 40×66 thumbnail via
  `openpyxl.drawing.image.Image`), Card Name, Page, Slot, Card URL, Set
  (name + code), Card Number, Release Date; row height fixed to 75.
- **Custom cards PDF export** (`_on_export_custom_cards` →
  `CustomCardExportDialog` → `_export_custom_cards_pdf`):
  - `CustomCardExportDialog` (nested `QMainWindow` class) shows a scrollable
    grid of every custom group's thumbnail (click to toggle selection,
    highlighted border), a "Split each custom group into individual cards"
    checkbox, and an Export button.
  - If **split**: lays out every selected group's *individual tile images*
    (not the merged crop) across A4 pages at full card size with
    horizontal/vertical spacing derived from the mm margins, wrapping
    rows/pages as needed.
  - If **not split**: lays out each selected group's *merged crop image*
    (full tile-sheet) at its native grid footprint across A4 pages, drawing
    bold black grid-division lines between tiles (via `PIL.ImageDraw`) so
    the sheet can be hand-cut along slot boundaries, likewise paginating
    when a page fills up.
  - Final save path chosen via `QFileDialog`; multi-page PDF written with
    Pillow at `DPI=300`.

### B.6.16 Status/misc
- `_update_status(message)`: sets the status-bar label.
- `_check_for_missing_images`: on load, scans custom groups + all slots for
  `custom://` URLs whose backing file no longer exists in `custom_cards/`;
  clears those slot URLs and prunes the corresponding custom-group entries.
- Delegation shims (`_get_viewable_pages`, `_get_page_scale`,
  `_get_page_pos`, `_place_slots`, `_update_slot_widgets`,
  `_clear_slot_widgets`, `_redraw`, plus `_page_view`/`_spread_idx`/
  `_slot_widgets` properties) simply forward to `self._renderer` (§B.3) for
  backward compatibility with older single-file code.

## B.7 Theme stylesheets — `theme/`
Two Qt stylesheet (`.qss`) files, `dark.qss` and `light.qss` (same selector
structure, light uses an inverted/lighter palette), applied wholesale to the
`QApplication` in `app.py`/on dark-mode toggle. Styled selectors include:
global font (`Segoe UI`/`Arial`, 13px, no focus outline), `QMenuBar`/
`QMenu` (rounded popups, blue `#3070e0` selection highlight), `QToolBar`,
`QPushButton` (rounded, hover/pressed/checked/disabled states), `QLabel`,
`QLineEdit`/`QSpinBox` (rounded, focus border), `QSlider`, `QSplitter`,
`QTabWidget`/`QTabBar` (rounded top-corner tabs), `QTreeWidget`/
`QHeaderView`, `QScrollArea`/`QScrollBar` (12px, rounded thumb),
`QStatusBar`, `QDialog`/`QMessageBox`/`QInputDialog`/`QFileDialog`,
`QToolTip`, plus two **custom object-named selectors** the app relies on:
`QWidget#binder_panel` (the right-hand panel's bordered/rounded background)
and `QPushButton#page_button` (small page-nav buttons, font-size 10px, blue
`:checked` state). Any port should preserve these two `objectName`s since
Python code sets them directly (`setObjectName("binder_panel")`,
`setObjectName("page_button")`) and the stylesheet selectors key off them.

---

## Appendix: Layer interaction summary (for porting)

| Concern | Backend owner | Frontend owner |
|---|---|---|
| Binder layout state (pages/slots/colors) | `binder_app/models.py` (`BinderData`) | consumed read/write by `main_window.py`, rendered by `renderer.py`/`widgets.py` |
| Undo/redo | `binder_app/history.py` | triggered by `main_window.py` key/menu actions |
| Card metadata + images (cache) | `data/models.py`, `data/database.py`, `data/services.py` | fetched/displayed via `binder_app/image_cache.py` workers → `widgets.py`/`dialogs.py` |
| Bulk pre-caching | `data/cache_cards.py` (offline CLI) | n/a (consumes the same SQLite DB) |
| Layout geometry math | n/a | `binder_app/constants.py` (shared) + `binder_app/renderer.py` |
| Persistence format | `BinderData.to_json/from_json` (JSON file) | `main_window.py` file-dialog I/O |
| Theming | `painter_colors` contract (external) + color fields on `BinderData` | `theme/*.qss` + `renderer.py`/`widgets.py` paint code |

A faithful port should keep the data layer (Part A) framework-agnostic where
possible (it has no Qt-painting dependencies beyond `QObject`/`Signal` for
change notification) so it can be swapped onto a different UI toolkit or a
real client/server split, while Part B can be freely reimplemented in
whatever UI framework the target platform uses, as long as it respects the
geometry constants (§0.3), the JSON persistence shape (§A.6), and the theme
color contract (§0.4).
