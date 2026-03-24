# Supabase Setup Guide

One Supabase project, one `votes` table, unlimited polls. Each poll is isolated by its `storage_prefix` (stored as `PollId` in the table).

## 1. Create a Supabase project

1. Go to [supabase.com](https://supabase.com) → New Project
2. Note your **Project URL** (e.g. `https://abcdef.supabase.co`)
3. Note your **anon public key** (Settings → API → Project API keys → `anon` `public`)

## 2. Create the `votes` table

Go to **SQL Editor** and run:

```sql
CREATE TABLE votes (
  id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  PollId    TEXT NOT NULL DEFAULT 'kinepolis',
  MovieTitle TEXT NOT NULL,
  ShowDate  TEXT NOT NULL,
  ShowTime  TEXT NOT NULL,
  VoterName TEXT NOT NULL,
  isPvx     TEXT DEFAULT 'Yes',
  Partners  INT DEFAULT 0,
  Others    INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Index for fast per-poll queries
CREATE INDEX idx_votes_poll_id ON votes (PollId);

-- Allow anonymous read/write (votes are public)
ALTER TABLE votes ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow anonymous read"
  ON votes FOR SELECT
  USING (true);

CREATE POLICY "Allow anonymous insert"
  ON votes FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Allow anonymous delete"
  ON votes FOR DELETE
  USING (true);
```

### Upgrading an existing table

If you already have a `votes` table without `PollId`, just add the column:

```sql
ALTER TABLE votes ADD COLUMN PollId TEXT NOT NULL DEFAULT 'kinepolis';
CREATE INDEX idx_votes_poll_id ON votes (PollId);
```

Existing votes will get `PollId = 'kinepolis'`, so your current Kinepolis poll keeps working.

## 3. How polls are isolated

Each poll has a `storage_prefix` (e.g. `"kinepolis"`, `"lunch"`, `"gamenight"`). This is stored as `PollId` in every vote row. When a page loads, it only fetches votes matching its own `PollId`.

```
votes table:
┌────┬───────────┬──────────────┬─────────────┬──────────┬───────────┐
│ id │ PollId    │ MovieTitle   │ ShowDate    │ ShowTime │ VoterName │
├────┼───────────┼──────────────┼─────────────┼──────────┼───────────┤
│  1 │ kinepolis │ Avatar       │ Wed 12 Mar  │ 14:30    │ Uthvag    │
│  2 │ kinepolis │ Avatar       │ Wed 12 Mar  │ 14:30    │ Alice     │
│  3 │ lunch     │ Sumo Sushi   │ Tue 25 Mar  │ 12:00    │ Bob       │
│  4 │ gamenight │ Catan        │ Friday      │ 19:00    │ Uthvag    │
└────┴───────────┴──────────────┴─────────────┴──────────┴───────────┘
```

- `kinepolis_poll.py` → fetches only `PollId = 'kinepolis'`
- Lunch poll → fetches only `PollId = 'lunch'`
- Game night → fetches only `PollId = 'gamenight'`

## 4. Three ways to provide Supabase config

### A. Baked into the HTML at generation time (recommended for sharing)

```bash
python matrix_vote_generator.py \
  --input poll.json \
  --supabase-url https://abcdef.supabase.co \
  --supabase-key eyJhbGci... \
  -o vote.html
```

The URL and key are embedded in the HTML. Anyone who opens the file can vote immediately.

### B. Configured in the browser (for quick testing)

Generate without Supabase args:

```bash
python matrix_vote_generator.py --input poll.json -o vote.html
```

When you open the page, it shows a setup panel where you paste your URL and key. These are saved to localStorage so you only do it once per browser.

### C. Via the admin editor

Open any generated page with `?admin=true`. The admin panel has Supabase URL/key fields with a "Test Connection" button.

## 5. Column name reference

The column names (`MovieTitle`, `ShowDate`, `ShowTime`) are legacy from the Kinepolis origin. They work for any poll type:

| Table column | What it stores       | Example (movies)  | Example (lunch)   | Example (games)  |
|-------------|----------------------|--------------------|--------------------|------------------|
| `PollId`    | `storage_prefix`     | `kinepolis`        | `lunch`            | `gamenight`      |
| `MovieTitle`| Item name            | `Avatar`           | `Sumo Sushi`       | `Catan`          |
| `ShowDate`  | Column header        | `Wed 12 Mar`       | `Tue 25 Mar`       | `Friday`         |
| `ShowTime`  | Slot label           | `14:30`            | `12:00`            | `19:00`          |
| `VoterName` | Voter's name         | `Uthvag`           | `Bob`              | `Alice`          |
| `isPvx`     | PVX membership       | `Yes`              | `No`               | `Yes`            |
| `Partners`  | Extra people (group) | `2`                | `0`                | `1`              |
| `Others`    | Extra people (other) | `0`                | `0`                | `0`              |
