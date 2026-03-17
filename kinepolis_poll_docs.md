# Kinepolis Movie Poll — System Documentation

## Overview

A movie night voting system for PVX members. Scrapes showtimes from Kinepolis Enschede, generates an interactive voting page, and stores votes in Supabase.

```
Kinepolis Website ──> Python Script ──> HTML voting page ──> GitHub Pages ──> SharePoint (embed)
                                              │
                                              ▼
                                        Supabase (votes DB)
```

---

## Architecture Flow

### 1. Scraping (Python — `kinepolis_poll.py`)

- Opens **Kinepolis Enschede** website (`https://kinepolis.nl/?complex=WCST`) using Playwright + Edge
- Extracts movie data from the Drupal.settings JSON blob embedded in the page source
- Parses film names, genres, and session times for the configured date range
- Filters by: number of days, time of day (`--after`), weekdays/weekend

### 2. IMDB Enrichment (optional)

- If `OMDB_API_KEY` environment variable is set, fetches IMDB ratings and genres via the OMDB API
- Falls back to Kinepolis genre data if no API key

### 3. HTML Generation

- Builds a 2D matrix: **rows** = movies, **columns** = dates, **cells** = showtimes
- Generates a single self-contained HTML file with:
  - All movie/showtime data baked in as JSON
  - CSS styling (dark theme)
  - JavaScript for voting UI + Supabase API calls
- Output file: `kinepolis_poll.html` (default)

### 4. Hosting & Embedding

- HTML is pushed to a **GitHub Pages** repo as `index.html`
- SharePoint page uses the **Embed** web part to iframe the GitHub Pages URL

### 5. Voting (browser-side)

- User enters: name, PVX membership (yes/no), partners/kids count, others count
- Saved to `localStorage` so they don't re-enter each visit
- Clicking a showtime button → POST to Supabase → vote stored
- Clicking again → DELETE from Supabase → vote removed
- All votes fetched on page load → counts and tooltips rendered

---

## Components

### Python Script

| Item | Value |
|---|---|
| File | `C:\Users\Uthvag.Sakthivelu\kinepolis_poll.py` |
| Requirements | `pip install playwright requests` |
| Browser | Uses system Edge (corporate-managed, passes Conditional Access) |
| Browser profile | `~/.kinepolis_poll_browser` (persistent, so login is remembered) |
| Target cinema | Kinepolis Enschede — complex code `WCST` |

**Usage:**

```bash
python kinepolis_poll.py                            # next 7 days, all times
python kinepolis_poll.py --days 3                    # next 3 days
python kinepolis_poll.py --after 17:00               # showtimes after 17:00
python kinepolis_poll.py --weekdays                  # Mon-Fri only
python kinepolis_poll.py --weekend                   # Sat-Sun only
python kinepolis_poll.py --days 7 --after 18:00 --weekdays
python kinepolis_poll.py --output my_poll.html       # custom output filename
```

### Supabase (Vote Storage)

| Item | Value |
|---|---|
| Project URL | `https://opadleobyehxumakwjvh.supabase.co` |
| Anon Key | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9wYWRsZW9ieWVoeHVtYWt3anZoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NzE2MjAsImV4cCI6MjA4OTI0NzYyMH0.GsKMeIYORamBs56Q11cfuM0czWj7GmN7SEL2mCpkKA4` |
| Dashboard | `https://supabase.com/dashboard/project/opadleobyehxumakwjvh` |
| RLS | **Disabled** on `votes` table (anon key can read/write) |

**Table: `votes`**

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | int8 | auto | Primary key |
| `created_at` | timestamptz | now() | When the vote was cast |
| `MovieTitle` | text | — | Movie name |
| `ShowDate` | text | — | e.g. "Tue 17 Mar" |
| `ShowTime` | text | — | e.g. "18:45" |
| `VoterName` | text | — | Who voted |
| `isPvx` | text | "Yes" | PVX member? "Yes" or "No" |
| `Partners` | int4 | 0 | Number of partners/kids joining |
| `Others` | int4 | 0 | Number of other guests joining |

**API endpoints used by the HTML:**

- `GET /rest/v1/votes?select=id,MovieTitle,ShowDate,ShowTime,VoterName,isPvx,Partners,Others&limit=5000` — fetch all votes
- `POST /rest/v1/votes` — add a vote (JSON body)
- `DELETE /rest/v1/votes?id=eq.{id}` — remove a vote

### GitHub Pages (Hosting)

| Item | Value |
|---|---|
| Repo | `https://github.com/uthvags/kinepolisPoll` |
| Live URL | `https://uthvags.github.io/kinepolisPoll/` |
| File | `index.html` (copy of generated `kinepolis_poll.html`) |
| Branch | `main` |
| Pages source | Deploy from branch, `/ (root)` |

### SharePoint (Embedding)

| Item | Value |
|---|---|
| Site | `https://movellatech.sharepoint.com/sites/in-PVM/` |
| Web part | **Embed** (under Advanced section) |
| Embed URL | `https://uthvags.github.io/kinepolisPoll/` |

---

## Updating the Poll (weekly workflow)

1. Run the script: `python kinepolis_poll.py --days 7 --after 17:00`
2. Copy output to GitHub repo: `cp kinepolis_poll.html ~/kinepolisPoll/index.html`
3. Push to GitHub:
   ```bash
   cd ~/kinepolisPoll
   git add index.html
   git commit -m "Update showtimes"
   git push
   ```
4. GitHub Pages updates automatically within ~1 minute
5. SharePoint embed refreshes on next page load
6. (Optional) Clear old votes from Supabase: go to Table Editor → select all rows → delete

---

## Voter Experience

1. Open the SharePoint page (or GitHub Pages URL directly)
2. Enter your name, PVX membership, partners/kids count, others count
3. Click any showtime button to vote (green = voted)
4. Click again to remove vote
5. Hover over buttons to see who voted
6. "Show all votes" button reveals full admin table
7. "Top Picks" summary shows most popular showtimes ranked by total people

---

## Security Notes

- The Supabase anon key is **public** (visible in the HTML source). This is by design — it's the Supabase "anon" role.
- RLS is disabled, meaning anyone with the key can read/write votes. This is acceptable for an internal poll.
- There is no authentication — voters identify themselves by name (stored in localStorage). Someone could vote under a fake name.
- If you need stricter security later, enable RLS in Supabase and add auth policies.
