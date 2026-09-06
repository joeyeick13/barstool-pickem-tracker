# Barstool Pick Em — Automated Tracker

Tracks picks posted by **@barstoolpickem** for **Big Cat, Stool Presidente, and Rico Bosco**.

## What it does

1. Polls the official X API user timeline for new @barstoolpickem posts.
2. Sends the post text + attached images to an OpenAI vision-capable model to extract explicit picks.
3. Stores each pick as an immutable row in `data/picks.json`, linked to the source X post.
4. Grades supported CFB moneylines, full-game spreads, and full-game totals from final scores.
5. Rebuilds `data/dashboard.json`, which `index.html` renders as a live capper leaderboard.
6. A GitHub Action can run the pipeline every 5 minutes and GitHub Pages can host the dashboard.

## Important production note

The included CFB grader uses ESPN's public-facing scoreboard JSON endpoint because it is easy to get started with, but it is not a documented commercial data contract. For a durable production tracker, swap `scripts/grade.py` to a licensed sports-data provider.

## Setup

### 1. Create a GitHub repository
Upload all files in this folder to a new repository.

### 2. Add two repository secrets
GitHub → Settings → Secrets and variables → Actions → New repository secret:

- `X_BEARER_TOKEN`
- `OPENAI_API_KEY`

The official X API user timeline endpoint is `GET /2/users/{id}/tweets`. The script first resolves `barstoolpickem` to its user ID and then requests new posts using `since_id`.

### 3. Enable GitHub Pages
GitHub → Settings → Pages → Deploy from branch → `main` / root.

Your `index.html` will then be publicly accessible and will read `data/dashboard.json` directly.

### 4. Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export X_BEARER_TOKEN='...'
export OPENAI_API_KEY='...'
python scripts/run_all.py
python -m http.server 8000
```

Open `http://localhost:8000`.

## Confidence / review behavior

AI-extracted picks with confidence >= 0.90 are set to `OPEN`. Anything below that is saved as `REVIEW` and is **not automatically graded**. This is intentional: a false pick is worse than a missed pick.

## Current grading support

Automatic:
- Full-game CFB spread
- Full-game CFB total
- CFB moneyline

Captured but left for later enhancement/manual review:
- First-half markets
- Team totals
- Player props
- Live bets
- Parlays

## Add a pick manually

You can append a record to `data/picks.json` using this minimum structure:

```json
{
  "id": "manual-001",
  "picker": "Big Cat",
  "sport": "CFB",
  "matchup": "Example State vs Example Tech",
  "team": "Example State",
  "opponent": "Example Tech",
  "bet_type": "SPREAD",
  "selection": "Example State -3.5",
  "side": "Example State",
  "line": -3.5,
  "odds": -110,
  "units": 1,
  "mortal_lock": false,
  "confidence": 1,
  "status": "OPEN",
  "result": null,
  "profit_units": 0,
  "source_url": "https://x.com/barstoolpickem/status/...",
  "posted_at": "2026-09-05T12:00:00Z"
}
```

Then run:

```bash
python scripts/grade.py
python scripts/build_site.py
```

## Recommended next upgrades

- Store data in SQLite/Postgres instead of JSON once the history grows.
- Add kickoff timestamps and reject/flag late picks automatically.
- Add historical Week filters and season filters.
- Add closing-line-value tracking from an odds provider.
- Add a small admin page where `REVIEW` picks can be approved/corrected without editing JSON.
- Add 1H grading using period-level scoring data.
