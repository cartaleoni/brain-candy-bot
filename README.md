# Brain Candy Bot

Curated essays for critical thinkers. Posts to [@candyforthebrain](https://t.me/candyforthebrain) on Telegram.

## What It Does

Brain Candy is an intelligent content curation bot that:

- **Posts** curated articles to the channel at 9 AM and 4 PM Chicago time (weekdays)
- **Listens** for DM commands 24/7 via local listener
- **Scores** articles using source reputation, title classification, and body content analysis
- **Discovers** new sources weekly via Substack recommendations and Hacker News
- **Learns** from your approvals/rejections to improve scoring over time

## Quick Start

### 1. Set up your bot token

Get your token from [@BotFather](https://t.me/BotFather) on Telegram, then add it to `.env`:

```
TELEGRAM_BOT_TOKEN=your_actual_token_here
```

### 2. Start the DM listener

```bash
cd ~/Desktop/cortex-bot-local
python3 main.py --listen
```

This runs locally and responds to your DMs instantly. Leave it running in a terminal tab. `Ctrl+C` to stop.

### 3. Channel posting (automatic)

Channel posts are handled by GitHub Actions on a cron schedule. No setup needed — it runs automatically at 9 AM and 4 PM Chicago time on weekdays.

## Bot DM Commands

Send these to the bot via Telegram DM:

| Command | Action |
|---------|--------|
| `/help` | Show all available commands |
| `1` | Approve article |
| `0` | Skip article (no penalty to source) |
| `x` | Block source permanently |
| `1,0,x,1` | Batch rate multiple articles |
| Send a URL | Add to queue (posts next scheduled slot) |
| `!URL` | Post immediately to channel |

## How It Works

### Content Pipeline

1. **Collect** — Pulls articles from 100+ RSS feeds and Hacker News (100+ point stories)
2. **Score** — Each article is scored based on source trust, title classification, and content analysis
3. **Filter** — CTAs, events, product announcements, and roundups are penalized or blocked
4. **Queue** — Top-scoring articles enter the queue with category diversity limits
5. **Post** — 1 article posted per scheduled window (9 AM and 4 PM Chicago, weekdays)

### Content Intelligence

The bot classifies every article title and penalizes non-essay content:

- **CTA** (apply now, sign up, cohort): -0.5 score penalty
- **Event** (conference, summit, webinar): -0.4
- **Roundup** (weekly digest, link roundup): -0.3
- **Product** (changelog, new feature): -0.2
- **News** (announces, raises $, launches): -0.1

Articles that still score well after title penalties get a body content check — if the body reads like a CTA, they're penalized further.

### Queue Management

- **Essays** never expire
- **News** articles expire after 7 days
- **Hacker News** articles expire after 3 days
- Max 5 articles per category in the queue
- Max 2 articles per source in the queue

### Source Scoring

Sources are scored using Bayesian smoothing to prevent small-sample bias. A source with 1/1 approvals won't outrank one with 9/10.

### Dynamic HN Domains

The bot learns which Hacker News domains you like from your training ratings and prioritizes them in future fetches.

## Modes

| Flag | Description |
|------|-------------|
| `--listen` | **DM Listener** — Always-on local mode for instant DM responses |
| `--github-actions` | Used by GitHub Actions for scheduled posting |
| `--stats` | Send weekly stats summary via DM |
| `--scheduled` | Local continuous posting mode (legacy) |
| `--production` | Production mode (legacy) |
| Default | Training mode — sends articles for rapid review |

## Source Discovery

### Weekly Discovery (Sundays 10 AM Chicago via GitHub Actions)
- Mines Hacker News for quality domains (150+ points)
- Sends weekly stats summary via DM

### Local Discovery
```bash
python3 discover.py --local
```
- Runs full discovery including Substack recommendation scraping (blocked on cloud IPs)
- Sends top discovered sources to your DM for review

### Processing Discovery Reviews
```bash
python3 discover.py --process-reviews
```
- Processes your 1/0/x responses to discovery DMs

## Blocked Content

The bot automatically filters out:
- **Corporate sites**: apple.com, openai.com, google.com, microsoft.com, etc.
- **Social media**: reddit, twitter, youtube, linkedin
- **News sites**: nytimes, wsj, bbc, cnn, techcrunch, etc.
- **Government docs**: supremecourt.gov, congress.gov, whitehouse.gov
- **Paywalled content**: stratechery, AI Supremacy, etc.
- **CTA/recruitment**: "apply for", "sign up", "now hiring", etc.

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Core logic — fetching, scoring, posting, DM handling, listener |
| `main.py` | Entry point with mode selection |
| `feeds.py` | RSS feed sources, blocked domains/keywords |
| `canonical.py` | 87 curated evergreen essays |
| `discover.py` | Source discovery (Substack recs, HN mining) |
| `queue.json` | Articles scored and ready to post |
| `posted.json` | URLs already posted (deduplication) |
| `training_log.json` | User ratings that inform scoring |
| `pending_review.json` | Article awaiting your approval |
| `rejected_sources.json` | Permanently blocked sources |
| `daily_sources.json` | Sources posted today (resets at midnight) |
| `discovered_sources.json` | Sources found by discovery system |
| `.env` | Local bot token (gitignored) |

## Deployment

- **DM handling**: Run `python3 main.py --listen` locally (always on)
- **Channel posting**: GitHub Actions cron (`0 14,15,21,22 * * 1-5` UTC)
- **Discovery**: GitHub Actions cron (Sundays) + local `discover.py --local`

### GitHub Secrets
- `TELEGRAM_BOT_TOKEN` — Bot API token from @BotFather
