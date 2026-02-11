import feedparser
import requests
import json
import os
import time
import random
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from zoneinfo import ZoneInfo

from feeds import FEEDS, BLOCKED_DOMAINS, BLOCKED_KEYWORDS
from canonical import CANONICAL_READINGS

# Hacker News API settings
HN_API_URL = "https://hn.algolia.com/api/v1/search"
HN_MIN_POINTS = 100  # Only fetch stories with 100+ points
HN_PREFERRED_DOMAINS = [
    # High-quality essay domains to prioritize from HN
    "paulgraham.com", "danluu.com", "gwern.net", "lesswrong.com",
    "astralcodexten.substack.com", "slatestarcodex.com", "overcomingbias.com",
    "stratechery.com", "ben-evans.com", "eugenewei.com", "ribbonfarm.com",
    "waitbutwhy.com", "nadia.xyz", "vitalik.eth.limo", "patrickcollison.com",
    "marginalrevolution.com", "elidourado.com", "noahpinion.substack.com",
]


def fix_known_redirects(url: str) -> str:
    """Fix known broken/redirect URLs from feeds."""
    # Vitalik's blog moved from vitalik.ca to vitalik.eth.limo
    if "vitalik.ca/" in url:
        url = url.replace("vitalik.ca/", "vitalik.eth.limo/")
    return url


def normalize_url(url: str) -> str:
    """Normalize URL for deduplication only - don't use for posting."""
    if not url:
        return url

    parsed = urlparse(url)

    # Force https, lowercase domain only (not path)
    scheme = "https"
    netloc = parsed.netloc.lower()

    # Remove trailing slash from path (keep original case)
    path = parsed.path.rstrip("/")

    # Remove tracking parameters
    tracking_params = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "source"}
    query_params = parse_qs(parsed.query)
    filtered_params = {k: v for k, v in query_params.items() if k.lower() not in tracking_params}
    query = urlencode(filtered_params, doseq=True) if filtered_params else ""

    return urlunparse((scheme, netloc, path, "", query, ""))


# === CONFIGURATION ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_TOKEN_HERE")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@candyforthebrain")

# Training mode - send to Andy directly for review
ANDY_CHAT_ID = "1023849161"

# Scoring thresholds
MIN_SCORE_THRESHOLD = 0.45  # Only post articles scoring above this

# Files for tracking state
DATA_DIR = Path(__file__).parent
POSTED_FILE = DATA_DIR / "posted.json"
TRAINING_LOG_FILE = DATA_DIR / "training_log.json"
QUEUE_FILE = DATA_DIR / "queue.json"
PENDING_REVIEW_FILE = DATA_DIR / "pending_review.json"
DAILY_SOURCES_FILE = DATA_DIR / "daily_sources.json"  # Track sources posted today

# URLs Andy already shared (don't send these for review)
ALREADY_SEEN_URLS = [
    "https://catherineshannon.substack.com/p/everyone-is-numbing-out",
    "https://reducibleerrors.com/prediction-markets/",
    "https://telah.vc/hyperstitions",
    "https://pmillerd.com/mediocre/",
    "https://welf.substack.com/p/what-does-it-take-for-wisdom-to-win",
    "https://nadia.xyz/basic",
    "https://www.pride.com/culture/celebrities/tiktok-censoring-megan-stalter-and-finneas",
    "https://tech.lgbt/@JadedBlueEyes/115967791152135761",
    "https://freddiedeboer.substack.com/p/the-buffalo-bills-are-a-mess-but",
]

# Additional blocked keywords (paywalled/premium content)
PREMIUM_KEYWORDS = [
    "trade alert", "premium", "members only", "subscriber only",
    "paid subscribers", "upgrade to read", "unlock this post",
    "for paying subscribers", "member-only",
]


def load_json(filepath, default):
    if filepath.exists():
        with open(filepath, "r") as f:
            return json.load(f)
    return default


def save_json(filepath, data):
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)


def get_today_date() -> str:
    """Get today's date in Chicago timezone as YYYY-MM-DD string."""
    chicago_tz = ZoneInfo("America/Chicago")
    return datetime.now(chicago_tz).strftime("%Y-%m-%d")


def load_daily_sources() -> set:
    """Load sources that have already been posted today. Resets at midnight Chicago time."""
    data = load_json(DAILY_SOURCES_FILE, {"date": None, "sources": []})
    today = get_today_date()

    # If it's a new day, reset the tracking
    if data.get("date") != today:
        return set()

    return set(data.get("sources", []))


def save_daily_source(source: str):
    """Add a source to today's posted sources."""
    data = load_json(DAILY_SOURCES_FILE, {"date": None, "sources": []})
    today = get_today_date()

    # Reset if new day
    if data.get("date") != today:
        data = {"date": today, "sources": []}

    if source not in data["sources"]:
        data["sources"].append(source)

    save_json(DAILY_SOURCES_FILE, data)


def is_blocked(url: str, title: str = "") -> bool:
    url_lower = url.lower()
    title_lower = title.lower()
    normalized = normalize_url(url)

    # Check if already seen
    if any(normalize_url(seen_url) == normalized for seen_url in ALREADY_SEEN_URLS):
        return True
    
    # Check blocked domains
    for domain in BLOCKED_DOMAINS:
        if domain in url_lower:
            return True
    
    # Check blocked keywords
    for keyword in BLOCKED_KEYWORDS:
        if keyword in title_lower:
            return True
    
    # Check premium/paywall keywords
    for keyword in PREMIUM_KEYWORDS:
        if keyword in title_lower:
            return True
    
    return False


def send_message(chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending message: {e}")
        return False


def get_updates(offset=None):
    """Get new messages sent to the bot"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 5}
    if offset:
        params["offset"] = offset
    
    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get("result", [])
    except Exception as e:
        print(f"Error getting updates: {e}")
    return []


def fetch_hacker_news(min_points: int = HN_MIN_POINTS, max_articles: int = 30) -> list:
    """Fetch high-scoring articles from Hacker News."""
    articles = []

    try:
        # Search for recent high-scoring stories
        params = {
            "tags": "story",
            "numericFilters": f"points>{min_points}",
            "hitsPerPage": 50,
        }

        response = requests.get(HN_API_URL, params=params, timeout=15)

        if response.status_code != 200:
            print(f"HN API error: {response.status_code}")
            return []

        data = response.json()
        hits = data.get("hits", [])

        for hit in hits:
            url = hit.get("url", "")
            title = hit.get("title", "")
            points = hit.get("points", 0)

            # Skip if no URL (self posts)
            if not url:
                continue

            # Skip blocked content
            if is_blocked(url, title):
                continue

            # Determine source name
            domain = urlparse(url).netloc.replace("www.", "")
            source_name = f"HN ({points}pt) via {domain}"

            # Boost score for preferred domains
            is_preferred = any(pref in url.lower() for pref in HN_PREFERRED_DOMAINS)

            articles.append({
                "title": title,
                "link": fix_known_redirects(url),
                "source": source_name,
                "hn_points": points,
                "hn_preferred": is_preferred,
            })

            if len(articles) >= max_articles:
                break

        print(f"Fetched {len(articles)} articles from Hacker News")
        return articles

    except Exception as e:
        print(f"Error fetching from Hacker News: {e}")
        return []


def fetch_feed(feed_info: dict) -> list:
    try:
        feed = feedparser.parse(feed_info["url"])
        entries = []

        for entry in feed.entries[:10]:
            title = entry.get("title", "Untitled")
            link = fix_known_redirects(entry.get("link", ""))

            if is_blocked(link, title):
                print(f"Blocked: {title[:40]}...")
                continue
            
            entries.append({
                "title": title,
                "link": link,
                "source": feed_info["name"],
            })
        
        return entries
    
    except Exception as e:
        print(f"Error fetching {feed_info['name']}: {e}")
        return []


def collect_articles():
    """Collect new articles from all feeds"""
    seen_raw = load_json(POSTED_FILE, [])
    seen = {normalize_url(u) for u in seen_raw}
    training_log = load_json(TRAINING_LOG_FILE, [])
    reviewed_urls = {normalize_url(item["url"]) for item in training_log}

    new_articles = []

    shuffled_feeds = FEEDS.copy()
    random.shuffle(shuffled_feeds)

    for feed_info in shuffled_feeds:
        entries = fetch_feed(feed_info)

        for entry in entries:
            url = entry["link"]
            normalized = normalize_url(url)

            # Skip if already seen or reviewed
            if normalized in seen or normalized in reviewed_urls:
                continue

            new_articles.append(entry)
            seen.add(normalized)
        
        time.sleep(0.3)
    
    save_json(POSTED_FILE, list(seen))
    return new_articles


def send_for_review(article: dict, num: int = 1) -> bool:
    """Send an article to Andy for review"""
    title = article["title"]
    link = article["link"]
    source = article["source"]
    
    message = f"""📋 <b>#{num}</b>

<b>{title}</b>

{link}

<i>— {source}</i>

Reply: <b>1</b>=👍  <b>0</b>=👎"""
    
    return send_message(ANDY_CHAT_ID, message)


def process_responses():
    """Check for Andy's responses - handles batch responses like '1,0,1,1,0' or individual '1'/'0'"""
    training_log = load_json(TRAINING_LOG_FILE, [])
    pending = load_json(PENDING_REVIEW_FILE, [])
    
    if not pending:
        return
    
    updates = get_updates()
    
    for update in updates:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip().lower()
        
        if chat_id != ANDY_CHAT_ID:
            continue
        
        # Handle batch response like "1,0,1,1,0" or "10110"
        ratings = []
        
        # Remove any spaces or commas, just get the 1s and 0s
        clean_text = text.replace(",", "").replace(" ", "").replace("y", "1").replace("n", "0")
        
        for char in clean_text:
            if char == "1":
                ratings.append("good")
            elif char == "0":
                ratings.append("bad")
        
        # Apply ratings to pending articles in order
        if ratings:
            for i, rating in enumerate(ratings):
                if i < len(pending):
                    pending[i]["rating"] = rating
                    training_log.append(pending[i])
                    print(f"Logged {rating.upper()}: {pending[i].get('title', '')[:40]}...")
            
            # Remove rated articles from pending
            pending = pending[len(ratings):]
            save_json(PENDING_REVIEW_FILE, pending)
    
    save_json(TRAINING_LOG_FILE, training_log)
    
    # Clear processed updates
    if updates:
        last_update_id = updates[-1]["update_id"]
        get_updates(offset=last_update_id + 1)


def run_training():
    """Main training loop - sends batch of articles for rapid review"""
    print(f"[{datetime.now()}] Training mode running...")
    
    # First, process any responses
    process_responses()
    
    # Load pending reviews
    pending = load_json(PENDING_REVIEW_FILE, [])
    
    # Count how many are still awaiting response
    awaiting = len(pending)
    
    # If less than 5 pending, send more articles
    if awaiting < 5:
        articles = collect_articles()
        
        if not articles:
            # Fall back to canonical evergreen essays
            seen_urls = {normalize_url(u) for u in load_json(POSTED_FILE, [])}
            reviewed_urls = {normalize_url(item["url"]) for item in load_json(TRAINING_LOG_FILE, [])}
            for canon in CANONICAL_READINGS:
                canon_normalized = normalize_url(canon["url"])
                if canon_normalized not in seen_urls and canon_normalized not in reviewed_urls:
                    articles.append({
                        "title": canon["title"],
                        "link": canon["url"],
                        "source": canon.get("author", "Canonical"),
                    })
                    if len(articles) >= 5:
                        break
            if not articles:
                print("No new articles found")

        if articles:
            # Send up to 5 new articles
            to_send = min(5 - awaiting, len(articles))
            
            for i in range(to_send):
                article = articles[i]
                article_num = len(pending) + 1
                
                if send_for_review(article, article_num):
                    pending.append({
                        "title": article["title"],
                        "url": article["link"],
                        "source": article["source"],
                        "sent_at": datetime.now().isoformat(),
                    })
                    print(f"Sent #{article_num}: {article['title'][:40]}...")
                    time.sleep(1)  # Small delay between sends
            
            save_json(PENDING_REVIEW_FILE, pending)
    else:
        print(f"Waiting for responses on {awaiting} pending articles...")
    
    # Log stats
    training_log = load_json(TRAINING_LOG_FILE, [])
    good = len([x for x in training_log if x.get("rating") == "good"])
    bad = len([x for x in training_log if x.get("rating") == "bad"])
    print(f"Training progress: {good} good, {bad} bad, {good + bad} total")


# === PRODUCTION MODE ===

# Title patterns that indicate low-quality content
BAD_TITLE_PATTERNS = [
    "roundup", "reading list", "classifieds", "open thread",
    "discussion post", "weekly top", "ainews", "[ainews]",
    "trade alert", "earnings,", "personal day",
]


def get_source_scores() -> dict:
    """Calculate trust scores per source from training data."""
    training_log = load_json(TRAINING_LOG_FILE, [])
    source_stats = {}

    for item in training_log:
        source = item.get("source", "Unknown")
        rating = item.get("rating", "")

        if source not in source_stats:
            source_stats[source] = {"good": 0, "bad": 0}

        if rating == "good":
            source_stats[source]["good"] += 1
        elif rating == "bad":
            source_stats[source]["bad"] += 1

    # Calculate scores (good ratio)
    scores = {}
    for source, stats in source_stats.items():
        total = stats["good"] + stats["bad"]
        if total > 0:
            scores[source] = stats["good"] / total
        else:
            scores[source] = 0.5  # Neutral for unknown

    return scores


def score_article(article: dict, source_scores: dict) -> float:
    """Score an article based on source reputation and title patterns."""
    title = article.get("title", "").lower()
    source = article.get("source", "")

    # Start with source score (or 0.5 if unknown)
    score = source_scores.get(source, 0.5)

    # Penalize bad title patterns
    for pattern in BAD_TITLE_PATTERNS:
        if pattern in title:
            score -= 0.3
            break

    # Bonus for Hacker News articles based on points
    hn_points = article.get("hn_points", 0)
    if hn_points > 0:
        # Base bonus for being on HN with high points
        if hn_points >= 500:
            score += 0.3
        elif hn_points >= 300:
            score += 0.2
        elif hn_points >= 150:
            score += 0.1

        # Extra bonus for preferred essay domains
        if article.get("hn_preferred", False):
            score += 0.15

    # Clamp to 0-1
    return max(0.0, min(1.0, score))


def post_to_channel(article: dict) -> bool:
    """Post an article to the Telegram channel."""
    title = article["title"]
    link = article["link"]
    source = article["source"]

    message = f"""<b>{title}</b>

{link}

<i>— {source}</i>"""

    return send_message(TELEGRAM_CHANNEL_ID, message)


def collect_articles_without_saving():
    """Collect new articles from feeds without saving to posted.json."""
    seen_raw = load_json(POSTED_FILE, [])
    seen = {normalize_url(u) for u in seen_raw}
    training_log = load_json(TRAINING_LOG_FILE, [])
    reviewed_urls = {normalize_url(item["url"]) for item in training_log}

    new_articles = []

    shuffled_feeds = FEEDS.copy()
    random.shuffle(shuffled_feeds)

    for feed_info in shuffled_feeds:
        entries = fetch_feed(feed_info)

        for entry in entries:
            url = entry["link"]
            normalized = normalize_url(url)

            if normalized in seen or normalized in reviewed_urls:
                continue

            new_articles.append(entry)

        time.sleep(0.3)

    return new_articles


def run_production():
    """Production mode - auto-curate and post to channel."""
    print(f"[{datetime.now()}] Production mode running...")

    # Get source scores from training data
    source_scores = get_source_scores()
    print(f"Loaded scores for {len(source_scores)} sources")

    # Collect new articles (without saving yet)
    articles = collect_articles_without_saving()

    if not articles:
        # Fall back to canonical essays
        seen_urls = {normalize_url(u) for u in load_json(POSTED_FILE, [])}
        training_log = load_json(TRAINING_LOG_FILE, [])
        reviewed_urls = {normalize_url(item["url"]) for item in training_log}
        good_urls = {normalize_url(item["url"]) for item in training_log if item.get("rating") == "good"}

        for canon in CANONICAL_READINGS:
            canon_normalized = normalize_url(canon["url"])
            if canon_normalized in good_urls or (canon_normalized not in seen_urls and canon_normalized not in reviewed_urls):
                articles.append({
                    "title": canon["title"],
                    "link": canon["url"],
                    "source": canon.get("author", "Canonical"),
                    "is_canonical": True,
                })
                if len(articles) >= 3:
                    break

    if not articles:
        print("No articles to post")
        return

    # Score and filter articles
    scored = []
    for article in articles:
        score = score_article(article, source_scores)
        article["score"] = score
        if score >= MIN_SCORE_THRESHOLD:
            scored.append(article)
        else:
            print(f"Filtered (score {score:.2f}): {article['title'][:40]}...")

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Post top articles (limit to 3 per cycle, max 1 per source)
    posted_count = 0
    posted_urls = load_json(POSTED_FILE, [])
    posted_sources = set()

    for article in scored:
        if posted_count >= 3:
            break

        # Skip if we already posted from this source this cycle
        source = article.get("source", "")
        if source in posted_sources:
            continue

        if post_to_channel(article):
            print(f"Posted (score {article['score']:.2f}): {article['title'][:40]}...")
            posted_count += 1
            posted_sources.add(source)
            posted_urls.append(normalize_url(article["link"]))
            time.sleep(2)
        else:
            print(f"Failed to post: {article['title'][:40]}...")

    # Save successfully posted URLs
    if posted_count > 0:
        save_json(POSTED_FILE, posted_urls)

    print(f"Posted {posted_count} articles to {TELEGRAM_CHANNEL_ID}")


# === SCHEDULED POSTING MODE ===

def build_queue():
    """Build a queue of scored articles ready for scheduled posting."""
    print(f"[{datetime.now()}] Building queue...")

    # Get source scores
    source_scores = get_source_scores()

    # Load existing queue
    queue = load_json(QUEUE_FILE, [])
    queue_urls = {normalize_url(item["link"]) for item in queue}

    # Collect new articles from RSS feeds
    articles = collect_articles_without_saving()

    # Also fetch from Hacker News
    hn_articles = fetch_hacker_news()
    seen_urls = {normalize_url(u) for u in load_json(POSTED_FILE, [])}
    for hn_article in hn_articles:
        normalized = normalize_url(hn_article["link"])
        if normalized not in seen_urls and normalized not in queue_urls:
            articles.append(hn_article)

    # Add canonical essays if needed
    if len(articles) < 20:
        seen_urls = {normalize_url(u) for u in load_json(POSTED_FILE, [])}
        training_log = load_json(TRAINING_LOG_FILE, [])
        reviewed_urls = {normalize_url(item["url"]) for item in training_log}
        good_urls = {normalize_url(item["url"]) for item in training_log if item.get("rating") == "good"}

        for canon in CANONICAL_READINGS:
            canon_normalized = normalize_url(canon["url"])
            if canon_normalized not in seen_urls and canon_normalized not in reviewed_urls and canon_normalized not in queue_urls:
                if canon_normalized in good_urls or canon_normalized not in reviewed_urls:
                    articles.append({
                        "title": canon["title"],
                        "link": canon["url"],
                        "source": canon.get("author", "Canonical"),
                    })

    # Track source counts for diversity (max 2 per source in queue)
    MAX_PER_SOURCE = 2
    source_counts = {}
    for item in queue:
        src = item.get("source", "")
        source_counts[src] = source_counts.get(src, 0) + 1

    # Score and filter new articles
    for article in articles:
        normalized = normalize_url(article["link"])
        if normalized in queue_urls:
            continue

        # Limit articles per source for diversity
        source = article.get("source", "")
        if source_counts.get(source, 0) >= MAX_PER_SOURCE:
            continue

        score = score_article(article, source_scores)
        if score >= MIN_SCORE_THRESHOLD:
            article["score"] = score
            queue.append(article)
            queue_urls.add(normalized)
            source_counts[source] = source_counts.get(source, 0) + 1
            print(f"Queued (score {score:.2f}): {article['title'][:40]}...")

    # Sort queue by score
    queue.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Keep queue manageable (max 50 articles)
    queue = queue[:50]

    save_json(QUEUE_FILE, queue)
    print(f"Queue size: {len(queue)} articles")
    return len(queue)


def post_from_queue(count: int = 2):
    """Post articles from the queue (for scheduled posting).

    Enforces one article per source per day to ensure variety.
    """
    print(f"[{datetime.now()}] Posting {count} articles from queue...")

    queue = load_json(QUEUE_FILE, [])

    if not queue:
        print("Queue is empty! Building queue first...")
        build_queue()
        queue = load_json(QUEUE_FILE, [])

    if not queue:
        print("No articles available to post")
        return 0

    posted_count = 0
    posted_urls = load_json(POSTED_FILE, [])

    # Load sources already posted TODAY (resets at midnight Chicago time)
    daily_sources = load_daily_sources()
    print(f"Sources already posted today: {len(daily_sources)}")

    remaining_queue = []

    for article in queue:
        if posted_count >= count:
            remaining_queue.append(article)
            continue

        source = article.get("source", "")

        # Skip if this source already posted today
        if source in daily_sources:
            print(f"Skipping (already posted today): {source}")
            remaining_queue.append(article)
            continue

        if post_to_channel(article):
            print(f"Posted: {article['title'][:50]}...")
            posted_count += 1
            daily_sources.add(source)
            save_daily_source(source)  # Persist immediately
            posted_urls.append(normalize_url(article["link"]))
            time.sleep(2)
        else:
            print(f"Failed to post: {article['title'][:40]}...")
            remaining_queue.append(article)

    # Save state
    if posted_count > 0:
        save_json(POSTED_FILE, posted_urls)
    save_json(QUEUE_FILE, remaining_queue)

    print(f"Posted {posted_count} articles. Queue remaining: {len(remaining_queue)}")
    return posted_count


if __name__ == "__main__":
    run_training()
