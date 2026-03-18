# Brain Candy Bot

Telegram bot that posts curated essays to @candyforthebrain channel.

## Architecture

Two-part system:
- **Local listener** (`--listen`) — Always-on process for instant DM handling
- **GitHub Actions** (`--github-actions`) — Scheduled channel posting at 11 AM Chicago

## Schedule
- **11 AM Chicago time, Monday-Friday only** (one post per day)
- Runs via GitHub Actions (`.github/workflows/post.yml`)
- Cron: `0 16,17 * * 1-5` (UTC times, two slots to cover CST/CDT)

## Key Files
- `main.py` — Entry point, handles mode selection
- `bot.py` — Core logic: queue building, scoring, posting, DM listener, content intelligence
- `feeds.py` — RSS feeds, blocked domains/keywords, feed types and categories
- `discover.py` — Source discovery (Substack recs, HN mining, local + GHA modes)
- `canonical.py` — 87 curated evergreen essays
- `queue.json` — Articles scored and ready to post
- `posted.json` — URLs already posted (deduplication)
- `training_log.json` — Historical ratings for source scoring
- `pending_review.json` — Article awaiting approval
- `rejected_sources.json` — Permanently blocked sources
- `.env` — Local bot token (gitignored, not in repo)

## Modes
- `--listen` — **DM Listener**: always-on local mode, polls every 3s, handles /help, URLs, ratings
- `--github-actions` — Used by GitHub Actions workflow for scheduled posting
- `--stats` — Sends weekly stats summary via DM
- `--scheduled` — Local continuous posting mode (legacy)
- `--production` — Production mode (legacy)
- Default: Training mode (sends articles for rapid review)

## Bot DM Commands
- `/help` — Show command list
- `1` — Approve article
- `0` — Skip article (no source penalty)
- `x` — Block source permanently
- `1,0,x,1` — Batch rate multiple articles
- Send URL — Queue for next posting slot
- `!URL` — Post immediately to channel

## Content Intelligence
- Title classifier: essay, cta, news, event, product, roundup
- Body content check (fetches article summary for non-essay titles)
- Score penalties: cta -0.5, event -0.4, roundup -0.3, product -0.2, news -0.1
- Bayesian source scoring with PRIOR=2

## Queue Rules
- Essays never expire, news expires in 7 days, HN in 3 days
- Max 5 per category, max 2 per source
- MIN_SCORE_THRESHOLD = 0.45

## Running Locally
```bash
# 1. Add token to .env file
# 2. Start listener
python3 main.py --listen
```

## Secrets (GitHub)
- `TELEGRAM_BOT_TOKEN` — Bot API token
- Channel: `@candyforthebrain`
- Andy's chat ID: `1023849161` (for DMs)
