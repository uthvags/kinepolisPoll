# Poll Generator — No-Code Guide

A tool that creates interactive voting pages. No programming required after the initial setup.

---

## Quick Start (3 steps)

### 1. Generate a blank poll page

Double-click or run this in a terminal:

```
python matrix_vote_generator.py --input empty.json --storage-prefix "mypoll" --supabase-url "https://opadleobyehxumakwjvh.supabase.co" --supabase-key "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9wYWRsZW9ieWVoeHVtYWt3anZoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NzE2MjAsImV4cCI6MjA4OTI0NzYyMH0.GsKMeIYORamBs56Q11cfuM0czWj7GmN7SEL2mCpkKA4" -o mypoll.html
```

Change `"mypoll"` to any unique name for your poll (e.g. `"teamlunch"`, `"boardgame"`). This name keeps polls separate — votes from one poll won't appear in another.

### 2. Open the admin editor

Open the generated file in your browser and add `?admin=true` to the URL:

```
mypoll.html?admin=true
```

You'll see the **Admin Editor** panel at the top of the page with these sections:

| Section | What it does |
|---|---|
| **Poll Title** | Change the heading shown on the page |
| **Supabase Config** | Pre-filled. Use "Test" to verify the connection works |
| **Columns** | Add date/time columns (e.g. "Mon 24 Mar", "Tue 25 Mar") |
| **Items** | Add rows (e.g. movie names, restaurant names). Each has optional Rating and Category fields |
| **Slots Grid** | For each Item + Column combination, enter the vote options separated by commas (e.g. `14:30, 19:00`) |

#### Example: setting up a lunch poll

1. **Add columns:** Click "+ Add Column" and type `Monday`, `Tuesday`, `Wednesday`
2. **Add items:** Click "+ Add Item" and type `Pizza Place`, then repeat for `Sushi Bar`, `Burger Joint`
3. **Fill slots:** In the grid, type `12:00, 13:00` in each cell where that restaurant is available
4. Click **"Apply to Page"** to preview your poll below the editor

### 3. Share the poll

Once you're happy with the setup:

1. Click **"Download JSON"** in the admin editor to save your poll data
2. Regenerate the final HTML (without the admin editor showing by default):
   ```
   python matrix_vote_generator.py --input poll_data.json --supabase-url "https://opadleobyehxumakwjvh.supabase.co" --supabase-key "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9wYWRsZW9ieWVoeHVtYWt3anZoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM2NzE2MjAsImV4cCI6MjA4OTI0NzYyMH0.GsKMeIYORamBs56Q11cfuM0czWj7GmN7SEL2mCpkKA4" -o mypoll.html
   ```
3. Upload `mypoll.html` to GitHub Pages, or share the file directly

Anyone who opens the page can vote immediately — no login required.

---

## How Voting Works

- Voters enter their name, select PVX membership, and specify how many partners/others are joining
- Click any time slot button to vote (green = voted). Click again to remove the vote
- Votes are saved instantly to the cloud (Supabase) — everyone sees updates in real time
- The page shows:
  - **Top Pick** — the most popular option
  - **Charts** — bar charts and a heatmap of votes
  - **Top Picks list** — ranked options with an **Export** button for each
  - **All Votes** — every voter's picks, grouped by name

---

## Exporting to Excel

In the **Top Picks** section, each option has an **Export** button. Clicking it downloads an Excel file with these columns:

| Column | Description |
|---|---|
| Name | Voter's name |
| PVX Member | Yes or No |
| Partners | Number of partners/kids joining |
| Invitees | Number of other guests |
| Amount to be paid | *(blank — fill in manually)* |
| Amount paid | *(blank — fill in manually)* |

---

## Running Multiple Polls

You can run as many polls as you want at the same time. Each poll needs a unique `storage-prefix`:

```
python matrix_vote_generator.py --input empty.json --storage-prefix "movienight" -o movienight.html
python matrix_vote_generator.py --input empty.json --storage-prefix "teamlunch" -o teamlunch.html
python matrix_vote_generator.py --input empty.json --storage-prefix "boardgame" -o boardgame.html
```

All polls share the same Supabase database but their votes are kept completely separate.

---

## Using a CSV Instead of the Editor

If you prefer spreadsheets, create a CSV file with these columns:

| name | column | slot | rating | category |
|---|---|---|---|---|
| Pizza Place | Monday | 12:00 | 4.5 | Italian |
| Pizza Place | Monday | 13:00 | 4.5 | Italian |
| Sushi Bar | Monday | 12:00 | 4.2 | Japanese |
| Sushi Bar | Tuesday | 19:00 | 4.2 | Japanese |

- `name`, `column`, and `slot` are required
- `rating`, `category`, `image_url`, `detail_url` are optional
- Rows with the same `name` are grouped into one item

Then generate:

```
python matrix_vote_generator.py --input mypoll.csv --title "Lunch Poll" --storage-prefix "lunch" -o lunch.html
```

XLSX files also work (requires `pip install openpyxl`).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Admin editor not showing | Make sure the URL ends with `?admin=true` |
| "Excel library not loaded" when exporting | You need internet access — the Excel library loads from a CDN |
| Votes not saving | Check Supabase config: click "Test Connection" in the admin editor |
| Want to clear all votes for a poll | Use: `python kinepolis_poll.py --reset` (for the Kinepolis poll) |

---

## File Overview

| File | What it is |
|---|---|
| `matrix_vote_generator.py` | The main tool — generates voting pages from JSON/CSV/XLSX |
| `empty.json` | Blank starting template |
| `kinepolis_poll.py` | Kinepolis-specific script (scrapes cinema showtimes) |
| `kinepolis_scraper.py` | Kinepolis website scraper + IMDB enrichment |
| `index.html` | The current Kinepolis movie poll (live on GitHub Pages) |
