import re
import io
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup
from PIL import Image
from sqlalchemy.orm import Session
from .models import CachedCard
import asyncio
from concurrent.futures import ThreadPoolExecutor
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}

_thread_local = threading.local()
RATE_LIMIT = 30  # requests/sec

_URL_RE = re.compile(
    r"/cards/(?:[a-z]{2}(?:-[a-z]+)?/)?([A-Za-z0-9]+)/([A-Za-z0-9]+)$",
    re.IGNORECASE,
)
_CARD_PATH_RE = re.compile(r"^/cards/([A-Za-z0-9]+)/([A-Za-z0-9\-]+)$", re.I)


# ── Rate limiter ────────────────────────────────────────────────────────────

class RateLimiter:
    def __init__(self, rate: float) -> None:
        self._interval = 1.0 / rate
        self._lock = threading.Lock()
        self._last: float = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


_rate_limiter = RateLimiter(RATE_LIMIT)


def _get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist={429, 500, 502, 503, 504},
            allowed_methods=["GET"],
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return _thread_local.session

IMAGE_CACHE_DIR = Path(__file__).parent / "data" / "image_cache"
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

executor = ThreadPoolExecutor(max_workers=5)


# ── Image helpers ───────────────────────────────────────────────────────────

def _extract_image_url(soup: BeautifulSoup) -> str | None:
    """Return the CDN URL of the card image from the page, or None."""
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "cdn.digitaloceanspaces.com" in src and ("/tpci/" in src or "/tpc/" in src):
            return src
        if "images.pokemontcg.io" in src and "_hires" in src:
            return src
    return None


def _get_image_bytes(
    session: requests.Session,
    set_code: str,
    number: str,
    image_url: str | None,
) -> bytes | None:
    """
    Return image bytes from the CDN URL.
    Images are fetched without rate-limiting because they come from a CDN.
    """
    if not image_url:
        return None
    try:
        resp = session.get(image_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException:
        return None


# ── Parsing helpers ──────────────────────────────────────────────────────────

def _extract_card_name(soup: BeautifulSoup, set_code: str) -> str:
    """
    Extract the card name from the page <title>.
    Title format: "Card Name - Set Name (SET_CODE) #Number – Limitless"
    """
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text()
        m = re.match(rf"^(.+?)\s+-\s+.+?\({re.escape(set_code)}\)", title)
        if m:
            return m.group(1).strip()
        if "–" in title:
            return title.split("–")[0].strip()
    return ""


def _extract_card_type(soup: BeautifulSoup) -> str:
    """Return the card supertype from the page (Pokémon / Trainer / Energy)."""
    type_tag = soup.find("p", class_="card-text-type")
    if type_tag:
        type_text = type_tag.get_text()
        type_text = type_text.replace("\n", " ")
        type_words = re.split(r"\s+", type_text.strip())
        if type_words[0] == "Pokémon" and type_words[2] != "Basic":
            type_phrase = f"{type_words[0]}: {type_words[2]} {type_words[3]}"
        else:
            type_phrase = f"{type_words[0]}: {type_words[2]}"
        return type_phrase
    return ""


def _extract_set_info(soup: BeautifulSoup, set_code: str, number: str, lang: str = None) -> tuple[str, str]:
    """
    Return (set_name, release_date) from the set info link on the card page.
    Link text format: "Set Name (SET_CODE)#NUMBER · Rarity"
    """
    if lang:
        set_link = soup.find("a", href=f"/cards/{lang}/{set_code}")
    else:
        set_link = soup.find("a", href=f"/cards/{set_code}")
    if not set_link:
        return "", ""

    set_name, release_date = "", ""
    session = _get_session()
    try:
        resp = session.get(f"https://limitlesstcg.com{set_link['href']}", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        set_soup = BeautifulSoup(resp.text, "html.parser")
        release_tag = set_soup.find("div", class_="infobox-line")
        name_tag = set_soup.find("div", class_="infobox-heading sm")
    except requests.RequestException:
        return "", ""


    if set_link:
        text = name_tag.get_text(strip=True)
        set_name = text.split("(")[0].strip() if "(" in text else text.strip()
        if release_tag:
            release_date = re.match(r".*?(\d{1,2}[a-z]{2} [a-zA-Z]* \d{4})", release_tag.get_text(strip=True))
            # print(f"Release date raw: {release_date}")
            try:
                # print(f"Card {set_code} {number} release date raw: {release_date.group(1)}")
                release_date = release_date.group(1)
                release_date_cleaned = re.sub(r'(\d{1,2})(st|nd|rd|th)', r'\1', release_date)
                # print(f"Release date matched: {release_date}")
                if not re.match(r'\d', release_date_cleaned[0]):
                    release_date_cleaned = "0" + release_date_cleaned
                # print(f"Card {set_code} {number} release date cleaned: {release_date_cleaned}")
                date_obj = datetime.strptime(release_date_cleaned, "%d %B %Y") if release_date_cleaned else None
                date_iso = date_obj.date().isoformat() if date_obj else None
                
            except Exception:
                date_obj = None
    return set_name, date_obj


def _extract_rarity(soup: BeautifulSoup, set_code: str, number: str, lang: str = None) -> str:
    """Return the rarity from the set info link on the card page."""
    if lang:
        set_link = soup.find("a", href=f"/cards/{lang}/{set_code}")
    else:
        set_link = soup.find("a", href=f"/cards/{set_code}")
    if set_link:
        text = set_link.get_text(strip=True)
        m = re.match(
            rf"^(.+?)\s*\({re.escape(set_code)}\)\s*#{re.escape(number)}\s*[·•]\s*(.+)$",
            text,
        )
        if m:
            return m.group(2).strip()
    return ""


def _extract_prices(soup: BeautifulSoup, set_code: str, number: str, lang: str = None) -> tuple[str, str, str, str]:
    """
    Return (usd_price, eur_price, tcgplayer_url, cardmarket_url).
    """
    usd, eur, tcp_url, cm_url = "", "", "", ""

    current_card_soup = soup.find("tr", class_="current")
    if current_card_soup:
        for a_tag in current_card_soup.find_all("a", class_='card-price usd', href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)
            if not usd:
                usd = text.replace("$", "").strip()
                tcp_url = href
        for a_tag in current_card_soup.find_all("a", class_='card-price eur', href=True):
            href = a_tag["href"]
            text = a_tag.get_text(strip=True)
            if not eur:
                eur = text.replace("€", "").strip()
                cm_url = href

    if usd and eur:
        return usd, eur, tcp_url, cm_url

    for table in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not any("prints" in h.lower() for h in headers):
            continue
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            cell_text = cells[0].get_text(strip=True)
            if not re.search(rf"#{re.escape(number)}\b", cell_text, re.IGNORECASE):
                continue
            if not usd and len(cells) >= 2:
                m = re.search(r"\$([0-9,]+\.[0-9]{2})", cells[1].get_text())
                if m:
                    usd = m.group(1)
                a = cells[1].find("a", href=True)
                if a and not tcp_url:
                    tcp_url = a["href"]
            if not eur and len(cells) >= 3:
                m = re.search(r"([0-9,]+\.[0-9]{2})€", cells[2].get_text())
                if m:
                    eur = m.group(1)
                a = cells[2].find("a", href=True)
                if a and not cm_url:
                    cm_url = a["href"]
            break

    return usd, eur, tcp_url, cm_url


def _extract_symbol_bytes(soup: BeautifulSoup) -> bytes | None:
    """Return the bytes of the set symbol image, or None if not found."""
    symbol_img = soup.find("img", class_="set")
    if symbol_img and symbol_img.get("src"):
        symbol_url = symbol_img["src"]
        session = _get_session()
        try:
            resp = session.get(symbol_url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.content
        except requests.RequestException:
            return None
    return None

def _extract_card_id(soup: BeautifulSoup) -> str | None:
    card_id_tag = soup.find("section", class_="card-page-market")
    card_id_script = card_id_tag.find("script")
    if card_id_tag:
        txt = card_id_script.get_text()
        return int(txt.strip().split("= ")[-1])
# ── Price history (TCGPlayer Infinite API) ──────────────────────────────────

def get_tcgplayer_set_code(tcgplayer_url: str) -> str | None:
    """Extract the card unique ID from the TCGPlayer URL."""
    m = re.search(r"%2Fproduct%2F(\d+)", tcgplayer_url)
    if m:
        return m.group(1)
    return None


def fetch_price_history(limitless_cardid: str) -> dict[str, list[tuple[str, float]]]:
    """
    Fetch TCGPlayer price history for the given product ID from limitlesstcg.com API.

    :return: Dict with 'tcgplayer' and 'cardmarket' keys, each containing a list of (date_str, price) tuples sorted ascending (oldest first).
    """
    if not limitless_cardid:
        return {'tcgplayer': [], 'cardmarket': []}

    api_url = f"https://limitlesstcg.com/api/cards/{limitless_cardid}/prices"
    session = _get_session()
    try:
        resp = session.get(api_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        # print(data)
        prices = data.get("tcgplayer", [])
        # print(prices)
        tcgp_history = {entry[0]: entry[1] for entry in prices}
        
        # tcgp_history.sort(key=lambda x: x[0])
        cm_history = {entry[0]: entry[1] for entry in data.get("cardmarket", [])}
        # cm_history.sort(key=lambda x: x[0])
        
        return {'tcgplayer': tcgp_history, 'cardmarket': cm_history}
    except requests.RequestException:
        return {'tcgplayer': [], 'cardmarket': []}

def _price_change_pct(
    history: list[tuple[str, float]], current_price: float, days: int
) -> float | None:
    """
    Return the % change from the price ~`days` ago to `current_price`.
    Returns None when insufficient history exists.
    """
    if not history or not current_price:
        return None
    target = (date.today() - timedelta(days=days)).isoformat()
    past_price = next((p for d, p in reversed(history) if d <= target), None)
    if past_price is None or past_price == 0:
        return None
    return round(((current_price - past_price) / past_price) * 100, 1)


# ── Main fetch function ──────────────────────────────────────────────────────

def fetch_card_info(url: str, fetch_images: bool = False, fetch_prices: bool = False) -> dict:
    """Fetch and parse comprehensive card info + prices for one card URL."""
    url = url.strip()
    to_match = r"https?://limitlesstcg\.com/cards/([A-Za-z0-9_-]+/)?([A-Za-z0-9]+)/([A-Za-z0-9]+)$"
    m = re.match(to_match, url)
    if not m:
        return {"url": url, "error": "Invalid URL"}

    set_code = m.group(2).upper()
    number = m.group(3)
    lang = m.group(1)[:-1].lower() if m.group(1) else None

    _rate_limiter.acquire()
    session = _get_session()
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        return {"url": url, "set_code": set_code, "number": number, "error": str(exc)}

    soup = BeautifulSoup(resp.text, "html.parser")

    card_name = _extract_card_name(soup, set_code)
    card_type = _extract_card_type(soup)
    card_id = _extract_card_id(soup)
    # print(card_id)
    rarity = _extract_rarity(soup, set_code, number, lang)
    set_name, release_date = _extract_set_info(soup, set_code, number, lang)
    # print(f"Set name: {set_name}, Release date: {release_date}")
    usd_price, eur_price, tcp_url, cm_url = _extract_prices(soup, set_code, number, lang)
    # print('before')
    price_arrays = fetch_price_history(card_id) if fetch_prices else {}
    cm_price_array = price_arrays['cardmarket'] if fetch_prices else []
    tcg_price_array = price_arrays['tcgplayer'] if fetch_prices else []
    # print('after')
    symbol_bytes = _extract_symbol_bytes(soup)
    image_url = _extract_image_url(soup) 
    if fetch_images:
        image_data = _get_image_bytes(session, set_code, number, image_url)
    else:
        image_data = None

    product_id = get_tcgplayer_set_code(tcp_url) if tcp_url else None
    # tcg_history = fetch_tcgplayer_price_history(product_id) if product_id else []
    usd_float = float(usd_price.replace(",", "")) if usd_price else None

    result = {
        "card_url": url,
        "set_code": set_code,
        "tcgplayer_set_code": product_id,
        "set_name": set_name,
        "release_date": release_date,
        "number": number,
        "card_name": card_name,
        "card_type": card_type,
        "rarity": rarity,
        "usd_price": usd_price,
        "eur_price": eur_price,
        "tcgplayer_url": tcp_url,
        "cardmarket_url": cm_url,
        "symbol_bytes": symbol_bytes,
        "image_data": image_data,
        "image_url": image_url,
        "tcgplayer_price_data": tcg_price_array,
        "cm_price_data": cm_price_array,
        "lang": lang,
    }

    return result



def extract_set_and_number(url: str) -> tuple[str, str]:
    """Extract set code and card number from card URL."""
    m = _URL_RE.search(url)
    if m:
        return m.group(1).upper(), m.group(2)
    return "", ""



# ── Async wrappers for FastAPI ───────────────────────────────────────────────

async def fetch_card_info_async(url: str, db: Session | None = None) -> dict:
    """Async wrapper around fetch_card_info for use in FastAPI."""
    loop = asyncio.get_event_loop()

    def _fetch():
        # Try database cache first
        if db:
            cached = db.query(CachedCard).filter_by(card_url=url).first()
            if cached and cached.card_info:
                return cached.card_info

        # Fall back to scraping
        return fetch_card_info(url)

    return await loop.run_in_executor(executor, _fetch)


async def fetch_image_async(url: str, db: Session | None = None) -> bytes | None:
    """Async wrapper around image fetching for use in FastAPI."""
    if not url:
        return None

    loop = asyncio.get_event_loop()

    def _fetch_sync():
        # Try database cache first
        if db:
            cached = db.query(CachedCard).filter_by(card_url=url).first()
            if cached and cached.image_data:
                return cached.image_data

        # Fetch from web
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.content
        except Exception:
            return None

    return await loop.run_in_executor(executor, _fetch_sync)


    """Resize image to fit in max dimensions, maintaining aspect ratio."""
    try:
        img = Image.open(io.BytesIO(image_data)).convert("RGBA")
        img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return image_data


def search_limitless_tcg(query: str) -> list[str]:
    """Search limitlesstcg.com for cards matching query."""
    try:
        url = f"https://limitlesstcg.com/cards?q={quote(query, safe='')}&show=all"
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen, urls = set(), []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if _CARD_PATH_RE.match(href):
            full = "https://limitlesstcg.com" + href
            if full not in seen:
                seen.add(full)
                urls.append(full)

    return urls


def get_cached_cards(
    db: Session,
    query: str = "",
    sort_by: str = "card_name",
    sort_dir: str = "asc",
    offset: int = 0,
    limit: int = 100,
) -> dict:
    """
    Get cached cards with optional filtering and sorting.

    Args:
        db: Database session
        query: Search query to filter by card name, set code, or card number
        sort_by: Field to sort by (card_name, set_code, release_date, card_type, rarity)
        sort_dir: Sort direction ('asc' or 'desc')
        offset: Pagination offset
        limit: Pagination limit

    Returns:
        {"cards": [...], "total": N} where each card includes card_info + url
    """
    q = db.query(CachedCard)

    # Filter by search query
    if query:
        query_lower = query.lower()
        filtered = []
        for cache in q.all():
            info = cache.card_info or {}
            if (
                query_lower in info.get("card_name", "").lower()
                or query_lower in info.get("set_code", "").lower()
                or query_lower in info.get("number", "").lower()
            ):
                filtered.append(cache)
        results = filtered
    else:
        results = q.all()
    print(f"Found {len(results)} cards matching query '{query}'")

    # Sort results
    sort_key = _get_sort_key(sort_by)
    reverse = sort_dir.lower() == "desc"
    results.sort(key=sort_key, reverse=reverse)

    # Paginate
    total = len(results)
    paginated = results[offset : offset + limit]

    # Build response
    cards = []
    for cache in paginated:
        info = cache.card_info or {}
        cards.append(
            {
                "url": cache.card_url,
                **info,
            }
        )

    return {"cards": cards, "total": total}


def _get_sort_key(field: str):
    """Return a sort key function for the given field."""

    def sort_key(cache: CachedCard):
        info = cache.card_info or {}
        value = info.get(field, "")

        # Handle numeric sorting
        if field == "number":
            # Extract numeric part for proper sorting (e.g., "123" before "25a")
            m = re.match(r"(\d+)", str(value))
            if m:
                return (0, int(m.group(1)), value)
            return (1, 0, value)

        # Handle date sorting
        if field == "release_date":
            return value or ""

        # Default string sorting
        return str(value).lower()
