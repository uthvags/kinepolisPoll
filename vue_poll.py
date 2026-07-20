"""
Vue Cinemas Movie Poll Creator
================================
Scrapes movie showtimes from Vue Cinemas Enschede (cinemaId 1025),
enriches with IMDB scores/genres (via OMDB API),
and generates a self-contained HTML voting page.

This is an addon wrapper around the same Kinepolis pipeline:
the only swap is the scraper. enrich_movies / pick_movies /
to_matrix_poll_data / generate_voting_page are reused as-is.

Requirements:
    pip install playwright requests

Optional:
    set OMDB_API_KEY=your_key   (https://www.omdbapi.com/apikey.aspx)

Usage:
    python vue_poll.py                          # next 7 days, all times
    python vue_poll.py --days 3                  # next 3 days
    python vue_poll.py --after 17:00             # only showtimes after 17:00
    python vue_poll.py --weekdays                # Mon-Fri only
    python vue_poll.py --weekend                 # Sat-Sun only
    python vue_poll.py --days 7 --after 18:00 --weekdays
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta

import requests

# patchright is a drop-in Playwright that strips the automation signals
# (navigator.webdriver, the CDP Runtime.enable leak) Cloudflare uses to reject
# the browser. Without it, solving the "Just a moment..." check never sticks
# because Cloudflare re-flags the browser as a bot. Fall back to stock
# Playwright if patchright isn't installed.
try:
    from patchright.sync_api import sync_playwright
    _USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    _USING_PATCHRIGHT = False

# Generic helpers — work on the movies dict regardless of source cinema.
from kinepolis_scraper import (
    OMDB_API_KEY,
    enrich_movies,
    filter_movies,
    pick_movies,
    scrape_kinepolis,
    to_matrix_poll_data,
)
from matrix_vote_generator import (
    DEFAULT_SUPABASE_ANON_KEY,
    DEFAULT_SUPABASE_URL,
    generate_voting_page,
)
from vue_scraper import scrape_vue

# Keep votes for Vue separated from Kinepolis votes in the shared Supabase table.
STORAGE_PREFIX = "vue"


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Vue Cinemas Enschede showtimes and create a voting page."
    )
    parser.add_argument("--days", type=int, default=7, help="Days to look ahead (default: 7)")
    parser.add_argument("--start", type=str, default=None, help="Poll start date YYYY-MM-DD (default: today)")
    parser.add_argument("--after", type=str, default=None, help="Only showtimes after HH:MM")
    parser.add_argument("--weekdays", action="store_true", help="Mon-Fri only")
    parser.add_argument("--weekend", action="store_true", help="Sat-Sun only")
    parser.add_argument(
        "--supabase-url", type=str, default=DEFAULT_SUPABASE_URL,
        help=f"Supabase project URL (default: {DEFAULT_SUPABASE_URL})",
    )
    parser.add_argument(
        "--supabase-key", type=str, default=DEFAULT_SUPABASE_ANON_KEY,
        help="Supabase anon key",
    )
    parser.add_argument(
        "--output", type=str, default="index.html",
        help="Output HTML file (default: index.html — overwrites the published poll)",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help=f"Clear all votes from Supabase (PollId={STORAGE_PREFIX}) before generating",
    )
    parser.add_argument(
        "--no-pick", action="store_true",
        help="Skip interactive movie picker (include all movies)",
    )
    parser.add_argument(
        "--filter", type=str, default=None, metavar="TERMS",
        help="Keep only movies matching these comma/space-separated substrings "
             "(case-insensitive). Matches movie titles AND showtime labels, so "
             "e.g. 'imax' keeps only IMAX showings. Applied to the final list.",
    )
    parser.add_argument(
        "--merge-kinepolis", action="store_true",
        help="Also scrape Kinepolis Enschede and merge its movies in as extra rows "
             "of this Vue poll (one combined voting page).",
    )
    parser.add_argument(
        "--kinepolis-filter", type=str, default=None, metavar="TERMS",
        help="Filter which Kinepolis movies get merged (same matching as --filter). "
             "E.g. 'imax' merges only the IMAX screening. Implies --merge-kinepolis.",
    )
    parser.add_argument(
        "--kinepolis-label", type=str, default=" (Kinepolis)", metavar="SUFFIX",
        help="Suffix appended to merged Kinepolis movie titles so voters can tell "
             "which cinema a row is for (default: ' (Kinepolis)').",
    )
    parser.add_argument(
        "--cdp", action="store_true",
        help="Attach to your own Edge over CDP instead of launching a browser. "
             "Start Edge first with --remote-debugging-port (see README notes).",
    )
    parser.add_argument(
        "--cdp-url", type=str, default="http://localhost:9222",
        help="CDP endpoint of your running Edge (default: http://localhost:9222)",
    )
    args = parser.parse_args()

    # --kinepolis-filter only makes sense when we actually scrape Kinepolis.
    if args.kinepolis_filter:
        args.merge_kinepolis = True

    if args.after:
        if not re.match(r"^\d{1,2}(:\d{2})?$", args.after):
            print(f"Error: --after must be HH:MM, got '{args.after}'")
            sys.exit(1)
        if ":" not in args.after:
            args.after += ":00"

    if args.start:
        try:
            start_date = datetime.strptime(args.start, "%Y-%m-%d")
        except ValueError:
            print(f"Error: --start must be YYYY-MM-DD, got '{args.start}'")
            sys.exit(1)
    else:
        start_date = datetime.now()

    day_filter = None
    if args.weekdays:
        day_filter = "weekdays"
    elif args.weekend:
        day_filter = "weekend"

    if args.reset:
        print(f"Clearing {STORAGE_PREFIX} votes from Supabase...")
        try:
            resp = requests.delete(
                f"{args.supabase_url}/rest/v1/votes?PollId=eq.{STORAGE_PREFIX}",
                headers={
                    "apikey": args.supabase_key,
                    "Authorization": f"Bearer {args.supabase_key}",
                },
                timeout=10,
            )
            if resp.ok:
                print("  Votes cleared!")
            else:
                print(f"  WARNING: Failed to clear votes ({resp.status_code}): {resp.text}")
        except requests.exceptions.RequestException as e:
            print(f"  WARNING: Could not reach Supabase ({e.__class__.__name__}). "
                  "Continuing without clearing votes.")

    with sync_playwright() as pw:
        print("=" * 60)
        print(f"Scraping Vue Cinemas Enschede ({args.days} days)...")
        if args.after:
            print(f"Filter: showtimes after {args.after}")
        print("=" * 60)

        cdp_browser = None
        if args.cdp:
            # Attach to the user's OWN Edge (launched by them with
            # --remote-debugging-port). Because they started it — not us — the
            # browser has no --enable-automation flag and navigator.webdriver is
            # false, so once they've passed Cloudflare manually in that window
            # our in-page fetches ride their real, human session.
            print(f"  Attaching to your Edge over CDP at {args.cdp_url} ...")
            try:
                cdp_browser = pw.chromium.connect_over_cdp(args.cdp_url)
            except Exception as e:
                print(f"\nERROR: could not connect to Edge at {args.cdp_url} ({e}).")
                print("Start Edge first, e.g. (PowerShell):")
                print('  & "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" '
                      "--remote-debugging-port=9222 "
                      '--user-data-dir="$env:USERPROFILE\\.vue_edge_cdp"')
                print("Then open https://www.vuecinemas.nl/cinema/enschede/nu-in-de-bioscoop "
                      "and pass the Cloudflare check before re-running with --cdp.")
                sys.exit(1)
            ctx = cdp_browser.contexts[0] if cdp_browser.contexts else cdp_browser.new_context()
        else:
            # patchright is most effective with a real browser binary and no
            # extra automation-revealing flags. Use a dedicated profile dir so
            # the cf_clearance cookie persists between runs. Channel is tunable
            # via VUE_BROWSER_CHANNEL (msedge=default, chrome, chromium=bundled).
            user_data_dir = os.path.join(os.path.expanduser("~"), ".vue_poll_browser_pr")
            launch_kwargs = dict(
                user_data_dir=user_data_dir,
                headless=False,
                locale="nl-NL",
                no_viewport=True,
            )
            channel = os.environ.get("VUE_BROWSER_CHANNEL", "msedge")
            if channel and channel != "chromium":
                launch_kwargs["channel"] = channel
            print(f"  Browser: {'patchright' if _USING_PATCHRIGHT else 'playwright'}"
                  f" / channel={channel or 'bundled-chromium'}")
            ctx = pw.chromium.launch_persistent_context(**launch_kwargs)

        movies = scrape_vue(ctx, args.days, args.after, day_filter, start_date=start_date)

        if cdp_browser is not None:
            # Detach without killing the user's browser window.
            cdp_browser.close()
        else:
            ctx.close()

        if not movies:
            print("\nNo movies found!")
            sys.exit(1)

        if OMDB_API_KEY:
            print(f"\nFetching IMDB scores for {len(movies)} movies...")
        else:
            print("\nNo OMDB_API_KEY — using Vue genres (often empty).")
        enrich_movies(movies)

        # ── Merge extra rows from Kinepolis Enschede ──────────────────────────
        if args.merge_kinepolis:
            print("\n" + "=" * 60)
            print("Scraping Kinepolis Enschede for extra rows...")
            print("=" * 60)
            kp_data_dir = os.path.join(os.path.expanduser("~"), ".kinepolis_poll_browser")
            kp_ctx = pw.chromium.launch_persistent_context(
                kp_data_dir,
                headless=False,
                channel="msedge",
                locale="en-GB",
            )
            kp_movies = scrape_kinepolis(
                kp_ctx, args.days, args.after, day_filter, start_date=start_date
            )
            kp_ctx.close()

            if args.kinepolis_filter and kp_movies:
                kp_movies = filter_movies(kp_movies, args.kinepolis_filter)

            if not kp_movies:
                print("  No Kinepolis movies matched — nothing to merge.")
            else:
                # Enrich with clean titles BEFORE adding the cinema suffix, so the
                # OMDB lookups still work.
                enrich_movies(kp_movies)
                merged = 0
                for kp_title, kp_data in kp_movies.items():
                    new_title = f"{kp_title}{args.kinepolis_label}"
                    # Guard against an unlikely clash with an existing Vue row.
                    while new_title in movies:
                        new_title += " "
                    movies[new_title] = kp_data
                    merged += 1
                print(f"  Merged {merged} Kinepolis movie(s) into the Vue poll.")

        # ── Optional string filter over the combined list ────────────────────
        if args.filter:
            before = len(movies)
            movies = filter_movies(movies, args.filter)
            print(f"\nFilter '{args.filter}': kept {len(movies)}/{before} movies.")
            if not movies:
                print("No movies matched the filter!")
                sys.exit(1)

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

        week_start = start_date.strftime("%d %b")
        week_end = (start_date + timedelta(days=args.days - 1)).strftime("%d %b %Y")
        poll_title = f"Vue Enschede Movie Night &mdash; {week_start} to {week_end}"

        poll_data = to_matrix_poll_data(movies, poll_title, storage_prefix=STORAGE_PREFIX)
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
