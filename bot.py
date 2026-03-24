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

# Load .env file if it exists (no extra dependency needed)
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

# Hacker News API settings
HN_API_URL = "https://hn.algolia.com/api/v1/search"
HN_MIN_POINTS = 100  # Only fetch stories with 100+ points
HN_PREFERRED_DOMAINS = [
    # High-quality essay/indie domains — only these pass the HN gate
    # Classic essay sites
    "paulgraham.com", "danluu.com", "gwern.net", "lesswrong.com",
    "slatestarcodex.com", "overcomingbias.com", "ben-evans.com",
    "eugenewei.com", "patrickcollison.com", "marginalrevolution.com",
    "elidourado.com",
    # From our FEEDS list (non-substack personal domains)
    "ribbonfarm.com", "waitbutwhy.com", "nadia.xyz", "vitalik.eth.limo",
    "meltingasphalt.com", "benkuhn.net", "ciechanow.ski", "danwang.co",
    "applieddivinitystudies.com", "fantasticanachronism.com", "nintil.com",
    "rootsofprogress.org", "constructionphysics.substack.com",
    "bitsaboutmoney.com", "collabfund.com", "epsilontheory.com",
    "palladiummag.com", "worksinprogress.co", "meaningness.com",
    "autotranslucence.com", "guzey.com", "cdixon.org", "devonzuegel.com",
    "antirez.com", "last-conformer.net", "staysaasy.com", "aeon.co",
    "strangeloopcanon.com", "intrinsicperspective.com", "dwarkeshpatel.com",
    "semianalysis.com", "every.to", "thediff.co", "notboring.co",
    "piratewires.com", "ifp.org", "uncommoncore.co", "latent.space",
    "slowboring.com",
    # Substack — match any substack personal blog from HN
    "substack.com",
    # Other known indie essay domains not in feeds
    "astralcodexten.substack.com",  # kept for specificity in logs
    "noahpinion.substack.com",
    "paragraph.com",
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

    # Force https, lowercase domain, strip www. prefix
    scheme = "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

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

# Queue expiry (essays never expire)
NEWS_EXPIRY_DAYS = 7
HN_EXPIRY_DAYS = 3

# Category diversity
MAX_PER_CATEGORY = 5

# Files for tracking state
DATA_DIR = Path(__file__).parent
POSTED_FILE = DATA_DIR / "posted.json"
TRAINING_LOG_FILE = DATA_DIR / "training_log.json"
QUEUE_FILE = DATA_DIR / "queue.json"
PENDING_REVIEW_FILE = DATA_DIR / "pending_review.json"
DAILY_SOURCES_FILE = DATA_DIR / "daily_sources.json"  # Track sources posted today
APPROVED_FILE = DATA_DIR / "approved.json"  # Articles approved by Andy, ready to post
REJECTED_SOURCES_FILE = DATA_DIR / "rejected_sources.json"  # Sources Andy has rejected
FEED_HEALTH_FILE = DATA_DIR / "feed_health.json"  # Track consecutive feed failures

# Previously hardcoded ALREADY_SEEN_URLS have been merged into posted.json

# Additional blocked keywords (paywalled/premium content) — checked in titles
PREMIUM_KEYWORDS = [
    "trade alert", "premium", "members only", "subscriber only",
    "paid subscribers", "upgrade to read", "unlock this post",
    "for paying subscribers", "member-only",
]

# Paywall indicators found in article body/HTML — checked by fetch
PAYWALL_BODY_SIGNALS = [
    "this post is for paid subscribers",
    "this post is for paying subscribers",
    "upgrade to paid",
    "subscribe to read",
    "continue reading with a subscription",
    "already a paid subscriber",
    "become a paid subscriber",
    "only paid subscribers",
    "only for paid subscribers",
    "available to paid subscribers",
    "to read the rest of this story",
    "subscribe to continue reading",
    "this content is for subscribers",
    "premium subscribers only",
    "you need a subscription",
    "sign in to read",
    "create a free account to continue",
    "free preview",
    "to continue reading",
    "unlock this article",
    "paywall",
]

# Temporarily paused sources (too much recently) - expires after date
# Format: source name or domain -> expiration date (YYYY-MM-DD)
# To pause a source, add it here with a future date
PAUSED_SOURCES = {}


def is_source_paused(source: str, url: str = "") -> bool:
    """Check if a source is temporarily paused."""
    today = datetime.now().strftime("%Y-%m-%d")

    # Check source name
    if source in PAUSED_SOURCES:
        if PAUSED_SOURCES[source] >= today:
            return True

    # Check domain in URL
    if url:
        domain = urlparse(url).netloc.replace("www.", "").lower()
        for paused_domain, expiry in PAUSED_SOURCES.items():
            if paused_domain in domain and expiry >= today:
                return True

    return False


def is_source_rejected(source: str, url: str = "") -> bool:
    """Check if a source has been rejected by Andy."""
    rejected = load_json(REJECTED_SOURCES_FILE, {"sources": [], "domains": []})

    # Check source name
    if source in rejected.get("sources", []):
        return True

    # Check domain
    if url:
        domain = urlparse(url).netloc.replace("www.", "").lower()
        for rejected_domain in rejected.get("domains", []):
            if rejected_domain in domain:
                return True

    return False


def add_rejected_source(source: str, url: str = ""):
    """Add a source to the rejected list."""
    rejected = load_json(REJECTED_SOURCES_FILE, {"sources": [], "domains": []})

    # Add source name
    if source and source not in rejected["sources"]:
        rejected["sources"].append(source)
        print(f"Blocked source: {source}")

    # Add domain
    if url:
        domain = urlparse(url).netloc.replace("www.", "").lower()
        if domain and domain not in rejected["domains"]:
            rejected["domains"].append(domain)
            print(f"Blocked domain: {domain}")

    save_json(REJECTED_SOURCES_FILE, rejected)


def add_source_to_feeds(name: str, url: str, domain: str = ""):
    """Add an approved source to feeds.py."""
    feeds_file = DATA_DIR / "feeds.py"

    try:
        with open(feeds_file, "r") as f:
            content = f.read()

        # Check if already exists (by name, URL, or domain)
        if url in content or f'"name": "{name}"' in content or (domain and domain in content):
            print(f"Source already in feeds.py: {name}")
            return False

        # Find the FEEDS list end (before BLOCKED_DOMAINS)
        insert_marker = "# Domains to skip"
        insert_pos = content.find(insert_marker)
        if insert_pos == -1:
            insert_marker = "BLOCKED_DOMAINS"
            insert_pos = content.find(insert_marker)

        if insert_pos == -1:
            print("Could not find insert position in feeds.py")
            return False

        # Go back to find the last feed entry
        last_bracket = content.rfind("},", 0, insert_pos)
        if last_bracket == -1:
            print("Could not find last feed entry")
            return False

        # Create new entry
        new_entry = f'\n    {{"name": "{name}", "url": "{url}", "category": "Discovered"}},  # Auto-approved'

        # Insert
        new_content = content[:last_bracket + 2] + new_entry + content[last_bracket + 2:]

        with open(feeds_file, "w") as f:
            f.write(new_content)

        print(f"Added to feeds.py: {name} ({domain})")
        return True

    except Exception as e:
        print(f"Error adding source to feeds.py: {e}")
        return False


def load_json(filepath, default):
    if filepath.exists():
        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            print(f"Warning: corrupted JSON in {filepath}, using default")
            return default
    return default


def save_json(filepath, data):
    tmp = filepath.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    tmp.replace(filepath)


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

    # Block action URLs (votes, logins, etc.)
    if "/vote?" in url_lower or "/login" in url_lower or "/submit" in url_lower:
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
        if response.status_code != 200:
            print(f"Telegram API HTTP error: {response.status_code}")
            return False
        result = response.json()
        if not result.get("ok"):
            print(f"Telegram API error: {result.get('description', 'unknown')}")
            return False
        return True
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


def send_help_message():
    """Send a quick command reference via DM."""
    help_text = (
        "🧠 <b>Brain Candy Commands</b>\n"
        "\n"
        "<b>Rating:</b>\n"
        "  <code>1</code> — Approve  |  <code>0</code> — Skip  |  <code>x</code> — Block source\n"
        "  <code>1,0,x,1</code> — Batch rate multiple\n"
        "\n"
        "<b>Submitting:</b>\n"
        "  Send a URL — Queue for next slot\n"
        "  <code>!URL</code> — Post immediately\n"
        "  <code>/addfeed URL</code> — Add blog to rotation\n"
        "\n"
        "<b>Commands:</b>\n"
        "  <code>/help</code> — This quick reference\n"
        "  <code>/guide</code> — Full bot guide (scoring, schedule, etc.)\n"
        "  <code>/stats</code> — Weekly stats summary\n"
        "  <code>/undo</code> — Undo last rating\n"
    )
    send_message(ANDY_CHAT_ID, help_text)
    print("Sent help message to Andy")


def send_guide_message():
    """Send a comprehensive bot guide covering all features and scoring."""
    queue = load_json(QUEUE_FILE, [])
    pending = load_json(PENDING_REVIEW_FILE, [])
    posted = load_json(POSTED_FILE, [])

    guide = (
        "📖 <b>Brain Candy — Full Guide</b>\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🎯 WHAT THIS BOT DOES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Posts one curated essay per day to @candyforthebrain at 11 AM Chicago time (Mon-Fri). "
        "You train it by rating articles in DMs.\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>💬 DM COMMANDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Rating articles:</b>\n"
        "  <code>1</code> — Approve (adds to queue, boosts source trust)\n"
        "  <code>0</code> — Skip (no penalty to source score)\n"
        "  <code>x</code> — Block source permanently\n"
        "  <code>1,0,x,1</code> — Batch rate (maps left→right to pending articles)\n"
        "\n"
        "<b>Submitting:</b>\n"
        "  Send a URL — Queues article for next posting slot\n"
        "  <code>!URL</code> — Posts immediately to channel\n"
        "  <code>/addfeed URL</code> — Adds blog/source to feed rotation\n"
        "\n"
        "<b>Commands:</b>\n"
        "  <code>/help</code> — Quick command reference\n"
        "  <code>/guide</code> — This full guide\n"
        "  <code>/stats</code> — Weekly stats (approvals, queue, top sources)\n"
        "  <code>/undo</code> — Undo last rating (including unblocking)\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📊 SCORING SYSTEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Source trust (Bayesian):</b>\n"
        "  • Starts at 0.5 (neutral)\n"
        "  • Each <code>1</code> rating pushes toward 1.0\n"
        "  • Each <code>0</code> rating pushes toward 0.0\n"
        "  • PRIOR=2 means ~4 ratings to move significantly\n"
        "  • Score = (approvals + 2×0.5) / (total + 2)\n"
        "\n"
        "<b>Article scoring:</b>\n"
        "  • Base score = source trust score\n"
        "  • Title type penalties:\n"
        "    CTA: -0.5 | Event: -0.4 | Roundup: -0.3\n"
        "    Product: -0.2 | News: -0.1 | Essay: 0\n"
        "  • MIN_SCORE_THRESHOLD = 0.45 to enter queue\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📋 QUEUE RULES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  • Max 50 articles, max 5 per category, max 2 per source\n"
        "  • Essays never expire\n"
        "  • News expires in 7 days, HN posts in 3 days\n"
        "  • One source per day (variety enforcement)\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🔄 SCHEDULE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  • <b>Daily 11 AM Chicago (Mon-Fri):</b> Posts 1 article\n"
        "  • <b>Monday 11 AM:</b> Also sends weekly stats\n"
        "  • <b>Sunday 10 AM:</b> Runs source discovery, sends top finds for review\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>🔍 DISCOVERY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  • Mines HN, Lobsters, Reddit, Substack recs, blogrolls\n"
        "  • Scores sources by: multi-method evidence, essay quality signals, topic match\n"
        "  • Learns from your approvals — topics you like boost future discoveries\n"
        "  • Sunday auto-discovery sends top 10 for your review\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<b>⚡ ALERTS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "  • Queue below 5 → warning DM\n"
        "  • Feed fails 5x in a row → dead feed alert\n"
        "  • Approve article → shows if source has more in queue\n"
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>📈 CURRENT STATE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"  • Queue: {len(queue)} articles\n"
        f"  • Pending review: {len(pending)}\n"
        f"  • Total posted: {len(posted)}\n"
    )
    send_message(ANDY_CHAT_ID, guide)
    print("Sent full guide to Andy")


def extract_url_from_message(text: str) -> tuple:
    """Check if message is a URL submission. Returns (url, immediate) or (None, False).
    Prefix with ! for immediate posting."""
    text = text.strip()

    immediate = False
    if text.startswith("!"):
        immediate = True
        text = text[1:].strip()

    if text.startswith("http://") or text.startswith("https://"):
        url = text.split()[0]
        return url, immediate

    return None, False


def fetch_page_title(url: str) -> str:
    """Fetch the <title> tag from a URL."""
    try:
        from bs4 import BeautifulSoup
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")
            title_tag = soup.find("title")
            if title_tag:
                return title_tag.get_text(strip=True)
    except Exception as e:
        print(f"Error fetching title for {url}: {e}")
    return "Untitled"


def handle_url_submission(url: str, immediate: bool = False) -> bool:
    """Queue or immediately post a user-submitted URL."""
    if is_blocked(url):
        send_message(ANDY_CHAT_ID, f"⚠️ URL is from a blocked domain — skipping.")
        return False

    title = fetch_page_title(url)
    domain = urlparse(url).netloc.replace("www.", "")

    article = {
        "title": title,
        "link": url,
        "source": f"Andy via {domain}",
        "score": 0.9,
        "queued_at": datetime.now().isoformat(),
        "content_type": "essay",
        "user_submitted": True,
    }

    if immediate:
        posted_urls = load_json(POSTED_FILE, [])
        if normalize_url(url) in {normalize_url(u) for u in posted_urls}:
            send_message(ANDY_CHAT_ID, f"📌 Already shared — <b>{title}</b>")
            return False
        if post_to_channel(article):
            posted_urls.append(normalize_url(url))
            save_json(POSTED_FILE, posted_urls)
            send_message(ANDY_CHAT_ID, f"Posted: {title}\nfrom {domain}")
            return True
        else:
            send_message(ANDY_CHAT_ID, f"Failed to post: {title}")
            return False
    else:
        queue = load_json(QUEUE_FILE, [])

        queue_urls = {normalize_url(item["link"]) for item in queue}
        posted_urls = {normalize_url(u) for u in load_json(POSTED_FILE, [])}
        normalized = normalize_url(url)

        if normalized in queue_urls:
            send_message(ANDY_CHAT_ID, f"⏳ Already in queue — <b>{title}</b>")
            return False
        if normalized in posted_urls:
            send_message(ANDY_CHAT_ID, f"📌 Already shared — <b>{title}</b>")
            return False

        queue.insert(0, article)
        save_json(QUEUE_FILE, queue)
        send_message(ANDY_CHAT_ID, f"✅ Added to queue — posting next.\n\n<b>{title}</b>\n<i>{domain}</i>")
        return True


def discover_feed_url(blog_url: str) -> str | None:
    """Try to find an RSS/Atom feed URL from a blog URL."""
    from bs4 import BeautifulSoup

    parsed = urlparse(blog_url)
    domain = parsed.netloc.replace("www.", "")

    # Substack: always has /feed
    if "substack.com" in domain or ".substack.com" in blog_url:
        base = f"{parsed.scheme}://{parsed.netloc}"
        return f"{base}/feed"

    # Paragraph.xyz
    if "paragraph.com" in domain or "paragraph.xyz" in domain:
        # e.g. paragraph.com/@author -> paragraph.com/@author/feed
        path = parsed.path.rstrip("/")
        return f"{parsed.scheme}://{parsed.netloc}{path}/feed"

    # Try common feed paths
    base = f"{parsed.scheme}://{parsed.netloc}"
    common_paths = ["/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml"]
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

    for path in common_paths:
        try:
            resp = requests.get(base + path, headers=headers, timeout=5, allow_redirects=True)
            ct = resp.headers.get("content-type", "").lower()
            if resp.status_code == 200 and any(t in ct for t in ["xml", "rss", "atom"]):
                return base + path
        except Exception:
            continue

    # Try parsing the page for <link rel="alternate"> feed tags
    try:
        resp = requests.get(blog_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("link", rel="alternate"):
                link_type = link.get("type", "")
                if "rss" in link_type or "atom" in link_type or "xml" in link_type:
                    href = link.get("href", "")
                    if href:
                        if href.startswith("/"):
                            return base + href
                        return href
    except Exception:
        pass

    return None


def handle_addfeed(url: str):
    """Add a blog/source to the feed rotation via DM command."""
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "")

    # Check if already in FEEDS
    existing_domains = {urlparse(f["url"]).netloc.replace("www.", "") for f in FEEDS}
    if domain in existing_domains:
        send_message(ANDY_CHAT_ID, f"Already tracking — <b>{domain}</b>")
        return

    # Discover RSS feed
    feed_url = discover_feed_url(url)
    if not feed_url:
        send_message(ANDY_CHAT_ID, f"Couldn't find RSS feed for <b>{domain}</b>")
        return

    # Verify feed actually works
    feed = feedparser.parse(feed_url)
    if not feed.entries:
        send_message(ANDY_CHAT_ID, f"Found feed but it's empty — <b>{feed_url}</b>")
        return

    # Derive name from feed title or page title
    name = feed.feed.get("title", "").strip()
    if not name or len(name) > 40:
        name = fetch_page_title(url)
    # Clean common suffixes
    for suffix in [" | Substack", " - Substack", " on Substack", " RSS Feed", " RSS", " Feed"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    if not name or name == "Untitled":
        name = domain.split(".")[0].title()

    # Add to feeds.py
    add_source_to_feeds(name, feed_url, domain)

    # Also add to in-memory FEEDS so current session picks it up
    FEEDS.append({"name": name, "url": feed_url, "category": "Discovered"})

    entry_count = len(feed.entries)
    send_message(
        ANDY_CHAT_ID,
        f"Added to rotation — <b>{name}</b>\n"
        f"<i>{feed_url}</i>\n"
        f"{entry_count} articles in feed",
    )
    print(f"Added feed via DM: {name} -> {feed_url}")


def fetch_hacker_news(min_points: int = HN_MIN_POINTS, max_articles: int = 30,
                      extra_preferred: set = None) -> list:
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
            # Boost for preferred domains (hardcoded + learned from training)
            all_preferred = set(HN_PREFERRED_DOMAINS)
            if extra_preferred:
                all_preferred.update(extra_preferred)
            is_preferred = any(pref in url.lower() for pref in all_preferred)

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


def _update_feed_health(feed_url: str, success: bool, feed_name: str = ""):
    """Track consecutive feed failures. DM Andy after 5 failures."""
    health = load_json(FEED_HEALTH_FILE, {})
    entry = health.get(feed_url, {"consecutive_failures": 0, "warned": False})

    if success:
        entry["consecutive_failures"] = 0
        entry["warned"] = False
    else:
        entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
        entry["last_failure"] = datetime.now().isoformat()
        if entry["consecutive_failures"] >= 5 and not entry.get("warned"):
            name = feed_name or feed_url
            send_message(ANDY_CHAT_ID, f"Dead feed warning: <b>{name}</b> has failed {entry['consecutive_failures']}x in a row.")
            entry["warned"] = True

    health[feed_url] = entry
    save_json(FEED_HEALTH_FILE, health)


def fetch_feed(feed_info: dict) -> list:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }
        try:
            response = requests.get(feed_info["url"], headers=headers, timeout=10)
            feed = feedparser.parse(response.content)
        except Exception:
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

        _update_feed_health(feed_info["url"], success=len(entries) > 0 or len(feed.entries) > 0, feed_name=feed_info["name"])
        return entries

    except Exception as e:
        print(f"Error fetching {feed_info['name']}: {e}")
        _update_feed_health(feed_info["url"], success=False, feed_name=feed_info["name"])
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

Reply: <b>1</b>=👍  <b>0</b>=👎  <b>x</b>=🚫 block source"""
    
    return send_message(ANDY_CHAT_ID, message)


def process_responses():
    """Check for Andy's responses - handles batch responses like '1,0,1,1,0' or individual '1'/'0'"""
    training_log = load_json(TRAINING_LOG_FILE, [])
    pending = load_json(PENDING_REVIEW_FILE, [])

    updates = get_updates()

    for update in updates:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip().lower()

        if chat_id != ANDY_CHAT_ID:
            continue

        # Check for help command
        if text in ("/help", "help", "/start"):
            send_help_message()
            continue

        # Check if message is a URL submission
        raw_text = message.get("text", "").strip()
        url, immediate = extract_url_from_message(raw_text)
        if url:
            handle_url_submission(url, immediate)
            continue

        # Skip ratings if nothing is pending
        if not pending:
            continue

        # Handle batch response like "1,0,1,x,0" or "10x10"
        # 1 = approve (good), 0 = skip (bad), x = block entire source
        ratings = []

        # Remove any spaces or commas, normalize shortcuts
        clean_text = text.replace(",", "").replace(" ", "").replace("y", "1").replace("n", "0")

        for char in clean_text:
            if char == "1":
                ratings.append("good")
            elif char == "0":
                ratings.append("bad")
            elif char == "x":
                ratings.append("block")

        # Apply ratings to pending articles in order
        if ratings:
            for i, rating in enumerate(ratings):
                if i < len(pending):
                    pending[i]["rating"] = "bad" if rating == "block" else rating
                    training_log.append(pending[i])

                    if rating == "block":
                        # Permanently block this source and domain
                        add_rejected_source(pending[i].get("source", ""), pending[i].get("url", ""))
                        print(f"BLOCKED SOURCE: {pending[i].get('source', '')} — {pending[i].get('title', '')[:40]}...")
                    else:
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

# Content type classification patterns
CTA_PATTERNS = [
    "apply for", "apply now", "applications open", "accepting applications",
    "sign up for", "register for", "join our", "now hiring",
    "we're hiring", "job opening", "cohort", "save the date",
    "rsvp", "tickets available", "early bird",
]

NEWS_TITLE_PATTERNS = [
    "announces", "announcing", "launches", "launched", "released",
    "raises $", "funding round", "series a", "series b", "acquires",
    "has been", "is now", "update:", "breaking:",
]

EVENT_PATTERNS = [
    "conference 20", "summit 20", "meetup", "webinar",
    "workshop 20",
]

PRODUCT_PATTERNS = [
    "introducing:", "we built", "we shipped", "changelog",
    "new feature:", "product update", "beta launch",
    "show hn:",
]

ROUNDUP_PATTERNS = [
    "roundup", "reading list", "links (", "weekly dose",
    "what i'm reading", "classifieds",
]


def classify_title(title: str) -> str:
    """Classify title into content type.
    Returns: 'essay', 'cta', 'news', 'roundup', 'event', or 'product'."""
    title_lower = title.lower()

    for pattern in CTA_PATTERNS:
        if pattern in title_lower:
            return "cta"

    for pattern in EVENT_PATTERNS:
        if pattern in title_lower:
            return "event"

    for pattern in ROUNDUP_PATTERNS:
        if pattern in title_lower:
            return "roundup"

    for pattern in PRODUCT_PATTERNS:
        if pattern in title_lower:
            return "product"

    for pattern in NEWS_TITLE_PATTERNS:
        if pattern in title_lower:
            return "news"

    return "essay"


def fetch_article_summary(url: str, max_chars: int = 500) -> str:
    """Fetch first ~500 chars of article body text for content checking."""
    try:
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code != 200:
            return ""

        soup = BeautifulSoup(response.text, "html.parser")

        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()

        content = soup.find("article") or soup.find("main") or soup.find("body")
        if not content:
            return ""

        text = content.get_text(separator=" ", strip=True)
        return text[:max_chars]

    except Exception as e:
        print(f"Error fetching summary for {url}: {e}")
        return ""


def check_paywall(url: str) -> bool:
    """Check if an article is behind a paywall by fetching and inspecting the page.

    Returns True if paywalled, False if free/accessible.
    On network errors, returns False (benefit of the doubt).
    """
    try:
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        }
        response = requests.get(url, headers=headers, timeout=8)

        # 402 Payment Required or 403 Forbidden often = paywall
        if response.status_code in (402, 403):
            return True

        if response.status_code != 200:
            return False  # Can't tell, benefit of the doubt

        html_lower = response.text.lower()

        # Check raw HTML for paywall class names and data attributes
        html_paywall_signals = [
            "class=\"paywall\"", "class=\"paywall-", "id=\"paywall\"",
            "data-paywall", "class=\"subscriber-only\"",
            "class=\"premium-content\"", "class=\"locked-content\"",
            "class=\"gate\"", "data-gate",
            "paywall-content", "subscription-required",
        ]
        for signal in html_paywall_signals:
            if signal in html_lower:
                return True

        # Substack-specific: check meta tags for paywall indicator
        if "substack" in html_lower:
            # Substack paywalled posts have this in their JSON-LD or meta
            if '"isAccessibleForFree":false' in html_lower or '"isAccessibleForFree": false' in html_lower:
                return True
            if 'class="paywall-title"' in html_lower or 'class="paywall"' in html_lower:
                return True

        # Parse body text for paywall language
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav"]):
            tag.decompose()
        body_text = (soup.find("article") or soup.find("main") or soup.find("body"))
        if not body_text:
            return False

        text = body_text.get_text(separator=" ", strip=True).lower()

        # Check for paywall body signals
        for signal in PAYWALL_BODY_SIGNALS:
            if signal in text:
                return True

        # Suspiciously short content often means gated (< 200 chars of actual content)
        # But only flag this for sources we know gate content (Substack, etc.)
        if len(text) < 150 and ("substack" in url.lower() or "every.to" in url.lower()):
            return True

        return False

    except Exception as e:
        print(f"Paywall check error for {url}: {e}")
        return False  # Can't reach it, benefit of the doubt


def is_substantive_content(summary: str) -> bool:
    """Check if summary reads like an essay vs. a CTA/announcement."""
    if not summary or len(summary) < 100:
        return True  # Benefit of the doubt if we can't fetch

    summary_lower = summary.lower()

    cta_signals = [
        "apply now", "sign up", "register today", "limited spots",
        "deadline", "applications close", "join us", "enroll",
        "submit your", "application form",
    ]

    cta_count = sum(1 for signal in cta_signals if signal in summary_lower)
    return cta_count < 2


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

    # Calculate scores with Bayesian smoothing
    # Uses (good + prior) / (total + 2*prior) to pull small samples toward 0.5
    # This prevents a source with 1/1 good from outranking one with 9/10 good
    PRIOR = 2  # Equivalent to adding 2 fake "good" and 2 fake "bad" ratings
    scores = {}
    for source, stats in source_stats.items():
        total = stats["good"] + stats["bad"]
        if total > 0:
            scores[source] = (stats["good"] + PRIOR) / (total + 2 * PRIOR)
        else:
            scores[source] = 0.5  # Neutral for unknown

    return scores


def get_hn_liked_domains() -> set:
    """Build dynamic set of HN domains the user has historically liked."""
    training_log = load_json(TRAINING_LOG_FILE, [])
    domain_stats = {}

    for item in training_log:
        source = item.get("source", "")
        rating = item.get("rating", "")

        # Match HN source pattern: "HN (Npt) via domain.com"
        if "HN" not in source or "via" not in source:
            continue

        parts = source.split("via ")
        if len(parts) < 2:
            continue
        domain = parts[1].strip()

        if domain not in domain_stats:
            domain_stats[domain] = {"good": 0, "bad": 0}

        if rating == "good":
            domain_stats[domain]["good"] += 1
        elif rating == "bad":
            domain_stats[domain]["bad"] += 1

    # Include domains with more good than bad ratings
    liked = set()
    for domain, stats in domain_stats.items():
        if stats["good"] > stats["bad"] and stats["good"] >= 1:
            liked.add(domain)

    return liked


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

    # Content type penalty
    content_type = classify_title(title)
    if content_type == "cta":
        score -= 0.5
    elif content_type == "event":
        score -= 0.4
    elif content_type == "roundup":
        score -= 0.3
    elif content_type == "product":
        score -= 0.2
    elif content_type == "news":
        score -= 0.1

    # Clamp to 0-1
    return max(0.0, min(1.0, score))


def post_to_channel(article: dict) -> bool:
    """Post an article to the Telegram channel."""
    title = article["title"]
    link = article["link"]
    source = article["source"]

    # Last-check: don't post paywalled content
    if check_paywall(link):
        print(f"Paywall detected at post time — skipping: {title[:40]}...")
        return False

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
    posted_normalized = {normalize_url(u) for u in posted_urls}
    posted_sources = set()

    for article in scored:
        if posted_count >= 3:
            break

        # Skip if already posted
        normalized_link = normalize_url(article["link"])
        if normalized_link in posted_normalized:
            continue

        # Skip if we already posted from this source this cycle
        source = article.get("source", "")
        if source in posted_sources:
            continue

        if post_to_channel(article):
            print(f"Posted (score {article['score']:.2f}): {article['title'][:40]}...")
            posted_count += 1
            posted_sources.add(source)
            posted_urls.append(normalized_link)
            posted_normalized.add(normalized_link)
            save_json(POSTED_FILE, posted_urls)  # Save immediately after each post
            time.sleep(2)
        else:
            print(f"Failed to post: {article['title'][:40]}...")

    print(f"Posted {posted_count} articles to {TELEGRAM_CHANNEL_ID}")


# === SCHEDULED POSTING MODE ===

def prune_expired_articles(queue: list) -> list:
    """Remove expired news/HN articles from queue. Essays never expire."""
    now = datetime.now()
    pruned = []

    for article in queue:
        queued_at_str = article.get("queued_at")
        content_type = article.get("content_type", "essay")

        # Essays and legacy articles (no timestamp) never expire
        if content_type == "essay" or not queued_at_str:
            pruned.append(article)
            continue

        try:
            queued_at = datetime.fromisoformat(queued_at_str)
        except (ValueError, TypeError):
            pruned.append(article)
            continue

        age_days = (now - queued_at).days

        if content_type == "hn" and age_days > HN_EXPIRY_DAYS:
            print(f"Expired (HN, {age_days}d): {article['title'][:40]}...")
            continue
        elif content_type == "news" and age_days > NEWS_EXPIRY_DAYS:
            print(f"Expired (news, {age_days}d): {article['title'][:40]}...")
            continue

        pruned.append(article)

    if len(pruned) < len(queue):
        print(f"Pruned {len(queue) - len(pruned)} expired articles")

    return pruned


def build_queue():
    """Build a queue of scored articles ready for scheduled posting."""
    print(f"[{datetime.now()}] Building queue...")

    # Get source scores
    source_scores = get_source_scores()

    # Load existing queue and prune expired articles
    queue = load_json(QUEUE_FILE, [])
    queue = prune_expired_articles(queue)

    # Load URLs to skip (already posted or reviewed)
    posted_urls = {normalize_url(u) for u in load_json(POSTED_FILE, [])}
    training_log = load_json(TRAINING_LOG_FILE, [])
    reviewed_urls = {normalize_url(item.get("url", "")) for item in training_log}

    # Purge already-posted articles from the queue
    before = len(queue)
    queue = [item for item in queue if normalize_url(item["link"]) not in posted_urls]
    if before > len(queue):
        print(f"Purged {before - len(queue)} already-posted articles from queue")

    queue_urls = {normalize_url(item["link"]) for item in queue}
    skip_urls = queue_urls | posted_urls | reviewed_urls

    # Build feed type and category lookups
    feed_types = {}
    feed_categories = {}
    for feed in FEEDS:
        feed_types[feed["name"]] = feed.get("type", "essay")
        feed_categories[feed["name"]] = feed.get("category", "Uncategorized")

    # Collect new articles from RSS feeds
    articles = collect_articles_without_saving()

    # Also fetch from Hacker News (with dynamic preferred domains)
    hn_liked = get_hn_liked_domains()
    if hn_liked:
        print(f"Dynamic HN preferred domains: {', '.join(list(hn_liked)[:5])}{'...' if len(hn_liked) > 5 else ''}")
    hn_articles = fetch_hacker_news(extra_preferred=hn_liked)
    for hn_article in hn_articles:
        normalized = normalize_url(hn_article["link"])
        if normalized not in skip_urls:
            articles.append(hn_article)

    # Add canonical essays if needed
    if len(articles) < 20:
        for canon in CANONICAL_READINGS:
            canon_normalized = normalize_url(canon["url"])
            if canon_normalized not in skip_urls:
                articles.append({
                    "title": canon["title"],
                    "link": canon["url"],
                    "source": canon.get("author", "Canonical"),
                })

    # Track source and category counts for diversity
    MAX_PER_SOURCE = 2
    source_counts = {}
    category_counts = {}
    for item in queue:
        src = item.get("source", "")
        source_counts[src] = source_counts.get(src, 0) + 1
        cat = item.get("category", "Uncategorized")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Score and filter new articles
    for article in articles:
        normalized = normalize_url(article["link"])
        if normalized in skip_urls:
            continue

        source = article.get("source", "")
        link = article.get("link", "")

        # Skip paused sources
        if is_source_paused(source, link):
            continue

        # Skip rejected sources
        if is_source_rejected(source, link):
            continue

        # HN domain gate: only allow articles from preferred/indie domains
        if article.get("hn_points") and not article.get("hn_preferred", False):
            print(f"HN gate — non-preferred domain, skipping: {article['title'][:40]}...")
            continue

        # Source diversity limit
        if source_counts.get(source, 0) >= MAX_PER_SOURCE:
            continue

        # Determine category
        if article.get("hn_points"):
            category = "Tech"  # Default for HN articles
        else:
            category = feed_categories.get(source, "Uncategorized")

        # Category diversity limit
        if category_counts.get(category, 0) >= MAX_PER_CATEGORY:
            continue

        score = score_article(article, source_scores)

        # Body check: non-essay titles OR any HN article (HN titles are unreliable)
        content_type_label = classify_title(article.get("title", ""))
        if (content_type_label != "essay" or article.get("hn_points")) and score >= MIN_SCORE_THRESHOLD:
            summary = fetch_article_summary(link)
            if summary and not is_substantive_content(summary):
                score -= 0.3
                print(f"Body check failed: {article['title'][:40]}...")

        if score >= MIN_SCORE_THRESHOLD:
            # Paywall check — don't queue articles behind a paywall
            if check_paywall(link):
                print(f"Paywalled — skipping: {article['title'][:40]}...")
                skip_urls.add(normalized)
                continue

            article["score"] = score
            article["queued_at"] = datetime.now().isoformat()
            article["category"] = category

            # Determine content type for expiry
            if article.get("hn_points"):
                article["content_type"] = "hn"
            else:
                article["content_type"] = feed_types.get(source, "essay")

            queue.append(article)
            skip_urls.add(normalized)
            source_counts[source] = source_counts.get(source, 0) + 1
            category_counts[category] = category_counts.get(category, 0) + 1
            print(f"Queued (score {score:.2f}, {category}): {article['title'][:40]}...")

    # Sort queue by score
    queue.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Keep queue manageable (max 50 articles)
    queue = queue[:50]

    save_json(QUEUE_FILE, queue)
    print(f"Queue size: {len(queue)} articles")
    return len(queue)


def post_from_queue(count: int = 1):
    """Post one article from the queue (for scheduled posting).

    Runs once daily at 11 AM Chicago time.
    Enforces one article per source per day to ensure variety.
    """
    print(f"[{datetime.now()}] Posting {count} article(s) from queue...")

    queue = load_json(QUEUE_FILE, [])
    queue = prune_expired_articles(queue)

    if not queue:
        print("Queue is empty! Building queue first...")
        build_queue()
        queue = load_json(QUEUE_FILE, [])

    if not queue:
        print("No articles available to post")
        return 0

    posted_count = 0
    posted_urls = load_json(POSTED_FILE, [])
    posted_normalized = {normalize_url(u) for u in posted_urls}

    # Load sources already posted TODAY (resets at midnight Chicago time)
    daily_sources = load_daily_sources()
    print(f"Sources already posted today: {len(daily_sources)}")

    remaining_queue = []

    for article in queue:
        if posted_count >= count:
            remaining_queue.append(article)
            continue

        source = article.get("source", "")
        link = article.get("link", "")
        normalized_link = normalize_url(link)

        # Skip if already posted (prevent duplicates)
        if normalized_link in posted_normalized:
            print(f"Skipping (already posted): {article['title'][:40]}...")
            continue  # Don't add back to queue - it's already posted

        # Re-check blocked domains/keywords (catches stale queue entries)
        if is_blocked(link, article.get("title", "")):
            print(f"Skipping (now blocked): {article['title'][:40]}...")
            continue

        # Skip if source is temporarily paused
        if is_source_paused(source, link):
            print(f"Skipping (paused until cooldown expires): {source}")
            remaining_queue.append(article)
            continue

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
            save_json(POSTED_FILE, posted_urls)  # Save immediately to prevent duplicates if crash
            time.sleep(2)
        else:
            print(f"Failed to post: {article['title'][:40]}...")
            remaining_queue.append(article)

    save_json(QUEUE_FILE, remaining_queue)

    print(f"Posted {posted_count} articles. Queue remaining: {len(remaining_queue)}")

    # Queue depletion warning
    if len(remaining_queue) < 5:
        send_message(ANDY_CHAT_ID, f"Low queue: only {len(remaining_queue)} articles remaining. Consider submitting URLs or running discovery.")

    return posted_count


# === REVIEW MODE (Human-in-the-loop) ===

def send_for_channel_review(article: dict, num: int = 1) -> bool:
    """Send an article to Andy for review before posting to channel."""
    title = article["title"]
    link = article["link"]
    source = article["source"]
    score = article.get("score", 0)

    message = f"""🔍 <b>Review #{num}</b>

<b>{title}</b>

{link}

<i>— {source}</i>
<i>Score: {score:.2f}</i>

Reply: <b>1</b>=✅ Post  <b>0</b>=❌ Skip  <b>x</b>=🚫 Block source"""

    return send_message(ANDY_CHAT_ID, message)


def process_review_responses():
    """Process Andy's responses to review requests.

    Handles both article reviews (pending_review.json) and discovery source
    reviews (pending_discovery_review.json) in a single pass over Telegram
    updates. Article reviews take priority; if none are pending, ratings
    are applied to discovery reviews instead.
    """
    pending = load_json(PENDING_REVIEW_FILE, [])
    approved = load_json(APPROVED_FILE, [])
    training_log = load_json(TRAINING_LOG_FILE, [])

    # Also load discovery reviews
    discovery_pending_file = DATA_DIR / "pending_discovery_review.json"
    discovery_pending = load_json(discovery_pending_file, [])

    updates = get_updates()
    processed = 0

    for update in updates:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip().lower()

        if chat_id != ANDY_CHAT_ID:
            continue

        # Check for help command
        if text in ("/help", "help", "/start"):
            send_help_message()
            continue

        # Check if message is a URL submission
        raw_text = message.get("text", "").strip()
        url, immediate = extract_url_from_message(raw_text)
        if url:
            handle_url_submission(url, immediate)
            continue

        # Parse ratings: 1=approve, 0=skip, x=block source
        ratings = []
        clean_text = text.replace(",", "").replace(" ", "").replace("y", "1").replace("n", "0")

        for char in clean_text:
            if char == "1":
                ratings.append("good")
            elif char == "0":
                ratings.append("bad")
            elif char == "x":
                ratings.append("block")

        if not ratings:
            continue

        # Apply ratings to article reviews first, then discovery reviews
        if pending:
            for i, rating in enumerate(ratings):
                if i < len(pending):
                    article = pending[i]
                    article["rating"] = "bad" if rating == "block" else rating
                    training_log.append(article)

                    if rating == "good":
                        # Add article to queue (will be posted in normal rotation)
                        queue = load_json(QUEUE_FILE, [])
                        new_article = {
                            "title": article["title"],
                            "link": article["url"],
                            "source": article["source"],
                            "score": 0.7,
                        }
                        # Insert at random position (not front, not last)
                        if len(queue) > 2:
                            insert_pos = random.randint(1, len(queue) - 1)
                            queue.insert(insert_pos, new_article)
                        else:
                            queue.append(new_article)
                        save_json(QUEUE_FILE, queue)

                        # Add source to feeds.py (now a trusted source)
                        if article.get("feed_url"):
                            add_source_to_feeds(
                                name=article["source"],
                                url=article["feed_url"],
                                domain=article.get("domain", "")
                            )

                        print(f"APPROVED: {article['title'][:40]}... (added to queue, source added to feeds)")
                    elif rating == "block":
                        # Permanently block source and domain
                        add_rejected_source(article["source"], article.get("url", ""))
                        print(f"BLOCKED SOURCE: {article.get('source', '')} — {article['title'][:40]}...")
                    else:
                        # Just skip this article, don't punish the source
                        print(f"SKIPPED: {article['title'][:40]}...")

                    processed += 1

            # Remove processed from pending
            pending = pending[len(ratings):]

        elif discovery_pending:
            # No article reviews pending — apply ratings to discovery source reviews
            for i, rating in enumerate(ratings):
                if i < len(discovery_pending):
                    source = discovery_pending[i]
                    source_name = source.get("name", "Unknown")
                    source_domain = source.get("domain", "")

                    if rating == "good" and source.get("url"):
                        # Add discovered source to feeds.py
                        try:
                            from discover import auto_add_top_sources_single
                            auto_add_top_sources_single(source)
                            send_message(ANDY_CHAT_ID, f"✅ Added source: {source_name} ({source_domain})")
                            print(f"DISCOVERY APPROVED: {source_name} ({source_domain})")
                        except Exception as e:
                            print(f"Error adding source {source_name}: {e}")
                    elif rating == "block":
                        add_rejected_source(source_name, f"https://{source_domain}")
                        send_message(ANDY_CHAT_ID, f"🚫 Blocked source: {source_name}")
                        print(f"DISCOVERY BLOCKED: {source_name} ({source_domain})")
                    else:
                        print(f"DISCOVERY SKIPPED: {source_name} ({source_domain})")

                    processed += 1

            # Remove processed from discovery pending
            discovery_pending = discovery_pending[len(ratings):]

    # Save state
    save_json(PENDING_REVIEW_FILE, pending)
    save_json(APPROVED_FILE, approved)
    save_json(TRAINING_LOG_FILE, training_log)
    save_json(discovery_pending_file, discovery_pending)

    # Clear processed updates
    if updates:
        last_update_id = updates[-1]["update_id"]
        get_updates(offset=last_update_id + 1)

    return processed


def post_approved_to_channel(count: int = 1):
    """Post approved articles to the channel."""
    approved = load_json(APPROVED_FILE, [])
    posted_urls = load_json(POSTED_FILE, [])
    daily_sources = load_daily_sources()

    if not approved:
        print("No approved articles to post")
        return 0

    posted_count = 0
    remaining = []
    posted_normalized = {normalize_url(u) for u in posted_urls}

    for article in approved:
        if posted_count >= count:
            remaining.append(article)
            continue

        source = article.get("source", "")
        link = article.get("link", "")
        normalized_link = normalize_url(link)

        # Skip if already posted (prevent duplicates)
        if normalized_link in posted_normalized:
            print(f"Skipping (already posted): {article['title'][:40]}...")
            continue

        # Check daily source limit
        if source in daily_sources:
            print(f"Skipping (already posted today): {source}")
            remaining.append(article)
            continue

        # Check if paused
        if is_source_paused(source, link):
            print(f"Skipping (paused): {source}")
            remaining.append(article)
            continue

        # Check if rejected
        if is_source_rejected(source, link):
            print(f"Skipping (rejected source): {source}")
            remaining.append(article)
            continue

        if post_to_channel(article):
            print(f"Posted to channel: {article['title'][:50]}...")
            posted_count += 1
            daily_sources.add(source)
            save_daily_source(source)
            posted_urls.append(normalize_url(link))
            save_json(POSTED_FILE, posted_urls)  # Save immediately to prevent duplicates if crash
            time.sleep(2)
        else:
            print(f"Failed to post: {article['title'][:40]}...")
            remaining.append(article)

    save_json(APPROVED_FILE, remaining)

    print(f"Posted {posted_count} approved articles to channel")
    return posted_count


def fetch_from_discovered_sources():
    """Fetch articles from discovered sources - disabled due to Substack blocking cloud IPs."""
    # Substack blocks GitHub Actions IPs, so this feature is disabled for now
    # To re-enable: run the bot locally instead of on GitHub Actions
    return []


def run_review_mode(should_post: bool = True):
    """Review mode workflow:
    1. Process any responses from Andy (approve/reject discovered sources)
    2. Post from regular queue (only if should_post=True)
    3. Send NEW discovered sources for review
    """
    print(f"[{datetime.now()}] Review mode running...")

    # Step 1: Process Andy's responses to discovered source reviews
    processed = process_review_responses()
    if processed > 0:
        print(f"Processed {processed} review responses")

    # Step 2: Post from regular queue (only during posting hours)
    if should_post:
        build_queue()
        posted = post_from_queue(count=1)
    else:
        print("Not a posting hour - skipping channel post")

    # Step 3: Report pending review status
    pending = load_json(PENDING_REVIEW_FILE, [])
    discovery_pending = load_json(DATA_DIR / "pending_discovery_review.json", [])

    if pending:
        print(f"Article reviews pending: {len(pending)} — waiting for your 1/0/x response")
    elif discovery_pending:
        print(f"Discovery source reviews pending: {len(discovery_pending)} — respond 1/0/x in DMs")
    else:
        print("No reviews pending")

    # Step 4: Send weekly stats on Mondays
    chicago_now = datetime.now(ZoneInfo("America/Chicago"))
    if chicago_now.weekday() == 0:  # Monday
        print("Monday — sending weekly stats")
        send_weekly_stats()


def send_weekly_stats():
    """Send a weekly stats summary to Andy via Telegram DM."""
    from collections import Counter

    queue = load_json(QUEUE_FILE, [])
    posted_urls = load_json(POSTED_FILE, [])
    training_log = load_json(TRAINING_LOG_FILE, [])

    # This week's ratings
    now = datetime.now()
    week_ago = now - timedelta(days=7)

    week_ratings = []
    for item in training_log:
        sent_at = item.get("sent_at", "")
        try:
            if sent_at and datetime.fromisoformat(sent_at) > week_ago:
                week_ratings.append(item)
        except (ValueError, TypeError):
            continue

    good_count = len([p for p in week_ratings if p.get("rating") == "good"])
    bad_count = len([p for p in week_ratings if p.get("rating") == "bad"])

    # Queue stats
    queue_categories = Counter(a.get("category", "Uncategorized") for a in queue)

    # Source scores
    source_scores = get_source_scores()
    top_sources = sorted(source_scores.items(), key=lambda x: x[1], reverse=True)[:5]

    # Sources with 0 articles in queue
    all_feed_names = {f["name"] for f in FEEDS}
    queue_feed_names = {a.get("source", "") for a in queue}
    empty_sources_count = len(all_feed_names - queue_feed_names)

    # Build message
    msg = "<b>Weekly Brain Candy Stats</b>\n\n"

    msg += f"<b>Queue:</b> {len(queue)} articles\n"
    msg += f"<b>Total posted:</b> {len(posted_urls)}\n"
    msg += f"<b>This week:</b> {good_count} approved, {bad_count} skipped\n\n"

    if queue_categories:
        msg += "<b>Queue by Category:</b>\n"
        for cat, count in queue_categories.most_common(8):
            msg += f"  {cat}: {count}\n"

    msg += f"\n<b>Top Sources (trust):</b>\n"
    for source, score in top_sources:
        msg += f"  {source}: {score:.2f}\n"

    msg += f"\n<b>Sources with 0 queued:</b> {empty_sources_count}"

    send_message(ANDY_CHAT_ID, msg)
    print("Weekly stats sent to Andy.")


def _handle_undo(action: dict):
    """Undo the last rating action."""
    article = action["article"]
    rating = action["type"]
    source = action.get("source", "")
    url = action.get("url", "")
    title = article.get("title", article.get("source", "Unknown"))[:50]

    # Remove from training log (last matching entry)
    training_log = load_json(TRAINING_LOG_FILE, [])
    for i in range(len(training_log) - 1, -1, -1):
        entry = training_log[i]
        entry_url = entry.get("url", entry.get("link", ""))
        if normalize_url(entry_url) == normalize_url(url):
            training_log.pop(i)
            break
    save_json(TRAINING_LOG_FILE, training_log)

    # If it was a block, remove from rejected_sources
    if rating == "block":
        rejected = load_json(REJECTED_SOURCES_FILE, {"sources": [], "domains": []})
        if source and source in rejected["sources"]:
            rejected["sources"].remove(source)
        if url:
            domain = urlparse(url).netloc.replace("www.", "")
            if domain and domain in rejected["domains"]:
                rejected["domains"].remove(domain)
        save_json(REJECTED_SOURCES_FILE, rejected)

    # Restore article to pending review
    pending = load_json(PENDING_REVIEW_FILE, [])
    article.pop("rating", None)
    pending.insert(0, article)
    save_json(PENDING_REVIEW_FILE, pending)

    action_label = {"good": "approval", "bad": "skip", "block": "block"}.get(rating, rating)
    send_message(ANDY_CHAT_ID, f"Undid {action_label} of <b>{title}</b>. Restored to pending.")
    print(f"Undid {action_label}: {title}")


def _suggest_more_from_source(article: dict):
    """After approving an article, suggest more from the same source if available."""
    source = article.get("source", "")
    if not source:
        return

    queue = load_json(QUEUE_FILE, [])
    same_source = [a for a in queue if a.get("source") == source]
    if same_source:
        send_message(ANDY_CHAT_ID, f"FYI: <b>{source}</b> has {len(same_source)} more article(s) in your queue.")


def listen_for_dms():
    """Continuously listen for Telegram DMs and respond instantly.

    Handles: /help, URL submissions, and article ratings in real time.
    Does NOT post to the channel — that stays on the GitHub Actions schedule.
    """
    print("🧠 Brain Candy — DM Listener")
    print("Listening for commands... (Ctrl+C to stop)\n")

    last_actions = []  # Stack for /undo (max 10)

    while True:
        try:
            updates = get_updates()

            for update in updates:
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                text = message.get("text", "").strip().lower()

                if chat_id != ANDY_CHAT_ID:
                    continue

                # Help command
                if text in ("/help", "help", "/start"):
                    send_help_message()
                    continue

                # Guide command (full reference)
                if text in ("/guide", "guide"):
                    send_guide_message()
                    continue

                # Stats command
                if text in ("/stats", "stats"):
                    send_weekly_stats()
                    continue

                # Undo command
                if text in ("/undo", "undo"):
                    if not last_actions:
                        send_message(ANDY_CHAT_ID, "Nothing to undo.")
                    else:
                        action = last_actions.pop()
                        _handle_undo(action)
                    continue

                # Add feed command
                if text.startswith("/addfeed") or text.startswith("addfeed"):
                    raw_text = message.get("text", "").strip()
                    parts = raw_text.split(None, 1)
                    if len(parts) < 2 or not parts[1].startswith("http"):
                        send_message(ANDY_CHAT_ID, "Usage: <code>/addfeed https://blog.example.com</code>")
                    else:
                        handle_addfeed(parts[1].strip())
                    continue

                # URL submission
                raw_text = message.get("text", "").strip()
                url, immediate = extract_url_from_message(raw_text)
                if url:
                    handle_url_submission(url, immediate)
                    continue

                # Process ratings for pending articles
                pending = load_json(PENDING_REVIEW_FILE, [])
                training_log = load_json(TRAINING_LOG_FILE, [])

                if not pending:
                    continue

                ratings = []
                clean_text = text.replace(",", "").replace(" ", "").replace("y", "1").replace("n", "0")

                for char in clean_text:
                    if char == "1":
                        ratings.append("good")
                    elif char == "0":
                        ratings.append("bad")
                    elif char == "x":
                        ratings.append("block")

                if ratings:
                    for i, rating in enumerate(ratings):
                        if i < len(pending):
                            article = pending[i]
                            article["rating"] = "bad" if rating == "block" else rating
                            training_log.append(article)

                            if rating == "good":
                                # If it's a discovered source, add to queue + feeds
                                if article.get("feed_url"):
                                    queue = load_json(QUEUE_FILE, [])
                                    new_article = {
                                        "title": article["title"],
                                        "link": article.get("url", article.get("link", "")),
                                        "source": article["source"],
                                        "score": 0.7,
                                    }
                                    if len(queue) > 2:
                                        insert_pos = random.randint(1, len(queue) - 1)
                                        queue.insert(insert_pos, new_article)
                                    else:
                                        queue.append(new_article)
                                    save_json(QUEUE_FILE, queue)

                                    add_source_to_feeds(
                                        name=article["source"],
                                        url=article["feed_url"],
                                        domain=article.get("domain", "")
                                    )
                                print(f"👍 APPROVED: {article.get('title', '')[:50]}")
                            elif rating == "block":
                                add_rejected_source(article.get("source", ""), article.get("url", ""))
                                send_message(ANDY_CHAT_ID, f"🚫 Blocked: {article.get('source', '')}")
                                print(f"🚫 BLOCKED: {article.get('source', '')}")
                            else:
                                print(f"⏭ SKIPPED: {article.get('title', '')[:50]}")

                            # Track for /undo
                            last_actions.append({
                                "type": rating,
                                "article": article,
                                "source": article.get("source", ""),
                                "url": article.get("url", article.get("link", "")),
                            })
                            if len(last_actions) > 10:
                                last_actions = last_actions[-10:]

                            # "More like this" suggestion on approve
                            if rating == "good":
                                _suggest_more_from_source(article)

                    pending = pending[len(ratings):]
                    save_json(PENDING_REVIEW_FILE, pending)
                    save_json(TRAINING_LOG_FILE, training_log)

            # Acknowledge processed updates
            if updates:
                last_update_id = updates[-1]["update_id"]
                get_updates(offset=last_update_id + 1)

        except KeyboardInterrupt:
            print("\n\nListener stopped.")
            break
        except Exception as e:
            print(f"Error: {e}")

        time.sleep(3)


if __name__ == "__main__":
    run_training()
