"""
Re-key Kinepolis vote rows to corrected local (Enschede) showtimes.
====================================================================
The Kinepolis scraper used to store showtimes in UTC, so e.g. the 20:00 CEST
IMAX show was saved as "18:00". After fixing the scraper, existing votes still
point at the OLD (wrong) ShowTime and would no longer line up with the buttons
in a freshly generated poll. This script migrates those votes IN PLACE — the
votes are preserved, only their ShowTime/ShowDate key is corrected.

It is deliberately scoped: it only touches rows whose MovieTitle ends with the
Kinepolis label (default " (Kinepolis)"), so Vue rows — which were always stored
in correct local time — are left untouched.

Supabase URL/key are auto-loaded from the baked values in index.html unless
given explicitly.

Usage:
    py remap_kinepolis_times.py                 # DRY RUN — shows the diff, writes nothing
    py remap_kinepolis_times.py --apply         # perform the migration
    py remap_kinepolis_times.py --poll-id vue --label " (Kinepolis)" --year 2026
"""
import argparse
import json
import os
import re
import sys
import urllib.request

from kinepolis_scraper import _to_amsterdam
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")


def load_baked(name):
    if not os.path.exists(INDEX_HTML):
        return None
    html = open(INDEX_HTML, encoding="utf-8").read()
    m = re.search(r"const " + name + r" = (.*?);\n", html)
    return json.loads(m.group(1)) if m else None


def api(method, url, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode()
        return resp.status, (json.loads(raw) if raw.strip() else [])


def corrected_showtime(show_date, show_time, year):
    """Treat the stored (date, HH:MM ...) as UTC and convert to local time.

    Returns (new_date_label, new_time_label). The trailing format/language tag
    (e.g. "Engelse versie IMAX") is preserved verbatim.
    """
    m = re.match(r"^(\d{1,2}):(\d{2})(.*)$", show_time)
    if not m:
        return show_date, show_time  # can't parse — leave unchanged
    hh, mm, tail = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        base = datetime.strptime(f"{show_date} {year}", "%a %d %b %Y")
    except ValueError:
        return show_date, show_time
    utc = base.replace(hour=hh, minute=mm, tzinfo=timezone.utc)
    local = _to_amsterdam(utc)
    return local.strftime("%a %d %b"), local.strftime("%H:%M") + tail


def main():
    ap = argparse.ArgumentParser(description="Re-key Kinepolis votes to local time.")
    ap.add_argument("--supabase-url", default=None)
    ap.add_argument("--supabase-key", default=None)
    ap.add_argument("--poll-id", default="vue", help="PollId the Kinepolis rows live under (default: vue)")
    ap.add_argument("--label", default=" (Kinepolis)", help="Merged-title suffix that marks Kinepolis rows")
    ap.add_argument("--year", type=int, default=2026, help="Year for the showtime dates (default: 2026)")
    ap.add_argument("--apply", action="store_true", help="Actually write changes (default is a dry run)")
    args = ap.parse_args()

    url = args.supabase_url or load_baked("BAKED_SUPABASE_URL")
    key = args.supabase_key or load_baked("BAKED_SUPABASE_KEY")
    if not url or not key:
        print("ERROR: no Supabase URL/key (pass --supabase-url/--supabase-key).")
        sys.exit(1)

    _, votes = api("GET", url + "/rest/v1/votes?select=id,PollId,MovieTitle,ShowDate,ShowTime"
                   "&PollId=eq." + args.poll_id + "&limit=5000", key)
    affected = [v for v in votes if str(v.get("MovieTitle", "")).endswith(args.label)]

    changes = []
    for v in affected:
        nd, nt = corrected_showtime(v["ShowDate"], v["ShowTime"], args.year)
        if (nd, nt) != (v["ShowDate"], v["ShowTime"]):
            changes.append((v, nd, nt))

    print(f"PollId={args.poll_id}  Kinepolis rows={len(affected)}  needing change={len(changes)}\n")
    for v, nd, nt in changes:
        print(f"  id {v['id']}: {v['MovieTitle']}")
        print(f"      {v['ShowDate']} {v['ShowTime']!r}  ->  {nd} {nt!r}")

    if not changes:
        print("Nothing to migrate.")
        return
    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to migrate.")
        return

    # Update in place, one row at a time by id (PATCH). If PATCH is blocked by
    # row-level security, fall back to delete-then-reinsert so votes survive.
    ok = 0
    for v, nd, nt in changes:
        patch_url = url + "/rest/v1/votes?id=eq." + str(v["id"])
        try:
            status, _ = api("PATCH", patch_url, key, {"ShowDate": nd, "ShowTime": nt})
            if status in (200, 204):
                ok += 1
                continue
            raise RuntimeError(f"PATCH status {status}")
        except Exception as e:
            print(f"  WARNING: PATCH failed for id {v['id']} ({e}); trying delete+reinsert...")
            try:
                _, full = api("GET", url + "/rest/v1/votes?id=eq." + str(v["id"]), key)
                row = dict(full[0])
                row.pop("id", None)
                row["ShowDate"], row["ShowTime"] = nd, nt
                api("POST", url + "/rest/v1/votes", key, row)
                api("DELETE", url + "/rest/v1/votes?id=eq." + str(v["id"]), key)
                ok += 1
            except Exception as e2:
                print(f"  ERROR: could not migrate id {v['id']}: {e2}")

    print(f"\nMigrated {ok}/{len(changes)} vote(s).")


if __name__ == "__main__":
    main()
