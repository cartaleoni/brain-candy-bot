# Brain Candy Bot

Curated essays for critical thinkers. Posts to [@candyforthebrain](https://t.me/candyforthebrain) on Telegram.

## What It Does

Brain Candy is an intelligent content curation bot that:

- **Posts** 1 article per hour from trusted feeds (9 AM - 6 PM Chicago)
- **Discovers** new sources weekly via Substack recommendations and Hacker News
- **Reviews** discovered sources via DM before adding them to the rotation
- **Learns** from your approvals/rejections to improve over time

## How It Works

### Existing Feeds (Automatic)
Articles from feeds in `feeds.py` post automatically - no review needed. These are pre-approved sources.

- 1 article per hour during posting window
- Max 1 article per source per day (variety)
- Scored based on training data

### Discovered Sources (Review Required)
New sources found by the discovery system are sent for review:

1. Bot sends you **1 article at a time** from a new source
2. Reply `1` to approve → Article posts, source added to feeds.py
3. Reply `0` to reject → Source permanently blocked
4. Bot waits for your response before sending the next one

## Blocked Content

The bot automatically filters out:
- **Corporate sites**: apple.com, openai.com, google.com, microsoft.com, etc.
- **Social media**: reddit, twitter, youtube, linkedin
- **News sites**: nytimes, wsj, bbc, cnn, techcrunch, etc.
- **Government docs**: supremecourt.gov, congress.gov, whitehouse.gov
- **Paywalled content**: stratechery, AI Supremacy, etc.

## Content Sources

### RSS Feeds (100+ sources)
- **Essays**: Paul Graham, Vitalik Buterin, gwern, ribbonfarm
- **AI/Tech**: One Useful Thing, The Gradient, Import AI, Zvi
- **Finance/Crypto**: Arthur Hayes, MacroAlf, a16z crypto
- **Culture**: Experimental History, Rob Henderson, The Intrinsic Perspective

### Hacker News
Pulls top stories (100+ points) with preference for essay domains.

### Canonical Essays
87 timeless pieces that never go stale.

## Deployment

Runs on GitHub Actions - triggered hourly by cron-job.org.

### Environment Variables
- `TELEGRAM_BOT_TOKEN`: Bot token from @BotFather
- `TELEGRAM_CHANNEL_ID`: Channel username (e.g., @candyforthebrain)

## Files

- `bot.py` - Core logic (fetching, scoring, posting, review mode)
- `main.py` - Entry point with scheduling
- `feeds.py` - RSS feed sources and blocked domains
- `canonical.py` - Curated evergreen essays
- `discover.py` - Source discovery (Substack recs, HN mining)
- `discovered_sources.json` - Sources found by discovery
- `training_log.json` - User ratings that inform scoring
- `posted.json` - Tracks posted URLs (prevents duplicates)
- `queue.json` - Upcoming articles from existing feeds
- `pending_review.json` - Discovered source awaiting approval
- `rejected_sources.json` - Permanently blocked sources
- `daily_sources.json` - Sources posted today (resets at midnight Chicago)

## Source Discovery

Weekly discovery (Sundays 10 AM Chicago):
- Scrapes Substack recommendations from existing feeds
- Mines Hacker News for quality domains (150+ points)
- Discovered sources are sent for review, not auto-added
