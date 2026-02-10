# Brain Candy Bot

Curated essays for critical thinkers. Posts to [@candyforthebrain](https://t.me/candyforthebrain) on Telegram.

## What It Does

Brain Candy is an intelligent content curation bot that:

- **Discovers** articles from 100+ RSS feeds and Hacker News
- **Scores** content based on learned preferences from training data
- **Curates** a diverse mix (max 3 articles per source)
- **Posts** 2 articles per hour during 9 AM - 6 PM Chicago time

## How Scoring Works

The bot learns what you like through a training phase:

1. **Source Reputation**: Each source gets a trust score (0-1) based on past ratings
2. **Title Patterns**: Penalizes roundups, trade alerts, news digests
3. **Hacker News Boost**: High-scoring HN posts (100+ points) get bonus points
4. **Preferred Domains**: Extra boost for essay-heavy sites (paulgraham.com, gwern.net, etc.)

Articles must score above 0.45 to be posted.

## Content Sources

### RSS Feeds (100+ sources)
- **Essays**: Paul Graham, Vitalik Buterin, gwern, ribbonfarm
- **AI/Tech**: One Useful Thing, The Gradient, Import AI, Zvi
- **Finance/Crypto**: MacroAlf, Malt Liquidity, a16z crypto
- **Culture**: Experimental History, Rob Henderson, The Intrinsic Perspective

### Hacker News
Pulls top stories (100+ points) with preference for essay domains.

### Canonical Essays
87 timeless pieces that never go stale.

## Deployment

Runs on GitHub Actions - posts automatically every hour, no server needed.

### Environment Variables
- `TELEGRAM_BOT_TOKEN`: Bot token from @BotFather
- `TELEGRAM_CHANNEL_ID`: Channel username (e.g., @candyforthebrain)

## Files

- `bot.py` - Core logic (fetching, scoring, posting)
- `feeds.py` - RSS feed sources and blocked domains
- `canonical.py` - Curated evergreen essays
- `training_log.json` - User ratings that inform scoring
- `posted.json` - Tracks posted URLs (prevents duplicates)
- `queue.json` - Upcoming articles ready to post

## Training Data

The bot was trained on 97 article ratings:
- 64 marked as good (66%)
- 33 marked as bad (34%)

High-trust sources: Paul Graham, Vitalik Buterin, One Useful Thing, Zvi
Low-trust sources: News roundups, trade alerts, paywalled content
