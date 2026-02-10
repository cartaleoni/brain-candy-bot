# Cortex Nutrition Bot - Setup Guide

## What This Bot Does
- Monitors RSS feeds from your favorite Substacks and blogs
- Posts new articles to your Telegram channel automatically
- Tracks what's been posted so you don't get duplicates

## Quick Setup (5 minutes)

### Step 1: Create Your Telegram Channel

1. Open Telegram
2. Tap the pencil/compose icon
3. Select "New Channel"
4. Name it whatever you want (e.g., "Cortex Feed" or "My Signal")
5. Make it Public and give it a username (e.g., @mycortexfeed)
6. Add your bot (@cortexnutritionbot) as an administrator:
   - Open the channel
   - Tap the channel name at the top
   - Tap "Administrators"
   - Tap "Add Administrator"
   - Search for @cortexnutritionbot and add it
   - Give it permission to "Post Messages"

### Step 2: Get Your Channel ID

Your channel ID is just: @yourchannelusername
(whatever username you picked in step 1)

### Step 3: Run the Bot

**Option A: Run on Replit (Recommended - runs 24/7)**

1. Go to replit.com and sign in
2. Click "Create Repl"
3. Choose "Python"
4. Name it "cortex-bot"
5. Upload bot.py and requirements.txt
6. In the "Secrets" tab (lock icon on left), add:
   - Key: TELEGRAM_BOT_TOKEN  Value: 8268414332:AAFX1we6-qWu6sZ_jWX0J092uVQffR8WoyY
   - Key: TELEGRAM_CHANNEL_ID  Value: @yourchannelusername
7. Click "Run"

**Option B: Run on Your Computer**

1. Install Python from python.org if you don't have it
2. Open Terminal (Mac) or Command Prompt (Windows)
3. Navigate to the bot folder
4. Run these commands:

```bash
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="8268414332:AAFX1we6-qWu6sZ_jWX0J092uVQffR8WoyY"
export TELEGRAM_CHANNEL_ID="@yourchannelusername"

python bot.py
```

## Adding/Removing Feeds

Edit the FEEDS list in bot.py. Each feed needs:
- name: A label for the source
- url: The RSS feed URL

Most Substacks use: https://AUTHORNAME.substack.com/feed

## Running Automatically

The bot runs once and exits. To run it continuously:

**On Replit:** Add this to keep it alive (create a file called `main.py`):

```python
import time
from bot import run_bot

while True:
    run_bot()
    time.sleep(1800)  # Wait 30 minutes
```

**On your Mac:** Use cron (ask me if you need help with this)

## Current Feeds Included

- Citrini
- 0xKyle  
- 0xSammy
- Damped Spring
- Fabricated Knowledge
- Fidenza Macro
- Malt Liquidity
- Oldcoin Newcoin
- Scimitar Capital
- Scuttleblurb
- Terminally Drifting
- The Algorithmic Bridge
- Not Boring
- Stratechery
- a16z Crypto
- Vitalik Buterin
- Import AI
- The Gradient
- AI Snake Oil
