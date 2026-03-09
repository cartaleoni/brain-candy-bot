"""
Brain Candy - Curated essays for @candyforthebrain
"""
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from bot import run_training, run_production, build_queue, post_from_queue, run_review_mode

CHICAGO_TZ = ZoneInfo("America/Chicago")
POSTING_HOURS = [9, 16]

def get_chicago_time():
    return datetime.now(CHICAGO_TZ)

def is_weekday():
    return get_chicago_time().weekday() < 5  # Mon=0, Fri=4

def is_posting_hour():
    return get_chicago_time().hour in POSTING_HOURS and is_weekday()

SCHEDULED_MODE = "--scheduled" in sys.argv
PRODUCTION_MODE = "--production" in sys.argv
GITHUB_ACTIONS_MODE = "--github-actions" in sys.argv

if GITHUB_ACTIONS_MODE:
    print("Brain Candy - GITHUB ACTIONS MODE")
    chicago_now = get_chicago_time()
    print(f"Current time: {chicago_now.strftime('%Y-%m-%d %H:%M %Z')}")
    posting_now = is_posting_hour()
    if posting_now:
        print("Within posting window - will post to channel")
    else:
        print("Outside posting hours - reviews only")
    run_review_mode(should_post=posting_now)
    print("Done!")
elif SCHEDULED_MODE:
    print("Brain Candy - SCHEDULED MODE")
    build_queue()
    last_posted_hour = -1
    while True:
        chicago_now = get_chicago_time()
        if chicago_now.minute == 0 and is_posting_hour() and chicago_now.hour != last_posted_hour:
            post_from_queue(count=1)
            last_posted_hour = chicago_now.hour
            build_queue()
        time.sleep(30)
elif PRODUCTION_MODE:
    print("Brain Candy - PRODUCTION MODE")
    while True:
        try:
            run_production()
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(600)
else:
    print("Brain Candy - TRAINING MODE")
    while True:
        try:
            run_training()
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(30)
