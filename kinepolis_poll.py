"""
Kinepolis Movie Poll Creator
==============================
Scrapes movie showtimes from Kinepolis Enschede (WCST),
enriches with IMDB scores/genres (via OMDB API),
and generates a self-contained HTML voting widget for SharePoint.

The widget uses the SharePoint REST API to store votes in a SharePoint list,
giving you cross-user tallying with zero external infrastructure.

Requirements:
    pip install playwright requests

Optional:
    export OMDB_API_KEY=your_key  (https://www.omdbapi.com/apikey.aspx)

SharePoint setup (one-time):
    1. Go to your SharePoint site → Site contents → New → List
    2. Name it "KinepolisVotes"
    3. Add these columns (all "Single line of text"):
       - MovieTitle
       - ShowDate
       - ShowTime
       - VoterEmail
    4. Add an Embed web part to a SharePoint page
    5. Paste the contents of the generated HTML file

Usage:
    python kinepolis_poll.py                          # next 7 days, all times
    python kinepolis_poll.py --days 3                  # next 3 days
    python kinepolis_poll.py --after 17:00             # only showtimes after 17:00
    python kinepolis_poll.py --weekdays                # Mon-Fri only
    python kinepolis_poll.py --weekend                 # Sat-Sun only
    python kinepolis_poll.py --days 7 --after 18:00 --weekdays
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from string import Template

import requests
from playwright.sync_api import sync_playwright, BrowserContext


# ─── Configuration ──────────────────────────────────────────────────────────────

KINEPOLIS_URL = "https://kinepolis.nl/?complex={complex}&main_section=vandaag"
KINEPOLIS_COMPLEX = "WCST"  # Kinepolis Enschede / West

OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

SUPABASE_URL = "https://opadleobyehxumakwjvh.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9wYWRsZW9ieWVoeHVtYWt3anZoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NzE2MjAsImV4cCI6MjA4OTI0NzYyMH0.GsKMeIYORamBs56Q11cfuM0czWj7GmN7SEL2mCpkKA4"


# ─── 1. Scrape Kinepolis (from Drupal.settings JSON) ────────────────────────────

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

    # Try to extract ratings from the film JSON blocks
    # Dump a sample film block for debugging field names
    sample_block = None
    for m in re.finditer(r'"documentType":"film","id":"(HO\d+)"', page_source):
        block_start = max(0, m.start() - 2000)
        block_end = min(len(page_source), m.end() + 500)
        block = page_source[block_start:block_end]
        fid = m.group(1)
        if sample_block is None:
            sample_block = block
        # Try common rating field names near the film id
        for pattern in [
            r'"imdbRating":"([^"]+)"',
            r'"rating":"([^"]+)"',
            r'"score":"([^"]+)"',
            r'"imdbScore":"([^"]+)"',
            r'"ratingValue":"([^"]+)"',
        ]:
            rm = re.search(pattern, block)
            if rm and fid not in film_ratings:
                try:
                    val = float(rm.group(1))
                    film_ratings[fid] = str(val)
                except ValueError:
                    film_ratings[fid] = rm.group(1)
                break

    if sample_block:
        # Dump one sample film block to help identify field names
        with open("kinepolis_film_sample.txt", "w", encoding="utf-8") as f:
            f.write(sample_block)
        print(f"  Dumped sample film block to kinepolis_film_sample.txt")
        print(f"  Found Kinepolis ratings for {len(film_ratings)} films")

    # Build poster lookup from Kinepolis page (poster/still images)
    film_posters = {}
    for m in re.finditer(
        r'"posterImageUrl":"([^"]+)"[^}]*?"documentType":"film","id":"(HO\d+)"',
        page_source,
    ):
        url = m.group(1).replace("\\/", "/")
        film_posters[m.group(2)] = url
    # Fallback: try "imageUrl" field
    if not film_posters:
        for m in re.finditer(
            r'"imageUrl":"([^"]+)"[^}]*?"documentType":"film","id":"(HO\d+)"',
            page_source,
        ):
            url = m.group(1).replace("\\/", "/")
            if m.group(2) not in film_posters:
                film_posters[m.group(2)] = url

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
            movies[title] = {
                "times_by_date": defaultdict(list),
                "genres": genres,
                "poster": film_posters.get(fid, ""),
                "kinepolis_rating": film_ratings.get(fid, ""),
            }
        movies[title]["times_by_date"][date_label].append(time_label)
        session_count += 1

    for title in movies:
        for dk in movies[title]["times_by_date"]:
            movies[title]["times_by_date"][dk] = sorted(set(movies[title]["times_by_date"][dk]))

    print(f"  Found {len(movies)} movies, {session_count} sessions")
    return movies


# ─── 2. OMDB / IMDB Enrichment ──────────────────────────────────────────────────

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
            data["imdb_url"] = info.get("imdb_url", "")
            # OMDB poster takes priority, fall back to Kinepolis poster
            omdb_poster = info.get("poster", "")
            if omdb_poster:
                data["poster"] = omdb_poster
        else:
            # Use Kinepolis rating as fallback
            kr = data.get("kinepolis_rating", "")
            data["imdb_rating"] = kr if kr else "?"
            data["display_genre"] = ", ".join(data.get("genres", [])) or "?"
            data.setdefault("imdb_url", "")


# ─── 3. Build matrix data structure ─────────────────────────────────────────────

def build_matrix_data(movies: dict[str, dict]) -> tuple[list[str], list[dict]]:
    """
    Builds a matrix: rows = movies, columns = dates, cells = list of times.

    Returns:
        (
            ["Thu 13 Mar", "Fri 14 Mar", ...],          # sorted date columns
            [
                {
                    "title": "Avatar: Fire and Ash",
                    "imdb": "7.2",
                    "genre": "Action, Sci-Fi",
                    "cells": {
                        "Thu 13 Mar": ["14:30", "18:45"],
                        "Fri 14 Mar": ["20:00"],
                    }
                },
                ...
            ]
        )
    """
    all_dates = set()
    for data in movies.values():
        all_dates.update(data["times_by_date"].keys())

    def date_sort_key(d):
        try:
            return datetime.strptime(d, "%a %d %b")
        except Exception:
            return datetime.min

    sorted_dates = sorted(all_dates, key=date_sort_key)

    matrix = []
    for title in sorted(movies.keys()):
        data = movies[title]
        row = {
            "title": title,
            "imdb": data.get("imdb_rating", "?"),
            "genre": data.get("display_genre", "?"),
            "poster": data.get("poster", ""),
            "imdb_url": data.get("imdb_url", ""),
            "cells": {},
        }
        for date in sorted_dates:
            times = data["times_by_date"].get(date, [])
            if times:
                row["cells"][date] = times
        matrix.append(row)

    return sorted_dates, matrix


# ─── 4. Generate HTML voting widget (Supabase backend) ───────────────────────

def generate_html(
    dates: list[str],
    matrix: list[dict],
    supabase_url: str,
    supabase_key: str,
    num_days: int,
) -> str:
    """Generate a self-contained HTML voting page using Supabase."""

    week_start = datetime.now().strftime("%d %b")
    week_end = (datetime.now() + timedelta(days=num_days - 1)).strftime("%d %b %Y")
    form_title = f"Kinepolis Movie Night &mdash; {week_start} to {week_end}"

    matrix_json = json.dumps({"dates": dates, "movies": matrix}, ensure_ascii=False)

    tmpl = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Kinepolis Movie Poll</title>
<style>
  :root {
    --bg: #1a1a2e;
    --surface: #16213e;
    --card: #0f3460;
    --accent: #e94560;
    --accent-hover: #ff6b81;
    --text: #eee;
    --text-dim: #aaa;
    --success: #2ecc71;
    --border: #2a2a4a;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 20px;
    min-height: 100vh;
  }

  h1 {
    text-align: center;
    font-size: 1.6rem;
    margin-bottom: 6px;
    color: var(--accent);
  }

  .subtitle {
    text-align: center;
    color: var(--text-dim);
    font-size: 0.9rem;
    margin-bottom: 20px;
  }

  .user-bar {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 12px;
    margin-bottom: 20px;
    padding: 12px;
    background: var(--surface);
    border-radius: 8px;
    font-size: 0.95rem;
    flex-wrap: wrap;
  }

  .user-bar label { color: var(--text-dim); }

  .user-bar input {
    padding: 6px 12px;
    border-radius: 6px;
    border: 2px solid var(--border);
    background: var(--bg);
    color: var(--accent);
    font-size: 0.95rem;
    font-weight: 600;
    width: 200px;
    outline: none;
  }

  .user-bar input:focus { border-color: var(--accent); }

  .user-bar button {
    padding: 6px 16px;
    border-radius: 6px;
    border: none;
    background: var(--accent);
    color: #fff;
    font-weight: 600;
    cursor: pointer;
  }

  .user-bar button:hover { background: var(--accent-hover); }

  .legend {
    display: flex;
    justify-content: center;
    gap: 20px;
    margin-bottom: 16px;
    font-size: 0.85rem;
  }

  .legend span {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .legend .swatch {
    width: 14px;
    height: 14px;
    border-radius: 3px;
    display: inline-block;
  }

  .table-wrap {
    overflow-x: auto;
    border-radius: 10px;
    border: 1px solid var(--border);
  }

  table {
    border-collapse: collapse;
    width: 100%;
    min-width: 600px;
  }

  th, td {
    padding: 10px 12px;
    border: 1px solid var(--border);
    text-align: center;
    vertical-align: top;
  }

  thead th {
    background: var(--card);
    color: var(--accent);
    font-size: 0.95rem;
    position: sticky;
    top: 0;
    z-index: 2;
  }

  .movie-cell {
    text-align: left;
    min-width: 220px;
    background: var(--surface);
  }

  .movie-cell-inner {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .movie-poster {
    width: 45px;
    height: 65px;
    object-fit: cover;
    border-radius: 4px;
    flex-shrink: 0;
  }

  .movie-link {
    color: inherit;
    text-decoration: none;
  }

  .movie-link:hover {
    color: var(--accent);
    text-decoration: underline;
  }

  .movie-title {
    font-weight: 600;
    font-size: 0.95rem;
  }

  .movie-meta {
    font-size: 0.75rem;
    color: var(--text-dim);
    margin-top: 2px;
  }

  .time-buttons {
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: center;
  }

  .time-btn {
    position: relative;
    border: 2px solid var(--border);
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 0.85rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    background: var(--surface);
    color: var(--text);
    min-width: 90px;
  }

  .time-btn:hover {
    border-color: var(--accent);
    transform: scale(1.05);
  }

  .time-btn.voted {
    background: var(--success);
    border-color: var(--success);
    color: #fff;
  }

  .time-btn .vote-count {
    display: inline-block;
    background: rgba(255,255,255,0.2);
    border-radius: 10px;
    padding: 1px 7px;
    font-size: 0.7rem;
    margin-left: 6px;
  }

  .time-btn .voters-tooltip {
    display: none;
    position: absolute;
    bottom: 105%;
    left: 50%;
    transform: translateX(-50%);
    background: #222;
    color: #fff;
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 0.75rem;
    white-space: nowrap;
    z-index: 10;
    box-shadow: 0 2px 8px rgba(0,0,0,0.4);
  }

  .time-btn:hover .voters-tooltip {
    display: block;
  }

  .empty-cell {
    color: var(--border);
    font-size: 0.8rem;
  }

  #loading {
    text-align: center;
    padding: 40px;
    font-size: 1.1rem;
    color: var(--text-dim);
  }

  #error-bar {
    display: none;
    background: var(--accent);
    color: #fff;
    padding: 10px 16px;
    border-radius: 8px;
    margin-bottom: 16px;
    text-align: center;
  }

  .summary {
    margin-top: 20px;
    padding: 16px;
    background: var(--surface);
    border-radius: 8px;
  }

  .summary h3 {
    margin-bottom: 10px;
    color: var(--accent);
  }

  .summary-item {
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 0.9rem;
    border-bottom: 1px solid var(--border);
  }

  .summary-item:last-child {
    border-bottom: none;
  }

  .user-bar select {
    padding: 6px 10px;
    border-radius: 6px;
    border: 2px solid var(--border);
    background: var(--bg);
    color: var(--accent);
    font-size: 0.9rem;
    font-weight: 600;
    outline: none;
  }

  .user-bar select:focus { border-color: var(--accent); }

  .admin-table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
  }

  .admin-table th, .admin-table td {
    padding: 6px 10px;
    border: 1px solid var(--border);
    text-align: left;
    font-size: 0.85rem;
  }

  .admin-table th {
    background: var(--card);
    color: var(--accent);
  }

  .admin-table tr:nth-child(even) {
    background: rgba(255,255,255,0.03);
  }

  /* Dashboard */
  .dashboard {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 20px;
  }

  .dashboard-card {
    background: var(--surface);
    border-radius: 10px;
    padding: 16px;
    border: 1px solid var(--border);
  }

  .dashboard-card h3 {
    color: var(--accent);
    font-size: 0.95rem;
    margin-bottom: 12px;
  }

  .top-pick-card {
    grid-column: 1 / -1;
    text-align: center;
    background: linear-gradient(135deg, var(--card), var(--surface));
    border: 2px solid var(--accent);
  }

  .top-pick-card .pick-movie {
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--accent);
    margin-bottom: 4px;
  }

  .top-pick-card .pick-detail {
    font-size: 1rem;
    color: var(--text-dim);
  }

  .top-pick-card .pick-count {
    font-size: 0.9rem;
    color: var(--success);
    margin-top: 4px;
  }

  .chart-bar-row {
    display: flex;
    align-items: center;
    margin-bottom: 6px;
    font-size: 0.8rem;
  }

  .chart-bar-label {
    width: 120px;
    text-align: right;
    padding-right: 8px;
    color: var(--text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .chart-bar-track {
    flex: 1;
    height: 22px;
    background: var(--bg);
    border-radius: 4px;
    overflow: hidden;
    position: relative;
  }

  .chart-bar-fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.3s;
  }

  .chart-bar-value {
    position: absolute;
    right: 6px;
    top: 2px;
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text);
  }

  .heatmap-wrap {
    overflow-x: auto;
  }

  .heatmap-table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.75rem;
  }

  .heatmap-table th, .heatmap-table td {
    padding: 4px 6px;
    border: 1px solid var(--border);
    text-align: center;
    white-space: nowrap;
  }

  .heatmap-table th {
    background: var(--card);
    color: var(--text-dim);
    font-weight: 600;
  }

  .heatmap-table .hm-label {
    text-align: right;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-dim);
  }

  /* Top Picks details */
  .summary-item details summary {
    cursor: pointer;
    list-style: none;
    display: flex;
    justify-content: space-between;
    width: 100%;
  }

  .summary-item details summary::-webkit-details-marker { display: none; }

  .summary-item details summary::after {
    content: '\\25B6';
    font-size: 0.7rem;
    color: var(--text-dim);
    margin-left: 8px;
    transition: transform 0.2s;
  }

  .summary-item details[open] summary::after {
    transform: rotate(90deg);
  }

  .voter-detail-list {
    padding: 6px 12px;
    font-size: 0.8rem;
    color: var(--text-dim);
  }

  .voter-detail-list div {
    padding: 2px 0;
  }

  /* Admin grouped */
  .admin-group {
    margin-bottom: 12px;
  }

  .admin-group details summary {
    cursor: pointer;
    padding: 8px 12px;
    background: var(--card);
    border-radius: 6px;
    font-weight: 600;
    color: var(--accent);
    list-style: none;
  }

  .admin-group details summary::-webkit-details-marker { display: none; }

  .admin-group details summary::before {
    content: '\\25B6 ';
    font-size: 0.7rem;
    transition: transform 0.2s;
    display: inline-block;
    margin-right: 6px;
  }

  .admin-group details[open] summary::before {
    transform: rotate(90deg);
  }

  .admin-group .admin-table {
    margin-top: 6px;
    margin-left: 12px;
  }

  @media (max-width: 768px) {
    body { padding: 10px; }
    th, td { padding: 6px 8px; }
    .time-btn { padding: 4px 10px; min-width: 70px; }
    .dashboard { grid-template-columns: 1fr; }
    .chart-bar-label { width: 80px; }
  }
</style>
</head>
<body>

<h1>$form_title</h1>
<p class="subtitle">Click a showtime to vote. Click again to remove your vote. Multiple votes allowed.</p>

<div id="error-bar"></div>

<div class="user-bar" id="name-bar" style="flex-direction:column;gap:10px;align-items:stretch;max-width:500px;margin:0 auto 20px">
  <div style="display:flex;gap:10px;align-items:center;justify-content:center">
    <label for="voter-name">Your name:</label>
    <input type="text" id="voter-name" placeholder="e.g. Uthvag" />
  </div>
  <div style="display:flex;gap:10px;align-items:center;justify-content:center">
    <label for="is-pvx">PVX member?</label>
    <select id="is-pvx">
      <option value="Yes">Yes</option>
      <option value="No">No</option>
    </select>
  </div>
  <div style="display:flex;gap:10px;align-items:center;justify-content:center">
    <label for="num-partners">Partners/kids:</label>
    <select id="num-partners">
      <option value="0">0</option>
      <option value="1">1</option>
      <option value="2">2</option>
      <option value="3">3</option>
      <option value="4">4</option>
      <option value="5">5</option>
    </select>
    <label for="num-others">Others:</label>
    <select id="num-others">
      <option value="0">0</option>
      <option value="1">1</option>
      <option value="2">2</option>
      <option value="3">3</option>
      <option value="4">4</option>
      <option value="5">5</option>
    </select>
  </div>
  <button onclick="saveName()" style="align-self:center">Start voting</button>
</div>

<div class="user-bar" id="user-bar" style="display:none">
  Voting as: <strong id="current-user"></strong> &mdash; <span id="current-info"></span>
  <button onclick="changeName()" style="background:var(--border);font-size:0.8rem;padding:4px 10px">Change</button>
  <button onclick="toggleAdmin()" style="background:var(--card);font-size:0.8rem;padding:4px 10px">Show all votes</button>
</div>

<div class="legend">
  <span><span class="swatch" style="background:var(--success)"></span> You voted</span>
  <span><span class="swatch" style="background:var(--surface);border:2px solid var(--border)"></span> Not voted</span>
</div>

<div id="top-pick-section" style="display:none"></div>

<div id="loading">Loading votes...</div>
<div class="table-wrap" id="table-wrap" style="display:none"></div>

<div id="charts-section" class="dashboard" style="display:none"></div>

<div class="summary" id="summary" style="display:none">
  <h3>Top Picks (most votes)</h3>
  <div id="summary-list"></div>
</div>

<div class="summary" id="admin-panel" style="display:none">
  <h3>All Votes</h3>
  <div id="admin-table"></div>
</div>

<script>
// ─── Configuration ─────────────────────────────────────────────────────────
const SUPABASE_URL = $supabase_url_json;
const SUPABASE_KEY = $supabase_key_json;
const MATRIX_DATA = $matrix_json;
const HEADERS = {
  'apikey': SUPABASE_KEY,
  'Authorization': 'Bearer ' + SUPABASE_KEY,
  'Content-Type': 'application/json',
  'Prefer': 'return=representation',
};

let voterName = localStorage.getItem('kinepolis_voter') || '';
let isPVX = localStorage.getItem('kinepolis_pvx') || 'Yes';
let numPartners = parseInt(localStorage.getItem('kinepolis_partners') || '0', 10);
let numOthers = parseInt(localStorage.getItem('kinepolis_others') || '0', 10);
let allVotes = [];

function getTotalPeople(v) {
  return 1 + (v.Partners || 0) + (v.Others || 0);
}

// ─── Name handling ─────────────────────────────────────────────────────────

function saveName() {
  const input = document.getElementById('voter-name');
  const name = input.value.trim();
  if (!name) { input.focus(); return; }
  voterName = name;
  isPVX = document.getElementById('is-pvx').value;
  numPartners = parseInt(document.getElementById('num-partners').value, 10);
  numOthers = parseInt(document.getElementById('num-others').value, 10);
  localStorage.setItem('kinepolis_voter', name);
  localStorage.setItem('kinepolis_pvx', isPVX);
  localStorage.setItem('kinepolis_partners', numPartners);
  localStorage.setItem('kinepolis_others', numOthers);
  document.getElementById('name-bar').style.display = 'none';
  document.getElementById('user-bar').style.display = 'flex';
  document.getElementById('current-user').textContent = name;
  const total = 1 + numPartners + numOthers;
  document.getElementById('current-info').textContent =
    (isPVX === 'Yes' ? 'PVX' : 'non-PVX') + ', ' + total + ' total';
  renderTable();
}

function changeName() {
  document.getElementById('name-bar').style.display = 'flex';
  document.getElementById('user-bar').style.display = 'none';
  document.getElementById('voter-name').value = voterName;
  document.getElementById('is-pvx').value = isPVX;
  document.getElementById('num-partners').value = numPartners;
  document.getElementById('num-others').value = numOthers;
  document.getElementById('voter-name').focus();
}

function toggleAdmin() {
  const panel = document.getElementById('admin-panel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    renderAdmin();
  } else {
    panel.style.display = 'none';
  }
}

// ─── Supabase API helpers ──────────────────────────────────────────────────

async function fetchAllVotes() {
  const resp = await fetch(
    SUPABASE_URL + '/rest/v1/votes?select=id,MovieTitle,ShowDate,ShowTime,VoterName,isPvx,Partners,Others&limit=5000',
    { headers: HEADERS }
  );
  if (!resp.ok) throw new Error('Failed to fetch votes: ' + resp.status);
  return await resp.json();
}

async function addVote(movie, date, time) {
  const resp = await fetch(
    SUPABASE_URL + '/rest/v1/votes',
    {
      method: 'POST',
      headers: HEADERS,
      body: JSON.stringify({
        MovieTitle: movie,
        ShowDate: date,
        ShowTime: time,
        VoterName: voterName,
        isPvx: isPVX,
        Partners: numPartners,
        Others: numOthers,
      }),
    }
  );
  if (!resp.ok) throw new Error('Failed to add vote: ' + resp.status);
  const data = await resp.json();
  return data[0];
}

async function deleteVote(itemId) {
  const resp = await fetch(
    SUPABASE_URL + '/rest/v1/votes?id=eq.' + itemId,
    { method: 'DELETE', headers: HEADERS }
  );
  if (!resp.ok) throw new Error('Failed to delete vote: ' + resp.status);
}

// ─── Rendering ─────────────────────────────────────────────────────────────

function getVotersForSlot(movie, date, time) {
  return allVotes.filter(
    v => v.MovieTitle === movie && v.ShowDate === date && v.ShowTime === time
  );
}

function getMyVoteForSlot(movie, date, time) {
  return allVotes.find(
    v => v.MovieTitle === movie && v.ShowDate === date && v.ShowTime === time
      && v.VoterName === voterName
  );
}

function renderDashboard() {
  const topPickEl = document.getElementById('top-pick-section');
  const chartsEl = document.getElementById('charts-section');

  if (allVotes.length === 0) {
    topPickEl.style.display = 'none';
    chartsEl.style.display = 'none';
    return;
  }

  // Aggregate data
  const bySlot = {};   // "Movie — Date Time" => total people
  const byMovie = {};  // movie => total people
  const byDate = {};   // date => total people
  const byMovieDate = {}; // "movie|date" => total people

  for (const v of allVotes) {
    const p = getTotalPeople(v);
    const slotKey = v.MovieTitle + ' \u2014 ' + v.ShowDate + ' ' + v.ShowTime;
    bySlot[slotKey] = (bySlot[slotKey] || 0) + p;
    byMovie[v.MovieTitle] = (byMovie[v.MovieTitle] || 0) + p;
    byDate[v.ShowDate] = (byDate[v.ShowDate] || 0) + p;
    const mdKey = v.MovieTitle + '|' + v.ShowDate;
    byMovieDate[mdKey] = (byMovieDate[mdKey] || 0) + p;
  }

  // Top Pick (above the table)
  const topSlot = Object.entries(bySlot).sort((a, b) => b[1] - a[1])[0];
  let topHtml = '<div class="dashboard-card top-pick-card"><h3>Current Top Pick</h3>';
  if (topSlot) {
    topHtml += '<div class="pick-movie">' + escHtml(topSlot[0]) + '</div>';
    topHtml += '<div class="pick-count">' + topSlot[1] + ' people interested</div>';
  }
  topHtml += '</div>';
  topPickEl.innerHTML = topHtml;
  topPickEl.style.display = 'block';

  // Charts (below the table)
  function barChart(title, data, color) {
    const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
    const max = sorted.length > 0 ? sorted[0][1] : 1;
    let h = '<div class="dashboard-card"><h3>' + title + '</h3>';
    for (const [label, val] of sorted.slice(0, 8)) {
      const pct = (val / max * 100).toFixed(0);
      h += '<div class="chart-bar-row">'
        + '<div class="chart-bar-label" title="' + escAttr(label) + '">' + escHtml(label) + '</div>'
        + '<div class="chart-bar-track"><div class="chart-bar-fill" style="width:' + pct + '%;background:' + color + '"></div>'
        + '<div class="chart-bar-value">' + val + '</div></div></div>';
    }
    h += '</div>';
    return h;
  }

  let chartsHtml = '';
  chartsHtml += barChart('Votes by Movie', byMovie, 'var(--accent)');
  chartsHtml += barChart('Votes by Day', byDate, 'var(--success)');

  // Combined heatmap
  const movieNames = Object.keys(byMovie).sort((a, b) => (byMovie[b] || 0) - (byMovie[a] || 0));
  const dateNames = MATRIX_DATA.dates;
  let maxHm = 0;
  for (const v of Object.values(byMovieDate)) { if (v > maxHm) maxHm = v; }

  chartsHtml += '<div class="dashboard-card" style="grid-column:1/-1"><h3>Movie &times; Day Heatmap</h3><div class="heatmap-wrap">';
  chartsHtml += '<table class="heatmap-table"><thead><tr><th></th>';
  for (const d of dateNames) { chartsHtml += '<th>' + escHtml(d) + '</th>'; }
  chartsHtml += '</tr></thead><tbody>';
  for (const m of movieNames) {
    chartsHtml += '<tr><td class="hm-label" title="' + escAttr(m) + '">' + escHtml(m) + '</td>';
    for (const d of dateNames) {
      const val = byMovieDate[m + '|' + d] || 0;
      const intensity = maxHm > 0 ? val / maxHm : 0;
      const bg = val > 0
        ? 'background:rgba(233,69,96,' + (0.15 + intensity * 0.7).toFixed(2) + ')'
        : '';
      chartsHtml += '<td style="' + bg + '">' + (val > 0 ? val : '') + '</td>';
    }
    chartsHtml += '</tr>';
  }
  chartsHtml += '</tbody></table></div></div>';

  chartsEl.innerHTML = chartsHtml;
  chartsEl.style.display = 'grid';
}

function renderTable() {
  const dates = MATRIX_DATA.dates;
  const movies = MATRIX_DATA.movies;

  // Pre-compute cell vote counts for heatmap shading
  let maxCellVotes = 0;
  const cellVoteCounts = {};
  for (const movie of movies) {
    for (const date of dates) {
      const times = movie.cells[date] || [];
      let cellTotal = 0;
      for (const t of times) {
        cellTotal += getVotersForSlot(movie.title, date, t).reduce((s, v) => s + getTotalPeople(v), 0);
      }
      const key = movie.title + '|' + date;
      cellVoteCounts[key] = cellTotal;
      if (cellTotal > maxCellVotes) maxCellVotes = cellTotal;
    }
  }

  let html = '<table><thead><tr><th>Movie</th>';
  for (const d of dates) {
    html += '<th>' + d + '</th>';
  }
  html += '</tr></thead><tbody>';

  for (const movie of movies) {
    html += '<tr>';

    const rating = movie.imdb !== '?' ? ' &#11088; ' + movie.imdb + '/10' : '';
    const genre = movie.genre !== '?' ? movie.genre : '';
    const hasLink = movie.imdb_url && movie.imdb_url.length > 0;
    const linkOpen = hasLink ? '<a href="' + escAttr(movie.imdb_url) + '" target="_blank" rel="noopener" class="movie-link">' : '';
    const linkClose = hasLink ? '</a>' : '';
    const posterImg = movie.poster
      ? linkOpen + '<img src="' + escAttr(movie.poster) + '" alt="" class="movie-poster" />' + linkClose
      : '';
    const titleHtml = linkOpen + escHtml(movie.title) + linkClose;
    html += '<td class="movie-cell">'
          + '<div class="movie-cell-inner">'
          + posterImg
          + '<div>'
          + '<div class="movie-title">' + titleHtml + '</div>'
          + '<div class="movie-meta">' + escHtml(genre) + rating + '</div>'
          + '</div></div>'
          + '</td>';

    for (const date of dates) {
      const times = movie.cells[date] || [];
      if (times.length === 0) {
        html += '<td><span class="empty-cell">&mdash;</span></td>';
        continue;
      }

      // Heatmap shading
      const cellKey = movie.title + '|' + date;
      const cellCount = cellVoteCounts[cellKey] || 0;
      const intensity = maxCellVotes > 0 ? cellCount / maxCellVotes : 0;
      const bgStyle = intensity > 0
        ? ' style="background:rgba(233,69,96,' + (0.08 + intensity * 0.35).toFixed(2) + ')"'
        : '';

      html += '<td' + bgStyle + '><div class="time-buttons">';
      for (const t of times) {
        const voters = getVotersForSlot(movie.title, date, t);
        const myVote = getMyVoteForSlot(movie.title, date, t);
        const votedClass = myVote ? ' voted' : '';
        const totalPeople = voters.reduce((sum, v) => sum + getTotalPeople(v), 0);
        const countBadge = voters.length > 0
          ? '<span class="vote-count">' + totalPeople + '&#128100;</span>'
          : '';

        let tooltip = '';
        if (voters.length > 0) {
          const names = voters.map(v => {
            const extra = (v.Partners || 0) + (v.Others || 0);
            return v.VoterName + (v.isPvx === 'Yes' ? '*' : '') + (extra > 0 ? ' (+' + extra + ')' : '');
          }).join(', ');
          tooltip = '<span class="voters-tooltip">' + escHtml(names) + ' (* = PVX)</span>';
        }

        html += '<button class="time-btn' + votedClass + '" '
              + 'data-movie="' + escAttr(movie.title) + '" '
              + 'data-date="' + escAttr(date) + '" '
              + 'data-time="' + escAttr(t) + '" '
              + 'onclick="toggleVote(this)">'
              + t + countBadge + tooltip
              + '</button>';
      }
      html += '</div></td>';
    }
    html += '</tr>';
  }

  html += '</tbody></table>';
  document.getElementById('table-wrap').innerHTML = html;
  renderDashboard();
  renderSummary();
}

function renderSummary() {
  const tally = {};
  const peopleTally = {};
  const pvxTally = {};
  const votersByKey = {};
  for (const v of allVotes) {
    const key = v.MovieTitle + ' \u2014 ' + v.ShowDate + ' ' + v.ShowTime;
    tally[key] = (tally[key] || 0) + 1;
    peopleTally[key] = (peopleTally[key] || 0) + getTotalPeople(v);
    if (v.isPvx === 'Yes') pvxTally[key] = (pvxTally[key] || 0) + 1;
    if (!votersByKey[key]) votersByKey[key] = [];
    votersByKey[key].push(v);
  }

  const sorted = Object.entries(peopleTally).sort((a, b) => b[1] - a[1]);
  if (sorted.length === 0) {
    document.getElementById('summary').style.display = 'none';
    return;
  }

  document.getElementById('summary').style.display = 'block';
  let html = '';
  for (const [label, people] of sorted.slice(0, 10)) {
    const votes = tally[label];
    const pvx = pvxTally[label] || 0;
    const voters = votersByKey[label] || [];

    let voterListHtml = '<div class="voter-detail-list">';
    for (const v of voters) {
      const extras = [];
      if (v.isPvx === 'Yes') extras.push('PVX');
      if (v.Partners > 0) extras.push(v.Partners + ' partner/kids');
      if (v.Others > 0) extras.push(v.Others + ' others');
      const extraStr = extras.length > 0 ? ' (' + extras.join(', ') + ')' : '';
      voterListHtml += '<div>' + escHtml(v.VoterName) + extraStr + ' \u2014 ' + getTotalPeople(v) + ' total</div>';
    }
    voterListHtml += '</div>';

    html += '<div class="summary-item"><details><summary><span>' + escHtml(label) + '</span><span><strong>'
          + people + '</strong> people, ' + pvx + ' PVX (' + votes + ' vote' + (votes !== 1 ? 's' : '') + ')</span></summary>'
          + voterListHtml + '</details></div>';
  }
  document.getElementById('summary-list').innerHTML = html;
}

function renderAdmin() {
  // Group by voter name
  const groups = {};
  for (const v of allVotes) {
    if (!groups[v.VoterName]) groups[v.VoterName] = [];
    groups[v.VoterName].push(v);
  }

  const sortedNames = Object.keys(groups).sort((a, b) => a.localeCompare(b));
  if (sortedNames.length === 0) {
    document.getElementById('admin-table').innerHTML = '<p style="color:var(--text-dim)">No votes yet.</p>';
    return;
  }

  let html = '';
  for (const name of sortedNames) {
    const votes = groups[name];
    const sample = votes[0];
    const pvxLabel = sample.isPvx === 'Yes' ? 'PVX' : 'non-PVX';
    const totalPeople = getTotalPeople(sample);

    html += '<div class="admin-group"><details><summary>'
      + escHtml(name) + ' &mdash; ' + pvxLabel
      + ', ' + (sample.Partners || 0) + ' partners/kids, ' + (sample.Others || 0) + ' others'
      + ' (' + votes.length + ' vote' + (votes.length !== 1 ? 's' : '') + ')'
      + '</summary>';

    html += '<table class="admin-table"><thead><tr>'
      + '<th>Movie</th><th>Date</th><th>Time</th>'
      + '</tr></thead><tbody>';

    for (const v of votes) {
      html += '<tr>'
        + '<td>' + escHtml(v.MovieTitle) + '</td>'
        + '<td>' + escHtml(v.ShowDate) + '</td>'
        + '<td>' + escHtml(v.ShowTime) + '</td>'
        + '</tr>';
    }

    html += '</tbody></table></details></div>';
  }

  document.getElementById('admin-table').innerHTML = html;
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function escAttr(s) {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;')
          .replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ─── Vote toggle ───────────────────────────────────────────────────────────

async function toggleVote(btn) {
  if (!voterName) { changeName(); return; }

  const movie = btn.dataset.movie;
  const date = btn.dataset.date;
  const time = btn.dataset.time;

  btn.disabled = true;
  btn.style.opacity = '0.5';

  try {
    const existing = getMyVoteForSlot(movie, date, time);
    if (existing) {
      await deleteVote(existing.id);
      allVotes = allVotes.filter(v => v.id !== existing.id);
    } else {
      const newItem = await addVote(movie, date, time);
      allVotes.push({
        id: newItem.id,
        MovieTitle: movie,
        ShowDate: date,
        ShowTime: time,
        VoterName: voterName,
        isPvx: isPVX,
        Partners: numPartners,
        Others: numOthers,
      });
    }
    renderTable();
  } catch (err) {
    showError('Vote failed: ' + err.message);
  }

  btn.disabled = false;
  btn.style.opacity = '1';
}

function showError(msg) {
  const bar = document.getElementById('error-bar');
  bar.textContent = msg;
  bar.style.display = 'block';
  setTimeout(() => { bar.style.display = 'none'; }, 5000);
}

// ─── Init ──────────────────────────────────────────────────────────────────

async function init() {
  // Restore saved name & party size
  if (voterName) {
    document.getElementById('name-bar').style.display = 'none';
    document.getElementById('user-bar').style.display = 'flex';
    document.getElementById('current-user').textContent = voterName;
    const total = 1 + numPartners + numOthers;
    document.getElementById('current-info').textContent =
      (isPVX === 'Yes' ? 'PVX' : 'non-PVX') + ', ' + total + ' total';
  }

  try {
    allVotes = await fetchAllVotes();
    document.getElementById('loading').style.display = 'none';
    document.getElementById('table-wrap').style.display = 'block';
    renderTable();
  } catch (err) {
    document.getElementById('loading').textContent =
      'Failed to load votes. Check that Supabase is configured correctly.';
    showError(err.message);
    console.error(err);
  }
}

init();
</script>
</body>
</html>""")

    return tmpl.safe_substitute(
        form_title=form_title,
        supabase_url_json=json.dumps(supabase_url),
        supabase_key_json=json.dumps(supabase_key),
        matrix_json=matrix_json,
    )


# ─── 5. Interactive movie picker ─────────────────────────────────────────────

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
        # Keep everything except the listed numbers
        kept_titles = [
            t for i, t in enumerate(sorted_titles, 1) if i not in selected_indices
        ]
    else:
        # Keep only the listed numbers
        kept_titles = [
            t for i, t in enumerate(sorted_titles, 1) if i in selected_indices
        ]

    filtered = {t: movies[t] for t in kept_titles if t in movies}
    print(f"\n  Selected {len(filtered)} movies:")
    for t in sorted(filtered.keys()):
        print(f"    ✓ {t}")

    return filtered


# ─── 6. Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scrape Kinepolis showtimes and create a voting page with Supabase backend."
    )
    parser.add_argument("--days", type=int, default=7, help="Days to look ahead (default: 7)")
    parser.add_argument("--after", type=str, default=None, help="Only showtimes after HH:MM")
    parser.add_argument("--weekdays", action="store_true", help="Mon-Fri only")
    parser.add_argument("--weekend", action="store_true", help="Sat-Sun only")
    parser.add_argument(
        "--supabase-url", type=str, default=SUPABASE_URL,
        help=f"Supabase project URL (default: {SUPABASE_URL})"
    )
    parser.add_argument(
        "--supabase-key", type=str, default=SUPABASE_ANON_KEY,
        help="Supabase anon key"
    )
    parser.add_argument(
        "--output", type=str, default="index.html",
        help="Output HTML file (default: index.html)"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear all votes from Supabase before generating"
    )
    parser.add_argument(
        "--no-pick", action="store_true",
        help="Skip interactive movie picker (include all movies)"
    )
    args = parser.parse_args()

    if args.after:
        if not re.match(r"^\d{1,2}(:\d{2})?$", args.after):
            print(f"Error: --after must be HH:MM, got '{args.after}'")
            sys.exit(1)
        if ":" not in args.after:
            args.after += ":00"

    day_filter = None
    if args.weekdays:
        day_filter = "weekdays"
    elif args.weekend:
        day_filter = "weekend"

    if args.reset:
        print("Clearing all votes from Supabase...")
        resp = requests.delete(
            f"{args.supabase_url}/rest/v1/votes?id=gt.0",
            headers={
                "apikey": args.supabase_key,
                "Authorization": f"Bearer {args.supabase_key}",
            },
        )
        if resp.ok:
            print("  Votes cleared!")
        else:
            print(f"  WARNING: Failed to clear votes ({resp.status_code}): {resp.text}")

    with sync_playwright() as pw:
        print("=" * 60)
        print(f"Scraping Kinepolis Enschede ({args.days} days)...")
        if args.after:
            print(f"Filter: showtimes after {args.after}")
        print("=" * 60)

        user_data_dir = os.path.join(os.path.expanduser("~"), ".kinepolis_poll_browser")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            channel="msedge",
            locale="en-GB",
        )
        movies = scrape_kinepolis(ctx, args.days, args.after, day_filter)
        ctx.close()

        if not movies:
            print("\nNo movies found!")
            sys.exit(1)

        if OMDB_API_KEY:
            print(f"\nFetching IMDB scores for {len(movies)} movies...")
        else:
            print("\nNo OMDB_API_KEY — using Kinepolis genres.")
        enrich_movies(movies)

        total = sum(len(t) for d in movies.values() for t in d["times_by_date"].values())
        print(f"\n{len(movies)} movies, {total} showtimes:\n")
        sorted_titles = sorted(movies.keys())
        for i, title in enumerate(sorted_titles, 1):
            data = movies[title]
            r, g = data.get("imdb_rating", "?"), data.get("display_genre", "?")
            tag = f" [{r}/10 — {g}]" if r != "?" else f" [{g}]" if g != "?" else ""
            sessions = sum(len(v) for v in data["times_by_date"].values())
            print(f"  {i:2d}. {title}{tag}  ({sessions} sessions)")

        if not args.no_pick:
            movies = pick_movies(movies, sorted_titles)
            if not movies:
                print("\nNo movies selected!")
                sys.exit(1)

        dates, matrix = build_matrix_data(movies)
        print(f"\nMatrix: {len(matrix)} movies x {len(dates)} dates")

        html = generate_html(
            dates, matrix,
            args.supabase_url, args.supabase_key,
            args.days,
        )

        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"\n{'=' * 60}")
        print("DONE!")
        print("=" * 60)
        print(f"\nGenerated: {args.output}")
        print(f"\nNext steps:")
        print(f"  1. Push {args.output} to a GitHub repo with GitHub Pages enabled")
        print(f"  2. Embed the GitHub Pages URL in SharePoint's Embed web part")
        print("=" * 60)


if __name__ == "__main__":
    main()
