"""
Vue Cinemas Scraper (Enschede)
================================
Hits the Vue Cinemas internal showings API and returns the same
movies-dict shape as kinepolis_scraper.scrape_kinepolis, so the rest
of the pipeline (enrich_movies / pick_movies / to_matrix_poll_data /
generate_voting_page) just works.

The API requires session cookies — we let Playwright load the cinema
page once to establish them, then issue JSON requests through the
same browser context.

API discovered via runtime recon (see vue_probe.py / vue_headers_probe.py):
    GET /api/microservice/showings/cinemas/{cinemaId}/films
        ?showingDate=YYYY-MM-DDT00:00:00
        &minEmbargoLevel=3
        &includesSession=true
        &includeSessionAttributes=true

Requirements:
    pip install playwright requests
"""

from collections import defaultdict
from datetime import datetime, timedelta

from playwright.sync_api import BrowserContext

VUE_BASE = "https://www.vuecinemas.nl"
VUE_CINEMA_ID = 1025  # Enschede
VUE_LISTING_URL = f"{VUE_BASE}/cinema/enschede/nu-in-de-bioscoop"


def _parse_after(after_time: str | None) -> tuple[int, int]:
    if not after_time:
        return 0, 0
    parts = after_time.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def scrape_vue(
    context: BrowserContext,
    num_days: int = 7,
    after_time: str | None = None,
    day_filter: str | None = None,  # "weekdays", "weekend", or None
) -> dict[str, dict]:
    """
    Returns:
        {
            "Movie Title": {
                "times_by_date": {"Fri 29 May": ["13:50", "15:20"], ...},
                "genres": [...],
                "poster": "https://...",
                "imdb_url": "https://...",   # falls back to the Vue film page
            },
        }
    """
    filter_hour, filter_min = _parse_after(after_time)
    if after_time:
        print(f"  Time filter: showtimes after {filter_hour:02d}:{filter_min:02d}")
    if day_filter == "weekdays":
        print("  Day filter: weekdays only (Mon-Fri)")
    elif day_filter == "weekend":
        print("  Day filter: weekend only (Sat-Sun)")

    # 1) Warm the session — Vue's API rejects requests without the cookies
    #    its Next.js front-end sets on the first visit.
    page = context.new_page()
    print(f"  Loading {VUE_LISTING_URL} ...")
    page.goto(VUE_LISTING_URL, wait_until="networkidle", timeout=45000)

    # Cookie banner (OneTrust). Best-effort, ignore if it's not there.
    for sel in (
        "button:has-text('Accepteren')",
        "button:has-text('Alles accepteren')",
        "button:has-text('Accept all')",
        "#onetrust-accept-btn-handler",
    ):
        try:
            page.locator(sel).first.click(timeout=2000)
            page.wait_for_timeout(800)
            break
        except Exception:
            pass

    page.wait_for_timeout(1500)

    # 2) Pull films for each requested date through the same browser context
    #    so the API call carries the cookies the front-end just set.
    start_date = datetime.now().date()
    movies: dict[str, dict] = {}
    session_count = 0

    for offset in range(num_days):
        d = start_date + timedelta(days=offset)
        if day_filter == "weekdays" and d.weekday() >= 5:
            continue
        if day_filter == "weekend" and d.weekday() < 5:
            continue

        date_iso = d.strftime("%Y-%m-%dT00:00:00")
        api_url = (
            f"{VUE_BASE}/api/microservice/showings/cinemas/{VUE_CINEMA_ID}/films"
            f"?showingDate={date_iso}"
            f"&minEmbargoLevel=3"
            f"&includesSession=true"
            f"&includeSessionAttributes=true"
        )

        try:
            resp = context.request.get(
                api_url,
                headers={
                    "accept": "application/json",
                    "referer": VUE_LISTING_URL,
                    "accept-language": "nl-NL,nl;q=0.9,en;q=0.8",
                },
                timeout=20000,
            )
        except Exception as e:
            print(f"  WARNING: API call failed for {d.isoformat()}: {e}")
            continue

        if not resp.ok:
            print(f"  WARNING: HTTP {resp.status} for {d.isoformat()}")
            continue

        try:
            payload = resp.json()
        except Exception:
            print(f"  WARNING: non-JSON response for {d.isoformat()}")
            continue

        films = payload.get("result") or []
        day_sessions = 0

        for film in films:
            title = film.get("filmTitle") or film.get("originalTitle") or ""
            if not title:
                continue

            poster = film.get("posterImageSrc") or ""
            film_url = film.get("filmUrl") or ""
            genres = [g.get("name", "") for g in (film.get("genres") or []) if g.get("name")]

            for group in (film.get("showingGroups") or []):
                for s in (group.get("sessions") or []):
                    start = s.get("startTime")
                    if not start:
                        continue
                    try:
                        st = datetime.fromisoformat(start)
                    except Exception:
                        continue
                    if st.date() != d:
                        continue
                    if st.hour < filter_hour or (
                        st.hour == filter_hour and st.minute < filter_min
                    ):
                        continue

                    date_label = st.strftime("%a %d %b")
                    time_label = st.strftime("%H:%M")

                    if title not in movies:
                        movies[title] = {
                            "times_by_date": defaultdict(list),
                            "genres": genres,
                            "poster": poster,
                            # Use the Vue film page as the fallback "detail" link.
                            # OMDB enrichment will overwrite this with a real
                            # IMDB URL when it finds a match.
                            "imdb_url": film_url,
                        }
                    movies[title]["times_by_date"][date_label].append(time_label)
                    day_sessions += 1
                    session_count += 1

        print(f"  {d.strftime('%a %d %b')}: {len(films)} films, {day_sessions} sessions kept")

    page.close()

    # Sort + dedupe times within each cell
    for title in movies:
        for dk in movies[title]["times_by_date"]:
            movies[title]["times_by_date"][dk] = sorted(set(movies[title]["times_by_date"][dk]))

    print(f"  Total: {len(movies)} movies, {session_count} sessions")
    return movies
