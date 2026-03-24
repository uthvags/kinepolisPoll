# Kinepolis Movie Poll — Full System Documentation

## Overview

A movie night voting system for PVX members. Scrapes showtimes from Kinepolis Enschede, enriches with IMDB data, generates an interactive voting page, and stores votes in Supabase.

```
Kinepolis Website ──> Python Script ──> index.html ──> GitHub Pages ──> SharePoint (embed)
                           │                │
                     OMDB API (optional)     ▼
                                       Supabase (votes DB)
```

---

## Architecture Decisions & Rationale

### Why this stack?

| Decision | Alternatives explored | Why we chose this |
|---|---|---|
| **Supabase** for vote storage | SharePoint Lists, Power Automate, Azure Functions | Free tier, no Premium license needed, REST API works from static HTML, zero server code |
| **GitHub Pages** for hosting | SharePoint document library, Azure Static Web Apps | Free, serves JS without restrictions. SharePoint blocks JS execution in uploaded HTML files |
| **SharePoint Embed web part** | SPFx, CEWP, direct upload | Only option that works on modern SharePoint without admin access. Just iframes the GitHub Pages URL |
| **Playwright + Edge** for scraping | Selenium, requests-only | Kinepolis uses Drupal with JS-rendered content. Edge passes corporate Conditional Access without extra auth |
| **Self-contained HTML** (single file) | React app, multi-file static site | Simplest deployment — one file, no build step, no dependencies at runtime |
| **`string.Template`** for HTML generation | f-strings, Jinja2 | f-strings break on CSS/JS curly braces. Template uses `$variable` syntax which doesn't conflict |
| **localStorage** for voter identity | Supabase Auth, Azure AD | No auth infrastructure needed. Internal poll — trust-based name entry is acceptable |
| **OMDB API** for ratings/posters | Scraping IMDB directly, TMDB API | Simple key-based API, free tier (1000 req/day), returns rating + poster + IMDB ID in one call |

### Key design choices

- **No authentication** — Voters self-identify by name. Acceptable for internal team poll.
- **RLS disabled on Supabase** — Anon key can read/write. Simplifies client-side code. Acceptable because the poll is low-stakes.
- **Votes are per-showtime** — A vote is for a specific movie + date + time combination, not just a movie.
- **Vote counts show total people** — Includes the voter + their partners/kids + others. This matters for booking capacity.
- **Charts show unique voters** — "Voters by Movie" and "Voters by Day" count distinct names, not total people.
- **Interactive movie picker** — After scraping, the operator chooses which movies to include. Prevents kids' movies or irrelevant screenings from cluttering the poll.

---

## Script Structure (`kinepolis_poll.py`)

**1699 lines** — Python script + embedded HTML/CSS/JS template.

### Section 1: Scraping (lines 61–240)
`scrape_kinepolis(context, num_days, after_time, day_filter)`

- Opens Kinepolis website using Playwright with persistent Edge browser profile
- Accepts cookies automatically
- Extracts data from the `Drupal.settings` JSON blob in page source:
  - **Film names**: regex on `"name":"...","country":"NL","language":"NL","documentType":"film","id":"HO\d+"`
  - **Genres**: regex on `"genres":[...]..."documentType":"film","id":"HO\d+"`
  - **IMDB codes**: regex on `"imdbCode":"tt\d+"` in the film block (up to 6000 chars before the film ID)
  - **Poster images**: regex on `"mediaType":"Poster Graphic","url":"..."` — prefixed with `https://kinepolis.nl`
  - **Sessions**: regex on `"complexOperator":"WCST"` then extracts `"showtime"` and `"film":{"id":"HO\d+"}`
- Filters sessions by: date range, weekday/weekend, time of day
- Returns dict: `{title: {times_by_date, genres, poster, imdb_url}}`

### Section 2: IMDB Enrichment (lines 242–292)
`fetch_imdb_info(title)` / `enrich_movies(movies)`

- Cleans title (removes prefixes like "Cineplus:", suffixes like "OV", "3D", "IMAX")
- Calls OMDB API: `https://www.omdbapi.com/?apikey=KEY&t=TITLE&type=movie`
- Extracts: `imdbRating`, `Genre`, `Poster` URL, `imdbID`
- OMDB data overrides Kinepolis data when available; Kinepolis data is the fallback

### Section 3: Matrix Builder (lines 294–347)
`build_matrix_data(movies)`

- Sorts dates chronologically, movies alphabetically
- Returns: `(sorted_dates, [{title, imdb, genre, poster, imdb_url, cells: {date: [times]}}])`

### Section 4: HTML Generation (lines 349–1503)
`generate_html(dates, matrix, supabase_url, supabase_key, num_days)`

- Uses `string.Template` with `$variable` substitution
- Produces a single self-contained HTML file with:
  - **CSS** (~300 lines): Dark theme, responsive, heatmap colors, dashboard layout
  - **HTML structure**: Sign-in form → Top Pick card → Voting table → Charts → Top Picks summary → All Votes
  - **JavaScript** (~400 lines): Supabase REST API calls, vote toggle, rendering functions

#### HTML page sections (top to bottom):
1. **Title** — "Kinepolis Movie Night — DD Mon to DD Mon YYYY"
2. **Sign-in form** — Name, PVX member (yes/no), Partners/kids (0-5), Others (0-5). Saved to localStorage.
3. **User bar** — Shows current user info, "Change" and "Show all votes" buttons
4. **Legend** — Green = voted, grey = not voted
5. **Top Pick card** — Hero display of the #1 most-voted showtime (by total people)
6. **Voting table** — 2D matrix: movies (rows) × dates (columns) × showtimes (cell buttons)
   - Movie column: poster thumbnail (linked to IMDB), title (linked to IMDB), genre, rating
   - Showtime buttons: click to vote/unvote, badge shows total people count, tooltip shows voter names
   - Cell background: heatmap shading proportional to votes (red tint, 0→max)
7. **Charts section** — "Voters by Movie" bar chart, "Voters by Day" bar chart, Movie×Day heatmap
   - Heatmap splits cells when a movie has multiple showtimes on the same day
8. **Top Picks summary** — Ranked list of showtimes by total people, expandable to see voter details
9. **All Votes** — Grouped by voter name, collapsible sections showing each person's picks

#### Supabase integration (client-side JS):
- `fetchAllVotes()` — GET all votes on page load
- `addVote(movie, date, time)` — POST new vote
- `deleteVote(id)` — DELETE vote by ID
- Headers: `apikey`, `Authorization: Bearer`, `Content-Type: application/json`, `Prefer: return=representation`

### Section 5: Interactive Movie Picker (lines 1505–1572)
`pick_movies(movies, sorted_titles)`

- Shows numbered list of scraped movies
- Input formats: `Enter` (all), `1,3,5` (pick), `1-5,8` (ranges), `-2,5` (exclude)
- Skipped with `--no-pick` flag

### Section 6: Main / CLI (lines 1574–1699)
- Argument parsing: `--days`, `--after`, `--weekdays`, `--weekend`, `--output`, `--reset`, `--no-pick`, `--supabase-url`, `--supabase-key`
- `--reset` deletes all votes from Supabase before generating
- Orchestrates: scrape → enrich → pick → build matrix → generate HTML → write file

---

## External Services & Credentials

### Supabase

| Item | Value |
|---|---|
| Project URL | `https://opadleobyehxumakwjvh.supabase.co` |
| Anon Key | `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9wYWRsZW9ieWVoeHVtYWt3anZoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NzE2MjAsImV4cCI6MjA4OTI0NzYyMH0.GsKMeIYORamBs56Q11cfuM0czWj7GmN7SEL2mCpkKA4` |
| Dashboard | `https://supabase.com/dashboard/project/opadleobyehxumakwjvh` |
| RLS | **Disabled** on `votes` table |

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

### OMDB API

| Item | Value |
|---|---|
| Key | Set via `OMDB_API_KEY` environment variable |
| Free tier | 1,000 requests/day |
| Get a key | `https://www.omdbapi.com/apikey.aspx` |

### GitHub Pages

| Item | Value |
|---|---|
| Repo | `https://github.com/uthvags/kinepolisPoll` |
| Live URL | `https://uthvags.github.io/kinepolisPoll/` |
| File | `index.html` (generated by the script) |
| Branch | `main` |

### SharePoint

| Item | Value |
|---|---|
| Site | `https://movellatech.sharepoint.com/sites/in-PVM/` |
| Web part | **Embed** (under Advanced section) |
| Embed URL | `https://uthvags.github.io/kinepolisPoll/` |

---

## Weekly Workflow

```cmd
cd %USERPROFILE%\kinepolisPoll
set OMDB_API_KEY=d3f7e1a8
python kinepolis_poll.py --days 7 --after 17:00 --reset
:: Interactive picker appears — select movies to include
git add index.html
git commit -m "Update showtimes"
git push
```

GitHub Pages updates within ~1 minute. SharePoint embed refreshes on next page load.

---

## Adapting for a Different Cinema Site

To rebuild this system for a different cinema/event site, you need:

### 1. Data source analysis
- **What to find**: How the site delivers showtime data. Look for:
  - JSON blobs in page source (like Kinepolis' `Drupal.settings`)
  - REST APIs called by the page (check Network tab in DevTools)
  - Server-rendered HTML with structured data (less ideal, needs HTML parsing)
- **Key fields needed**: Movie title, showtime (datetime), and ideally: genre, poster image URL, external ID (IMDB code)

### 2. Scraper modifications
Replace `scrape_kinepolis()` with a new function that:
- Navigates to the cinema's website
- Extracts the data (adjust regexes or use JSON parsing if the site has a clean API)
- Returns the same dict structure:
  ```python
  {
      "Movie Title": {
          "times_by_date": {"Wed 12 Mar": ["14:30", "19:00"], ...},
          "genres": ["Action", "Sci-Fi"],
          "poster": "https://...",       # image URL
          "imdb_url": "https://...",     # link to movie page
      }
  }
  ```

### 3. Site-specific considerations
- **Authentication**: Does the site need cookies/login? Playwright's persistent context handles this.
- **Geolocation/complex selection**: Kinepolis uses `?complex=WCST` for Enschede. Other sites may use location IDs, URLs, or GPS.
- **Anti-scraping**: Some sites block headless browsers. Using `headless=False` with a real browser profile helps.
- **Date formats**: The date label format (`"%a %d %b"` → "Wed 12 Mar") is used as a key in both the matrix and Supabase. Keep it consistent.

### 4. What stays the same
- **Everything after scraping**: Matrix builder, HTML generation, Supabase integration, voting UI, charts, admin panel
- **OMDB enrichment**: Works for any movie title (cleans common prefixes/suffixes before searching)
- **Infrastructure**: Supabase, GitHub Pages, SharePoint embedding — all site-agnostic

### 5. Supabase setup (if starting fresh)
1. Create free account at `https://supabase.com`
2. Create new project → get Project URL and anon key
3. Create table `votes` with columns: `id` (int8, auto), `created_at` (timestamptz, now()), `MovieTitle` (text), `ShowDate` (text), `ShowTime` (text), `VoterName` (text), `isPvx` (text, default "Yes"), `Partners` (int4, default 0), `Others` (int4, default 0)
4. Disable RLS on the `votes` table (or configure policies if you want access control)
5. Update `SUPABASE_URL` and `SUPABASE_ANON_KEY` in the script

### 6. Customizing the voter form
The sign-in form fields (PVX member, Partners/kids, Others) are specific to this use case. To customize:
- **HTML**: Edit the form in the template (search for `id="name-bar"`)
- **JS**: Update `saveName()`, `addVote()`, and the rendering functions
- **Supabase**: Add/remove columns in the `votes` table to match
- **Summary logic**: Update `getTotalPeople()`, `renderSummary()`, `renderAdmin()`

---

## File Inventory

| File | Location | Purpose |
|---|---|---|
| `kinepolis_poll.py` | `~/kinepolisPoll/` | Main script — scraper + HTML generator |
| `index.html` | `~/kinepolisPoll/` | Generated voting page (served by GitHub Pages) |
| `kinepolis_poll_docs.md` | `~/kinepolisPoll/` | This documentation file |

---

## Security Notes

- The Supabase anon key is **public** (visible in HTML source). This is by design for the anon role.
- RLS is disabled — anyone with the key can read/write votes. Acceptable for an internal poll.
- No authentication — voters identify themselves by name (localStorage). Trust-based.
- OMDB API key is set as an environment variable, not committed to the repo.
- To add security later: enable RLS in Supabase, add Supabase Auth or external auth provider.
