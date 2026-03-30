# Brain Candy Bot

Curated essays for critical thinkers. Posts to [@candyforthebrain](https://t.me/candyforthebrain) on Telegram.

## What It Does

Brain Candy is an intelligent content curation bot that:

- **Posts** one curated article per day to the channel at 11 AM Chicago time (weekdays)
- **Processes DMs** every 6 hours via GitHub Actions — no local listener needed
- **Scores** articles using source reputation, title classification, and body content analysis
- **Discovers** new sources weekly via Hacker News mining
- **Learns** from your approvals/rejections to improve scoring over time

## Bot DM Commands

Send these to the bot via Telegram DM. Commands are processed every 6 hours by GitHub Actions.

| Command | Action |
|---------|--------|
| `URL` | Queue for next posting slot |
| `!URL` | Post immediately to channel |
| `+URL` | Post immediately + add source to feed rotation |
| `/addfeed URL` | Add source to feed rotation only |
| `1` | Approve article |
| `0` | Skip article (no penalty to source) |
| `x` | Block source permanently |
| `1,0,x,1` | Batch rate multiple articles |
| `/undo` | Undo last rating |
| `/overview` | Pinnable summary of all commands |
| `/stats` | Weekly stats summary |
| `/guide` | Full scoring and queue rules |
| `/help` | Quick command reference |

## How It Works

### Content Pipeline

1. **Collect** — Pulls articles from 100+ RSS feeds and Hacker News (100+ point stories)
2. **Score** — Each article is scored based on source trust, title classification, and content analysis
3. **Filter** — CTAs, events, product announcements, and roundups are penalized or blocked
4. **Queue** — Top-scoring articles enter the queue with category diversity limits
5. **Post** — 1 article posted at 11 AM Chicago time, weekdays

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

Sources are scored using Bayesian smoothing (PRIOR=2) to prevent small-sample bias. A source with 1/1 approvals won't outrank one with 9/10.

## Modes

| Flag | Description |
|------|-------------|
| `--listen` | DM Listener — always-on local mode for instant DM responses |
| `--process-dms` | Process pending DMs once and exit (used by GHA) |
| `--github-actions` | Scheduled posting mode (used by GHA) |
| `--stats` | Send weekly stats summary via DM |

## GitHub Actions Workflows

| Workflow | Schedule | What it does |
|----------|----------|-------------|
| `post.yml` | 11 AM Chicago, Mon-Fri | Posts article, processes DMs, sends Monday stats |
| `process-dms.yml` | Every 6 hours, all 7 days | Processes DMs (URLs, ratings, commands) |
| `discover.yml` | Sunday 10 AM Chicago | Discovers new sources, sends top finds for review |

## Source Discovery

### Weekly Discovery (Sundays 10 AM Chicago via GitHub Actions)
- Mines Hacker News for quality domains (150+ points)
- Sends top discovered sources to DM for 1/0/x review

### Local Discovery
```bash
python3 discover.py --local
```
- Runs full discovery including Substack recommendation scraping (blocked on cloud IPs)
- Sends top discovered sources to your DM for review

## Files

| File | Purpose |
|------|---------|
| `bot.py` | Core logic — fetching, scoring, posting, DM handling |
| `main.py` | Entry point with mode selection |
| `feeds.py` | RSS feed sources, blocked domains/keywords |
| `canonical.py` | 87 curated evergreen essays |
| `discover.py` | Source discovery (HN mining, Substack recs) |
| `queue.json` | Articles scored and ready to post |
| `posted.json` | URLs already posted (deduplication) |
| `training_log.json` | User ratings that inform scoring |
| `pending_review.json` | Articles awaiting approval |
| `rejected_sources.json` | Permanently blocked sources |
| `feed_health.json` | Tracks consecutive feed failures |
| `.env` | Local bot token (gitignored) |

## Deployment

- **DM handling**: GitHub Actions `process-dms.yml` (every 6 hours)
- **Channel posting**: GitHub Actions `post.yml` (11 AM Chicago, weekdays)
- **Discovery**: GitHub Actions `discover.yml` (Sundays) + local `discover.py --local`

### GitHub Secrets
- `TELEGRAM_BOT_TOKEN` — Bot API token from @BotFather
