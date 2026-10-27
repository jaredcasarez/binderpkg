"""
Bulk cache all Pokémon cards from Limitless TCG into the database using multiprocessing.
Run this script once to populate the cache, then the web server will serve from it.

Usage:
    python cache_cards.py                    # cache all cards
    python cache_cards.py --rebuild          # clear and rebuild cache
    python cache_cards.py --limit 100        # test with first 100 cards
    python cache_cards.py --workers 4        # use 4 worker processes
    python cache_cards.py --rps 5            # limit to 5 requests per second
"""

import argparse
import os
import sys
import time
from multiprocessing import Pool, cpu_count
from pathlib import Path
from tqdm import tqdm

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).parent))

from database import init_db, SessionLocal
from models import CachedCard, CardURL, CardPrices
from services import fetch_card_info

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
}



def get_all_card_urls(count=None, stored=set(), db: Session = None) -> list[str]:
    """
    Generate a list of cards based on the specified criteria and save it to a text file.

    :return: List of card URLs.
    """
    base_url = "https://limitlesstcg.com/cards?q="
    search_url = f"{base_url}%20unique%3Aprints&show=all"
    print(f"Search URL: {search_url}")
    # Fetch the search results page
    response = requests.get(search_url)
    response.raise_for_status()
    # Parse the HTML content
    soup = BeautifulSoup(response.text, 'html.parser')
    if soup.find('ul', class_='pagination'):
        print("Pagination detected. Fetching all pages...")
        max_page = soup.find('ul', class_='pagination').get("data-max")
        page_urls = [search_url + "&page=" + str(i) for i in range(1, int(max_page) + 1)]
    else:
        page_urls = [search_url]
    raw_links = []
    for page_url in page_urls:
        print(f"Fetching page: {page_url}")
        page_response = requests.get(page_url)
        page_response.raise_for_status()
        page_soup = BeautifulSoup(page_response.text, 'html.parser')
        
        page_grid_container = page_soup.find('div', class_='card-search-grid')
        
        if page_grid_container:
            raw_links.extend(page_grid_container.find_all('a', href=True))
        card_urls = [f"https://limitlesstcg.com{link['href']}" for link in raw_links]
        for url in card_urls:
            db_entry = db.query(CardURL).filter_by(url=url).first()
            if not db_entry:
                db.add(CardURL(url=url))
                db.commit()
            print(f"Added card URL to database: {url}")
    print(len(raw_links))
    raw_links = [link for link in raw_links if f"https://limitlesstcg.com{link['href']}" not in stored]
    print(len(stored))
    
    print(f"Found {len(raw_links)} new card links to cache.")
    stored.update(f"https://limitlesstcg.com{link['href']}" for link in raw_links)

    urls = [f"https://limitlesstcg.com{link['href']}" for link in raw_links]
    
    if count:
        urls = urls[:count]
    return urls


def cache_card_worker(args_tuple) -> tuple[str, str, bool]:
    """Worker function to fetch and cache a single card in a separate process."""
    url, delay, fetch_images, fetch_prices = args_tuple

    if delay > 0:
        time.sleep(delay)

    db = SessionLocal()
    try:
        # Check if already cached
        cached = db.query(CachedCard).filter_by(card_url=url).first()
        price_cached = db.query(CardPrices).filter_by(card_url=url).first()
        # print(price_cached , fetch_prices)
        if cached and (price_cached and fetch_prices):
            return url, None, True
    
        # Fetch comprehensive card info
        card_info = fetch_card_info(url, fetch_images=False, fetch_prices=fetch_prices)
        # print(card_info["tcgplayer_price_data"])
        price_info = {"card_url": url, "tcgplayer_price_data":card_info.pop("tcgplayer_price_data"), "cm_price_data":card_info.pop("cm_price_data")}
        # print(price_info)
        if not card_info or card_info.get("error"):
            print("error")
            return url, None, False

        # Store in database
        if not cached:
            cache_entry = CachedCard(**card_info)
            db.add(cache_entry)
        if fetch_prices:
            # print("priceinfo",price_info)
            # print(price_info)
            price_cache_entry = CardPrices(**price_info)
            
            db.add(price_cache_entry) 
        db.commit()
        card_name = card_info.get('card_name', 'Unknown')
        # print(card_name)
        os.stdout.flush()
        # print(f"✓ Cached card {cache_entry.id:>05d}: {card_name:^30} {f'({url})':<45}")
        return url, card_name, True

    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        return url, None, False
    finally:
        db.close()


def cache_all_cards(urls: list[str], stored_images, stored_prices, limit: int = None, num_workers: int = None, requests_per_second: float = None, images: bool = False, prices: bool = False) -> None:
    """Cache all cards using multiprocessing with rate limiting."""
    if limit:
        urls = urls[:limit]

    if num_workers is None:
        num_workers = cpu_count()

    # Calculate delay per request to respect rate limit
    # Spread requests evenly across workers to avoid bursts
    if requests_per_second is None:
        requests_per_second = num_workers * 2  # Default: 2 requests/sec per worker

    delay_per_request = 1.0 / requests_per_second if requests_per_second > 0 else 0

    print(f"\nCaching {len(urls)} cards using {num_workers} processes...")
    print(f"Rate limit: {requests_per_second} requests/second (delay: {delay_per_request:.2f}s per request)")

    # Prepare work tuples with delays
    work_items = [(url, delay_per_request, False,  stored_prices is not None and (url not in stored_prices)) for url in urls]

    with Pool(processes=num_workers) as pool:
        results = pool.imap_unordered(cache_card_worker, work_items)
        success_count = sum(1 for _, _, success in tqdm(results, total=len(urls), desc="Caching cards") if success)

    db = SessionLocal()
    try:
        total_cached = db.query(CachedCard).count()
        print(f"\n✓ Cache complete: {total_cached} cards in database ({success_count} newly cached)")
    finally:
        db.close()


def rebuild_cache(db: Session) -> None:
    """Clear the card cache and rebuild from scratch."""
    print("Clearing existing card cache...")
    db.query(CachedCard).delete()
    db.commit()
    print("Cache cleared")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cache all Limitless TCG cards",
        usage="python cache_cards.py [options]"
    )
    parser.add_argument("--prices", action="store_true", help="Fetch all card price history")
    parser.add_argument("--images", action="store_true", help="Fetch all card images")
    parser.add_argument("--rebuild", action="store_true", help="Clear and rebuild cache")
    parser.add_argument("--limit", type=int, help="Limit to first N cards (for testing)")
    parser.add_argument("--workers", type=int, default=cpu_count(), help=f"Number of worker processes (default: {cpu_count()})")
    parser.add_argument("--rps", type=float, help="Requests per second (default: 2x worker count)")
    args = parser.parse_args()

    # Initialize database
    init_db()
    db = SessionLocal()

    try:
        if args.rebuild:
            rebuild_cache(db)
            stored = set()
            stored_prices = set()
        else:
            # Get already cached card URLs
            stored = {card.card_url for card in db.query(CachedCard.card_url).all()}
            stored_prices = {card.card_url for card in db.query(CardPrices.card_url).all()}
        # Get all card URLs
        if db.query(CardURL).count() == 0:
            print("No card URLs found in the database. Please check the scraping logic.")
            get_all_card_urls(count=args.limit, stored=stored.intersection(stored_prices), db=db)
        print(f"Found {len(stored)} already cached cards.")
        urls = [card.url for card in db.query(CardURL) if (card.url not in stored) or (card.url not in stored_prices and args.prices)]
        if not urls:
            print("No cards found to cache")
            return

        # Cache all cards
        cache_all_cards(urls, None, stored_prices, limit=args.limit, num_workers=args.workers, requests_per_second=args.rps, prices=args.prices, images=args.images)

    finally:
        db.close()


if __name__ == "__main__":
    main()
