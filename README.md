# Brain Candy Bot

Curated essays for critical thinkers. Posts to [@candyforthebrain](https://t.me/candyforthebrain) on Telegram.

## What It Does

Brain Candy is a human-in-the-loop content curation bot that:

- **Discovers** articles from 100+ RSS feeds and Hacker News
- **Scores** content based on learned preferences from training data
- **Reviews** top candidates via DM before posting (human approval required)
- **Posts** approved articles to the channel (max 1 per source per day)

## Review Mode

The bot runs in review mode - nothing gets posted without human approval:

1. **Bot finds candidates**: Scores articles from feeds and HN
2. **Bot sends for review**: DMs top picks with score and source
3. **You approve/reject**: Reply `1` to approve, `0` to reject
4. **Approved articles post**: Only approved content goes to channel
5. **Rejected sources blocked**: Rejecting an article permanently blocks that source

This ensures quality control while still automating discovery and scheduling.

## How Scoring Works

The bot learns what you like through ratings:

1. **Source Reputation**: Each source gets a trust score (0-1) based on past ratings
2. **Title Patterns**: Penalizes roundups, trade alerts, news digests
3. **Hacker News Boost**: High-scoring HN posts (100+ points) get bonus points
4. **Preferred Domains**: Extra boost for essay-heavy sites (paulgraham.com, gwern.net, etc.)

Articles must score above 0.45 to be sent for review.

## Content Sources

### RSS Feeds (100+ sources)
- **Essays**: Paul Graham, Vitalik Buterin, gwern, ribbonfarm
- **AI/Tech**: One Useful Thing, The Gradient, Import AI, Zvi
- **Finance/Crypto**: MacroAlf, Malt Liquidity, a16z crypto
- **Culture**: Experimental History, Rob Henderson, The Intrinsic Perspective

### Hacker News
Pulls top stories (100+ points) with preference for essay domains. Corporate sites (apple.com, openai.com, etc.) and social media (reddit, twitter) are blocked.

### Canonical Essays
87 timeless pieces that never go stale.

## Deployment

Runs on GitHub Actions - triggered hourly by cron-job.org, no server needed.

### Environment Variables
- `TELEGRAM_BOT_TOKEN`: Bot token from @BotFather
- `TELEGRAM_CHANNEL_ID`: Channel username (e.g., @candyforthebrain)

## Files

- `bot.py` - Core logic (fetching, scoring, review mode, posting)
- `main.py` - Entry point with scheduling modes
- `feeds.py` - RSS feed sources and blocked domains
- `canonical.py` - Curated evergreen essays
- `discover.py` - Autonomous source discovery (weekly)
- `training_log.json` - User ratings that inform scoring
- `posted.json` - Tracks posted URLs (prevents duplicates)
- `queue.json` - Upcoming articles ready for review
- `pending_review.json` - Articles awaiting approval
- `approved.json` - Approved articles ready to post
- `rejected_sources.json` - Permanently blocked sources
- `daily_sources.json` - Tracks sources posted today (resets at midnight Chicago)

## Training Data

The bot was trained on 122 article ratings:
- 86 marked as good (70%)
- 36 marked as bad (30%)

High-trust sources: Paul Graham, Vitalik Buterin, One Useful Thing, Zvi, Arthur Hayes
Low-trust sources: News roundups, trade alerts, paywalled content, corporate sites

## Source Discovery

The bot autonomously expands its reach weekly:
- Scrapes Substack recommendations from existing feeds
- Mines Hacker News for quality domains (150+ points)
- Auto-adds top 5 sources that pass quality filters
- Runs silently every Sunday at 10 AM Chicago time
