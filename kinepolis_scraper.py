"""
Kinepolis Scraper + OMDB Enrichment
====================================
Scrapes movie showtimes from Kinepolis Enschede (WCST),
enriches with IMDB scores/genres (via OMDB API),
and converts to generic MatrixPollData schema.

Requirements:
    pip install playwright requests
"""

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

import requests
from playwright.sync_api import BrowserContext


# ─── Configuration ──────────────────────────────────────────────────────────────

KINEPOLIS_URL = "https://kinepolis.nl/?complex={complex}&main_section=vandaag"
KINEPOLIS_COMPLEX = "WCST"  # Kinepolis Enschede / West

OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")


# ─── Scraping ────────────────────────────────────────────────────────────────────

def scrape_kinepolis(
    context: BrowserContext,
    num_days: int = 7,
    after_time: str | None = None,
    day_filter: str | None = None,  # "weekdays", "weekend", or None
) -> dict[str, dict]:
    """
    Extracts movie + session data from the Drupal.settings JSON blob.

    Returns:
        {
            "Movie Title": {
                "times_by_date": {"Wed 12 Mar": ["14:30", "19:00"], ...},
                "genres": ["Action", "Sci-Fi"],
                "poster": "https://...",
                "imdb_url": "https://...",
            },
        }
    """
    filter_hour, filter_min = 0, 0
    if after_time:
        parts = after_time.split(":")
        filter_hour = int(parts[0])
        filter_min = int(parts[1]) if len(parts) > 1 else 0
        print(f"  Time filter: showtimes after {filter_hour:02d}:{filter_min:02d}")

    if day_filter == "weekdays":
        print("  Day filter: weekdays only (Mon-Fri)")
    elif day_filter == "weekend":
        print("  Day filter: weekend only (Sat-Sun)")

    start_date = datetime.now()
    end_date = start_date + timedelta(days=num_days)

    page = context.new_page()
    url = KINEPOLIS_URL.format(complex=KINEPOLIS_COMPLEX)
    print(f"  Loading {url} ...")
    page.goto(url, wait_until="networkidle", timeout=30000)

    # Accept cookies
    try:
        cookie_btn = page.locator(
            "button:has-text('Accepteren'), "
            "button:has-text('Alles accepteren'), "
            "button:has-text('Accept'), "
            "#onetrust-accept-btn-handler"
        )
        cookie_btn.first.click(timeout=5000)
        page.wait_for_timeout(1000)
    except Exception:
        pass

    page.wait_for_timeout(3000)
    print("  Extracting session data...")
    page_source = page.content()

    if "current_movies" not in page_source:
        print("  WARNING: 'current_movies' not found in page source.")
        with open("kinepolis_debug.html", "w", encoding="utf-8") as f:
            f.write(page_source)
        print("  Dumped to kinepolis_debug.html for inspection.")

    page.close()

    # Build film lookup: id -> name
    film_names = {}
    for m in re.finditer(
        r'"name":"([^"]+)","country":"NL","language":"NL","documentType":"film","id":"(HO\d+)"',
        page_source,
    ):
        raw_name, fid = m.group(1), m.group(2)
        try:
            name = raw_name.replace("\\/", "/")
            name = name.encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
        except Exception:
            name = raw_name.replace("\\/", "/")
        film_names[fid] = name

    # Build genre lookup + rating from Kinepolis data
    film_genres = {}
    film_ratings = {}
    for m in re.finditer(
        r'"genres":\[(.*?)\].*?"documentType":"film","id":"(HO\d+)"',
        page_source,
    ):
        film_genres[m.group(2)] = re.findall(r'"name":"([^"]+)"', m.group(1))

    # Extract imdbCode and poster images from film blocks
    film_imdb_codes = {}
    film_posters = {}
    for m in re.finditer(r'"documentType":"film","id":"(HO\d+)"', page_source):
        fid = m.group(1)
        block_start = max(0, m.start() - 6000)
        block = page_source[block_start:m.end()]

        imdb_match = re.search(r'"imdbCode":"(tt\d+)"', block)
        if imdb_match:
            film_imdb_codes[fid] = imdb_match.group(1)

        poster_match = re.search(
            r'"mediaType":"Poster Graphic","url":"([^"]+)"', block
        )
        if poster_match:
            url = poster_match.group(1).replace("\\/", "/")
            film_posters[fid] = "https://kinepolis.nl" + url

    print(f"  Found Kinepolis IMDB codes for {len(film_imdb_codes)} films")
    print(f"  Found Kinepolis posters for {len(film_posters)} films")

    # Extract sessions for our complex
    movies: dict[str, dict] = {}
    session_count = 0

    for m in re.finditer(
        rf'"complexOperator":"{KINEPOLIS_COMPLEX}"', page_source
    ):
        chunk = page_source[max(0, m.start() - 1): m.start() + 2000]

        st_match = re.search(r'"showtime":"([^"]+)"', chunk)
        if not st_match:
            continue

        try:
            st_naive = datetime.fromisoformat(st_match.group(1).replace("+00:00", ""))
        except Exception:
            continue

        # Date range filter
        if st_naive.date() < start_date.date() or st_naive.date() >= end_date.date():
            continue

        # Day filter (weekday: Mon=0..Fri=4, weekend: Sat=5, Sun=6)
        if day_filter == "weekdays" and st_naive.weekday() >= 5:
            continue
        if day_filter == "weekend" and st_naive.weekday() < 5:
            continue

        # Time filter
        if st_naive.hour < filter_hour or (
            st_naive.hour == filter_hour and st_naive.minute < filter_min
        ):
            continue

        fid_match = re.search(r'"film":\{.*?"id":"(HO\d+)"', chunk)
        if not fid_match:
            continue

        fid = fid_match.group(1)
        title = film_names.get(fid, fid)
        genres = film_genres.get(fid, [])

        date_label = st_naive.strftime("%a %d %b")
        time_label = st_naive.strftime("%H:%M")

        if title not in movies:
            imdb_code = film_imdb_codes.get(fid, "")
            movies[title] = {
                "times_by_date": defaultdict(list),
                "genres": genres,
                "poster": film_posters.get(fid, ""),
                "imdb_url": f"https://www.imdb.com/title/{imdb_code}/" if imdb_code else "",
            }
        movies[title]["times_by_date"][date_label].append(time_label)
        session_count += 1

    for title in movies:
        for dk in movies[title]["times_by_date"]:
            movies[title]["times_by_date"][dk] = sorted(set(movies[title]["times_by_date"][dk]))

    print(f"  Found {len(movies)} movies, {session_count} sessions")
    return movies


# ─── OMDB / IMDB Enrichment ─────────────────────────────────────────────────────

def fetch_imdb_info(title: str) -> dict | None:
    if not OMDB_API_KEY:
        return None
    clean = re.sub(r"^(Cineplus|CinePlus|Klassieker|Kleuterbios|Anime):\s*", "", title)
    clean = re.sub(r"\s*\(.*?\)\s*", " ", clean)
    clean = re.sub(
        r"\b(OV|OmU|3D|IMAX|4DX|Dolby|Atmos|NL|EN|re-release)\b", "",
        clean, flags=re.IGNORECASE,
    )
    clean = clean.strip()
    if not clean:
        return None
    try:
        resp = requests.get(
            "https://www.omdbapi.com/",
            params={"apikey": OMDB_API_KEY, "t": clean, "type": "movie"},
            timeout=5,
        )
        data = resp.json()
        if data.get("Response") == "True":
            poster = data.get("Poster", "N/A")
            if poster == "N/A":
                poster = ""
            imdb_id = data.get("imdbID", "")
            imdb_url = f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else ""
            return {"imdb_rating": data.get("imdbRating", "N/A"), "genre": data.get("Genre", "N/A"), "poster": poster, "imdb_url": imdb_url}
    except Exception:
        pass
    return None


def enrich_movies(movies: dict[str, dict]) -> None:
    for title, data in movies.items():
        info = fetch_imdb_info(title)
        if info and info["imdb_rating"] != "N/A":
            data["imdb_rating"] = info["imdb_rating"]
            data["display_genre"] = info["genre"]
            omdb_url = info.get("imdb_url", "")
            if omdb_url:
                data["imdb_url"] = omdb_url
            omdb_poster = info.get("poster", "")
            if omdb_poster:
                data["poster"] = omdb_poster
        else:
            data["imdb_rating"] = "?"
            data["display_genre"] = ", ".join(data.get("genres", [])) or "?"


# ─── Interactive movie picker ────────────────────────────────────────────────────

def pick_movies(movies: dict[str, dict], sorted_titles: list[str]) -> dict[str, dict]:
    """
    Interactive picker: lets the user choose which movies to include.

    Input formats:
        - Enter / blank  → include ALL movies
        - "1,3,5"        → include only movies 1, 3, 5
        - "1-5"          → include movies 1 through 5
        - "1-3,7,9-11"   → mix of ranges and individual numbers
        - "-2,5"         → exclude movies 2 and 5 (keep the rest)
    """
    print(f"\n{'─' * 60}")
    print("Select movies to include in the poll:")
    print("  Enter    → include ALL")
    print("  1,3,5    → pick specific movies by number")
    print("  1-5,8    → ranges and individual numbers")
    print("  -2,5     → exclude movies 2 and 5 (keep the rest)")
    print(f"{'─' * 60}")

    choice = input("\nYour selection: ").strip()

    if not choice:
        print(f"  Including all {len(movies)} movies.")
        return movies

    # Parse selection
    exclude_mode = choice.startswith("-")
    if exclude_mode:
        choice = choice[1:]

    selected_indices = set()
    for part in choice.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                lo, hi = int(bounds[0]), int(bounds[1])
                selected_indices.update(range(lo, hi + 1))
            except ValueError:
                print(f"  WARNING: Could not parse '{part}', skipping.")
        else:
            try:
                selected_indices.add(int(part))
            except ValueError:
                print(f"  WARNING: Could not parse '{part}', skipping.")

    if exclude_mode:
        kept_titles = [
            t for i, t in enumerate(sorted_titles, 1) if i not in selected_indices
        ]
    else:
        kept_titles = [
            t for i, t in enumerate(sorted_titles, 1) if i in selected_indices
        ]

    filtered = {t: movies[t] for t in kept_titles if t in movies}
    print(f"\n  Selected {len(filtered)} movies:")
    for t in sorted(filtered.keys()):
        print(f"    ✓ {t}")

    return filtered


# ─── Bridge to generic MatrixPollData ────────────────────────────────────────────

def to_matrix_poll_data(
    movies: dict[str, dict],
    poll_title: str,
    storage_prefix: str = "kinepolis",
) -> dict:
    """Convert Kinepolis movies dict -> generic MatrixPollData schema."""
    # Collect and sort all date columns
    all_dates = set()
    for data in movies.values():
        all_dates.update(data["times_by_date"].keys())

    def date_sort_key(d):
        try:
            return datetime.strptime(d, "%a %d %b")
        except Exception:
            return datetime.min

    sorted_dates = sorted(all_dates, key=date_sort_key)

    # Build items
    items = []
    for title in sorted(movies.keys()):
        data = movies[title]
        slots = {}
        for date in sorted_dates:
            times = data["times_by_date"].get(date, [])
            if times:
                slots[date] = times

        items.append({
            "name": title,
            "rating": data.get("imdb_rating", ""),
            "category": data.get("display_genre", ""),
            "image_url": data.get("poster", ""),
            "detail_url": data.get("imdb_url", ""),
            "slots": slots,
        })

    return {
        "title": poll_title,
        "storage_prefix": storage_prefix,
        "row_label": "Movie",
        "columns": sorted_dates,
        "items": items,
    }
