# Brain Candy Bot

Telegram bot that posts curated essays to @candyforthebrain channel.

## Schedule
- **9 AM and 4 PM Chicago time, Monday-Friday only**
- Runs via GitHub Actions (`.github/workflows/post.yml`)
- Cron: `0 15,22 * * 1-5` (UTC times)

## Key Files
- `main.py` - Entry point, handles scheduling modes
- `bot.py` - Core logic: queue building, posting, feed fetching
- `feeds.py` - List of RSS feeds to pull from
- `queue.json` - Articles ready to post
- `posted.json` - URLs already posted (prevents duplicates)
- `training_log.json` - Historical ratings for articles

## Modes
- `--github-actions` - Used by GitHub Actions workflow
- `--scheduled` - Local continuous mode (legacy)
- `--production` - Production mode (legacy)
- Default: Training mode

## Known Limitations
- Discovered sources feature disabled (Substack blocks GitHub Actions IPs)
- To re-enable: run bot locally instead of GitHub Actions

## Secrets (GitHub)
- `TELEGRAM_BOT_TOKEN` - Bot API token
- Channel: `@candyforthebrain`
- Andy's chat ID: `1023849161` (for DMs)
