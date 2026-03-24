"""
Kinepolis Movie Poll Creator
==============================
Scrapes movie showtimes from Kinepolis Enschede (WCST),
enriches with IMDB scores/genres (via OMDB API),
and generates a self-contained HTML voting page.

Requirements:
    pip install playwright requests

Optional:
    export OMDB_API_KEY=your_key  (https://www.omdbapi.com/apikey.aspx)

Usage:
    python kinepolis_poll.py                          # next 7 days, all times
    python kinepolis_poll.py --days 3                  # next 3 days
    python kinepolis_poll.py --after 17:00             # only showtimes after 17:00
    python kinepolis_poll.py --weekdays                # Mon-Fri only
    python kinepolis_poll.py --weekend                 # Sat-Sun only
    python kinepolis_poll.py --days 7 --after 18:00 --weekdays
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

import requests
from playwright.sync_api import sync_playwright

from kinepolis_scraper import (
    OMDB_API_KEY,
    enrich_movies,
    pick_movies,
    scrape_kinepolis,
    to_matrix_poll_data,
)
from matrix_vote_generator import DEFAULT_SUPABASE_ANON_KEY, DEFAULT_SUPABASE_URL, generate_voting_page


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Kinepolis showtimes and create a voting page with Supabase backend."
    )
    parser.add_argument("--days", type=int, default=7, help="Days to look ahead (default: 7)")
    parser.add_argument("--after", type=str, default=None, help="Only showtimes after HH:MM")
    parser.add_argument("--weekdays", action="store_true", help="Mon-Fri only")
    parser.add_argument("--weekend", action="store_true", help="Sat-Sun only")
    parser.add_argument(
        "--supabase-url", type=str, default=DEFAULT_SUPABASE_URL,
        help=f"Supabase project URL (default: {DEFAULT_SUPABASE_URL})"
    )
    parser.add_argument(
        "--supabase-key", type=str, default=DEFAULT_SUPABASE_ANON_KEY,
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
        print("Clearing kinepolis votes from Supabase...")
        resp = requests.delete(
            f"{args.supabase_url}/rest/v1/votes?PollId=eq.kinepolis",
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

        # Build poll title
        week_start = datetime.now().strftime("%d %b")
        week_end = (datetime.now() + timedelta(days=args.days - 1)).strftime("%d %b %Y")
        poll_title = f"Kinepolis Movie Night &mdash; {week_start} to {week_end}"

        poll_data = to_matrix_poll_data(movies, poll_title, storage_prefix="kinepolis")
        html = generate_voting_page(poll_data, args.supabase_url, args.supabase_key)

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
