"""
Brain Candy - Curated essays for @candyforthebrain

Usage:
  python main.py              # Training mode (send to Andy for review)
  python main.py --production # Production mode (continuous posting)
  python main.py --scheduled  # Scheduled mode (hourly 9 AM - 6 PM Chicago)
"""
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from bot import run_training, run_production, build_queue, post_from_queue

# Timezone
CHICAGO_TZ = ZoneInfo("America/Chicago")

# Schedule: post at top of each hour from 9 AM to 6 PM Chicago time
POSTING_HOURS = list(range(9, 19))  # 9, 10, 11, 12, 13, 14, 15, 16, 17, 18


def get_chicago_time():
    """Get current time in Chicago timezone."""
    return datetime.now(CHICAGO_TZ)


def is_posting_hour():
    """Check if current hour is within posting window."""
    chicago_now = get_chicago_time()
    return chicago_now.hour in POSTING_HOURS


def minutes_until_next_hour():
    """Calculate minutes until the top of the next hour."""
    chicago_now = get_chicago_time()
    return 60 - chicago_now.minute


def run_scheduled():
    """Run in scheduled mode - post 1 article at the top of each hour (9 AM - 6 PM Chicago)."""
    print("🍬 Brain Candy - SCHEDULED MODE")
    print("=" * 40)
    print("Posting 1 article per hour")
    print("Schedule: 9 AM - 6 PM Chicago time")
    print("=" * 40)

    # Build initial queue
    build_queue()

    last_posted_hour = -1  # Use -1 so first post waits for top of hour

    while True:
        chicago_now = get_chicago_time()
        current_hour = chicago_now.hour
        current_minute = chicago_now.minute

        # Post at the top of each hour (minute 0) within posting window
        if current_minute == 0 and is_posting_hour() and current_hour != last_posted_hour:
            print(f"\n[{chicago_now.strftime('%Y-%m-%d %H:%M %Z')}] Posting time!")
            post_from_queue(count=1)
            last_posted_hour = current_hour

            # Rebuild queue after posting
            build_queue()

        # Log status every 15 minutes
        if current_minute % 15 == 0 and chicago_now.second < 30:
            next_post = f"{current_hour + 1}:00" if current_hour < 18 else "9:00 tomorrow"
            if is_posting_hour():
                print(f"[{chicago_now.strftime('%H:%M %Z')}] Next post at {next_post} Chicago time")
            else:
                print(f"[{chicago_now.strftime('%H:%M %Z')}] Outside posting hours (9 AM - 6 PM)")

        # Sleep for 30 seconds before checking again
        time.sleep(30)


# Check mode
SCHEDULED_MODE = "--scheduled" in sys.argv or "-s" in sys.argv
PRODUCTION_MODE = "--production" in sys.argv or "-p" in sys.argv
GITHUB_ACTIONS_MODE = "--github-actions" in sys.argv or "-g" in sys.argv

if GITHUB_ACTIONS_MODE:
    # Single-run mode for GitHub Actions (no loop)
    print("Brain Candy - GITHUB ACTIONS MODE")
    print("=" * 40)
    chicago_now = get_chicago_time()
    print(f"Current time: {chicago_now.strftime('%Y-%m-%d %H:%M %Z')}")

    if is_posting_hour():
        print("Within posting window - posting 1 article...")
        build_queue()
        post_from_queue(count=1)
        print("Done!")
    else:
        print(f"Outside posting hours (9 AM - 6 PM Chicago). Skipping.")

    # Exit after single run

elif SCHEDULED_MODE:
    run_scheduled()

elif PRODUCTION_MODE:
    print("🍬 Brain Candy - PRODUCTION MODE")
    print("=" * 40)
    print("Auto-curating and posting to @candyforthebrain")
    print("=" * 40)

    while True:
        try:
            run_production()
        except Exception as e:
            print(f"Error: {e}")

        print(f"[{datetime.now()}] Next check in 10 minutes...\n")
        time.sleep(600)

else:
    print("🍬 Brain Candy - TRAINING MODE")
    print("=" * 40)
    print("Sending articles to Andy for review")
    print("Reply '1' for good, '0' for bad")
    print("=" * 40)

    while True:
        try:
            run_training()
        except Exception as e:
            print(f"Error: {e}")

        print(f"[{datetime.now()}] Checking in 30 seconds...\n")
        time.sleep(30)
