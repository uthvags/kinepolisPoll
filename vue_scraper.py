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

import json
from collections import defaultdict
from datetime import datetime, timedelta

from playwright.sync_api import BrowserContext

VUE_BASE = "https://www.vuecinemas.nl"
VUE_CINEMA_ID = 1025  # Enschede
VUE_LISTING_URL = f"{VUE_BASE}/cinema/enschede/nu-in-de-bioscoop"

# Vue tags every session with attributes. Most are noise; skip those.
# Anything else (Dolby Atmos, 3D, Voorpremière, Vue Plus, Nederlandse Versie,
# Marathon, Soft Sound Screening, Mini Mornings, ...) gets appended to the
# time label so distinct formats become distinct vote slots.
_SKIP_ATTRS = {
    "Singleseatrule",       # admin/booking flag
    "Netherlands",           # default language, redundant
    "Flitsende beelden",     # epilepsy warning, not booking-relevant
}


def _format_time_label(start_dt, attributes) -> str:
    base = start_dt.strftime("%H:%M")
    tags: list[str] = []
    for a in attributes or []:
        name = (a.get("name") or "").strip()
        if name and name not in _SKIP_ATTRS and name not in tags:
            tags.append(name)
    return f"{base} {' '.join(tags)}" if tags else base


def _parse_after(after_time: str | None) -> tuple[int, int]:
    if not after_time:
        return 0, 0
    parts = after_time.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def _films_api_url(date_obj) -> str:
    date_iso = date_obj.strftime("%Y-%m-%dT00:00:00")
    return (
        f"{VUE_BASE}/api/microservice/showings/cinemas/{VUE_CINEMA_ID}/films"
        f"?showingDate={date_iso}"
        f"&minEmbargoLevel=3"
        f"&includesSession=true"
        f"&includeSessionAttributes=true"
    )


def _api_returns_json(page, url: str) -> bool:
    """True if an in-page fetch of `url` yields JSON (challenge cleared)."""
    try:
        result = page.evaluate(
            """async (url) => {
                const r = await fetch(url, {
                    method: 'GET',
                    headers: { 'accept': 'application/json' },
                    credentials: 'include',
                });
                const text = await r.text();
                return { ok: r.ok, isJson: text.trim().startsWith('[') || text.trim().startsWith('{') };
            }""",
            url,
        )
        return bool(result.get("ok") and result.get("isJson"))
    except Exception:
        return False


def _clear_cloudflare(page, probe_url: str, max_rounds: int = 5) -> bool:
    """
    Vue's API sits behind a Cloudflare bot challenge. The challenge fires on
    the background API fetch, which has no visible checkbox — so we navigate
    the *top-level* browser to an API URL to surface Cloudflare's interactive
    "Just a moment..." page. A human solves it once; Cloudflare then sets a
    cf_clearance cookie for the whole origin and the per-day fetches succeed.

    Returns True once the API serves JSON, False if it never clears.
    """
    # Fast path: maybe a prior run's cf_clearance cookie is still valid.
    if _api_returns_json(page, probe_url):
        return True

    print("\n" + "!" * 60)
    print("  Cloudflare bot check detected on Vue's API.")
    print("  A browser window is open. If it shows a 'Just a moment...' /")
    print("  'Verify you are human' check, complete it, then wait a moment.")
    print("!" * 60)

    for round_no in range(1, max_rounds + 1):
        # Surface the interactive challenge in the top frame.
        try:
            page.goto(probe_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(1500)

        try:
            input(f"  [{round_no}/{max_rounds}] Solve the check in the browser, "
                  "then press Enter here to continue... ")
        except EOFError:
            # Non-interactive run — just wait and hope it auto-cleared.
            page.wait_for_timeout(6000)

        # Return to the listing page so subsequent fetches carry a normal
        # referer, then verify the challenge is gone.
        try:
            page.goto(VUE_LISTING_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

        if _api_returns_json(page, probe_url):
            print("  Cloudflare check cleared — continuing.\n")
            return True
        print("  Still blocked; let's try again.")

    print("  Could not get past the Cloudflare check.")
    return False


def scrape_vue(
    context: BrowserContext,
    num_days: int = 7,
    after_time: str | None = None,
    day_filter: str | None = None,  # "weekdays", "weekend", or None
    start_date: datetime | None = None,
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
    #
    # When attached over CDP the user likely already has a vuecinemas.nl tab
    # open where they've passed Cloudflare. Reuse it so we ride their live,
    # human session instead of opening a fresh tab that Cloudflare re-checks.
    page = None
    for p in context.pages:
        try:
            if "vuecinemas.nl" in (p.url or ""):
                page = p
                print(f"  Reusing your open Vue tab: {p.url}")
                break
        except Exception:
            continue

    created_page = page is None
    if created_page:
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

    start_d = (start_date or datetime.now()).date()
    movies: dict[str, dict] = {}
    session_count = 0

    # 2) Clear Cloudflare's bot challenge once (human-in-the-loop) using the
    #    first eligible date as the probe, so every per-day fetch below sails
    #    through with the cf_clearance cookie it sets.
    probe_date = start_d
    for offset in range(num_days):
        d = start_d + timedelta(days=offset)
        if day_filter == "weekdays" and d.weekday() >= 5:
            continue
        if day_filter == "weekend" and d.weekday() < 5:
            continue
        probe_date = d
        break

    if not _clear_cloudflare(page, _films_api_url(probe_date)):
        if created_page:
            page.close()
        return {}

    # 3) Pull films for each requested date through the real browser page so
    #    each call carries the session + cf_clearance cookies.
    for offset in range(num_days):
        d = start_d + timedelta(days=offset)
        if day_filter == "weekdays" and d.weekday() >= 5:
            continue
        if day_filter == "weekend" and d.weekday() < 5:
            continue

        api_url = _films_api_url(d)

        # Fetch through the *real browser page* rather than context.request.
        # Vue sits behind a WAF that fingerprints requests: Playwright's
        # APIRequestContext (context.request) shares cookies but not the
        # browser's TLS fingerprint / sec-* headers, so it gets 403'd.
        # An in-page fetch() carries the genuine fingerprint + cookies and
        # is indistinguishable from the site's own front-end calls.
        try:
            result = page.evaluate(
                """async (url) => {
                    const r = await fetch(url, {
                        method: 'GET',
                        headers: { 'accept': 'application/json' },
                        credentials: 'include',
                    });
                    const text = await r.text();
                    return { status: r.status, ok: r.ok, body: text };
                }""",
                api_url,
            )
        except Exception as e:
            print(f"  WARNING: API call failed for {d.isoformat()}: {e}")
            continue

        if not result.get("ok"):
            print(f"  WARNING: HTTP {result.get('status')} for {d.isoformat()}")
            continue

        try:
            payload = json.loads(result.get("body") or "")
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
                    time_label = _format_time_label(st, s.get("attributes"))

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

    if created_page:
        page.close()

    # Sort + dedupe times within each cell
    for title in movies:
        for dk in movies[title]["times_by_date"]:
            movies[title]["times_by_date"][dk] = sorted(set(movies[title]["times_by_date"][dk]))

    print(f"  Total: {len(movies)} movies, {session_count} sessions")
    return movies
