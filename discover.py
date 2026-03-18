"""
Source Discovery Module for Brain Candy Bot

Discovers new sources through:
1. Substack Recommendations - scrapes "recommended" writers from existing Substacks
2. Hacker News Domain Mining - tracks domains that frequently appear in high-scoring posts
3. Enhanced HN Mining - lower threshold + topic-specific searches
4. Topic-Based Web Search - DuckDuckGo searches for interest-related blogs
5. Blogroll Mining - scrape links pages from trusted sources
6. Lobsters Mining - curated tech link aggregator
7. Reddit Mining - quality intellectual subreddits
"""

import requests
import json
import re
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from bs4 import BeautifulSoup

# Load .env file if it exists
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _key, _val = _line.split("=", 1)
            os.environ.setdefault(_key.strip(), _val.strip())

# Telegram config
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANDY_CHAT_ID = "1023849161"  # Send discoveries to Andy for review

# Files
DATA_DIR = Path(__file__).parent
DISCOVERED_FILE = DATA_DIR / "discovered_sources.json"
FEEDS_FILE = DATA_DIR / "feeds.py"

# Hacker News API
HN_API_URL = "https://hn.algolia.com/api/v1/search"

# Domains to ignore (news sites, social media, corporate sites, etc.)
# NOTE: is_ignored_domain() also checks parent domains, so "microsoft.com"
# blocks news.microsoft.com, blog.microsoft.com, etc.
IGNORE_DOMAINS = {
    # Social media
    "twitter.com", "x.com", "youtube.com", "reddit.com", "old.reddit.com",
    "medium.com", "linkedin.com", "facebook.com", "instagram.com", "tiktok.com",
    "threads.net", "mastodon.social", "bsky.app",

    # Code/developer sites
    "github.com", "github.blog", "gitlab.com", "stackoverflow.com",
    "docs.google.com", "drive.google.com", "notion.so", "figma.com",

    # News/media orgs (not essay writers)
    "nytimes.com", "wsj.com", "bloomberg.com", "ft.com", "economist.com",
    "bbc.com", "bbc.co.uk", "cnn.com", "reuters.com", "apnews.com",
    "theguardian.com", "washingtonpost.com", "news.ycombinator.com",
    "techmeme.com", "techcrunch.com", "theverge.com", "wired.com",
    "arstechnica.com", "engadget.com", "vice.com", "vox.com",
    "propublica.org", "theatlantic.com", "newyorker.com", "slate.com",
    "salon.com", "thedailybeast.com", "huffpost.com", "buzzfeed.com",
    "nbcnews.com", "cbsnews.com", "foxnews.com", "abcnews.go.com",
    "politico.com", "thehill.com", "axios.com", "semafor.com",
    "businessinsider.com", "fortune.com", "cnbc.com", "qz.com",
    "thenation.com", "motherjones.com", "theintercept.com",

    # Corporate sites (blocks all subdomains too)
    "apple.com", "google.com", "microsoft.com", "amazon.com", "meta.com",
    "openai.com", "anthropic.com", "nvidia.com", "intel.com", "amd.com",
    "stripe.com", "shopify.com", "salesforce.com", "oracle.com",
    "ibm.com", "cisco.com", "vmware.com", "adobe.com", "spotify.com",
    "ycombinator.com",

    # Platforms (the platform itself, not individual users)
    "substack.com",  # blocks substack.com and open.substack.com but NOT writer.substack.com
    "ghost.io", "wordpress.com", "blogger.com", "tumblr.com",
    "beehiiv.com",

    # Academic/reference
    "arxiv.org", "wikipedia.org", "wikimedia.org", "archive.org",
    "scholar.google.com", "semanticscholar.org", "researchgate.net",

    # Other non-blog sites
    "imgur.com", "gfycat.com", "giphy.com", "pastebin.com",
    "dropbox.com", "wetransfer.com", "mega.nz",
    "v.redd.it", "i.redd.it", "preview.redd.it",
}

# Subdomains that are OK even though parent is blocked
# (individual writers on platforms)
SUBDOMAIN_ALLOWLIST_PATTERNS = {
    "substack.com",  # writer.substack.com is OK, but substack.com and open.substack.com are not
}


def is_ignored_domain(domain: str) -> bool:
    """Check if domain should be ignored, including subdomain matching.

    'news.microsoft.com' is blocked because 'microsoft.com' is in IGNORE_DOMAINS.
    'writer.substack.com' is ALLOWED because substack.com is in SUBDOMAIN_ALLOWLIST_PATTERNS.
    'substack.com' and 'open.substack.com' are blocked (not individual writers).
    """
    domain = domain.lower().strip()

    # Direct match
    if domain in IGNORE_DOMAINS:
        return True

    # Check parent domain matching (news.microsoft.com -> microsoft.com)
    parts = domain.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[i:])
        if parent in IGNORE_DOMAINS:
            # Check if this is an allowed subdomain pattern (individual writer on platform)
            if parent in SUBDOMAIN_ALLOWLIST_PATTERNS:
                # Only allow if it's a real individual writer subdomain (exactly one level above)
                # writer.substack.com = OK (3 parts), open.substack.com = block it too
                subdomain_part = ".".join(parts[:i])
                # Block platform-owned subdomains
                platform_subdomains = {"open", "www", "blog", "news", "support", "help", "api", "app", "mail", "about", "press", "media", "jobs", "careers"}
                if subdomain_part in platform_subdomains:
                    return True
                # It's a real writer subdomain, allow it
                return False
            # Parent is blocked, no allowlist exception
            return True

    return False


# Keywords that indicate non-essay content (newsletters, roundups, news)
NON_ESSAY_NAME_KEYWORDS = {
    "weekly", "daily", "digest", "roundup", "newsletter", "briefing",
    "morning", "evening", "recap", "update", "bulletin", "dispatch",
    "business", "fintech", "insider", "transcript", "earnings",
    "mainstream", "institutional",
}

# Keywords in sample titles that suggest essay/opinion content
ESSAY_TITLE_KEYWORDS = [
    "why", "how", "against", "defense of", "case for", "case against",
    "lessons", "what i learned", "thinking about", "notes on", "reflections",
    "paradox", "myth", "essay", "philosophy", "theory", "framework",
    "principles", "mental model", "first principles",
]

# User's interest topics for relevance scoring
INTEREST_KEYWORDS = [
    "crypto", "defi", "ethereum", "bitcoin", "blockchain", "token",
    "ai", "alignment", "intelligence", "machine learning", "llm", "gpt",
    "philosophy", "epistemology", "rationality", "consciousness", "ethics",
    "startup", "venture", "founder", "vc", "fundrais",
    "market", "macro", "fed", "inflation", "rates", "economy",
    "progress", "acceleration", "technology", "innovation", "civilization",
    "culture", "psychology", "identity", "meaning", "society",
    "mechanism design", "game theory", "incentive", "governance",
]

# Browser headers for scraping
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

# Interest-based search queries for topic discovery
# Queries designed to find actual essay writers, not listicles
TOPIC_QUERIES = [
    "crypto DeFi mechanism design essay site:substack.com",
    "AI alignment risk essay site:substack.com",
    "philosophy rationality consciousness essay site:substack.com",
    "macro economics contrarian investing essay site:substack.com",
    "startup venture capital founder essay site:substack.com",
    "progress studies techno optimism essay site:substack.com",
    "systems thinking complexity essay blog",
    "ethereum L2 rollup analysis essay",
    "effective altruism longtermism essay blog",
    "game theory incentive design essay",
    "culture technology society essay substack",
    "mental models decision making essay blog",
]

# Trusted blogroll pages to mine
BLOGROLL_SOURCES = [
    {"url": "https://gwern.net/links", "name": "Gwern"},
    {"url": "https://www.ribbonfarm.com/now-reading/", "name": "Ribbonfarm"},
    {"url": "https://slatestarcodex.com/blogroll/", "name": "SlateStarCodex"},
    {"url": "https://www.benkuhn.net/blogroll/", "name": "Ben Kuhn"},
    {"url": "https://nintil.com/blogroll", "name": "Nintil"},
    {"url": "https://patrickcollison.com/bookshelf", "name": "Patrick Collison"},
    {"url": "https://nadia.xyz/", "name": "Nadia Asparouhova"},
]

# Lobsters tags matching user interests
LOBSTERS_TAGS = ["ai", "philosophy", "culture", "practices", "compsci", "economics"]

# Quality subreddits for discovery
QUALITY_SUBREDDITS = [
    "slatestarcodex",
    "TheMotte",
    "CryptoCurrency",
    "ethereum",
    "philosophy",
    "DepthHub",
    "TrueReddit",
]

# HN topic queries for enhanced mining
HN_TOPIC_QUERIES = [
    "crypto defi essay",
    "AI alignment essay",
    "systems thinking",
    "philosophy technology essay",
    "startup essay founder",
    "macro economics essay",
]


# === SHARED UTILITIES ===

def find_rss_feed(domain: str) -> str:
    """Try common RSS feed URL patterns for a domain. Returns first working URL or None."""
    if "substack.com" in domain:
        return f"https://{domain}/feed"

    patterns = ["/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml", "/feed/atom"]

    for pattern in patterns:
        test_url = f"https://{domain}{pattern}"
        try:
            r = requests.get(test_url, headers=HEADERS, timeout=5, allow_redirects=True)
            if r.status_code == 200:
                content_type = r.headers.get("content-type", "").lower()
                body_start = r.text[:500].lower()
                if "xml" in content_type or "<rss" in body_start or "<feed" in body_start or "<?xml" in body_start:
                    return test_url
        except Exception:
            continue

    return None


def _normalize_domain_for_dedup(domain: str) -> str:
    """Normalize domain for dedup matching.
    scuttleblurb.substack.com -> scuttleblurb (to match scuttleblurb.com in feeds)
    writer.substack.com -> writer
    myblog.com -> myblog
    """
    domain = domain.lower().replace("www.", "")
    # Strip common platform suffixes to match cross-domain
    for suffix in [".substack.com", ".ghost.io", ".wordpress.com", ".beehiiv.com", ".mirror.xyz"]:
        if domain.endswith(suffix):
            return domain.replace(suffix, "")
    # Strip TLD for bare domains
    parts = domain.split(".")
    if len(parts) >= 2:
        return parts[0]
    return domain


def deduplicate_sources(raw_sources: list) -> list:
    """Merge and deduplicate discoveries from all methods against all known sources."""
    from bot import load_json
    from bot import POSTED_FILE, REJECTED_SOURCES_FILE

    existing_domains = get_existing_domains()
    discovered = load_discovered()
    seen_domains = set(discovered.get("seen_domains", []))

    # Build normalized set from existing feeds for cross-domain matching
    existing_normalized = set()
    for d in existing_domains:
        existing_normalized.add(_normalize_domain_for_dedup(d))
        existing_normalized.add(d)  # Keep exact match too

    # Rejected domains
    rejected = load_json(REJECTED_SOURCES_FILE, [])
    rejected_domains = set()
    for r in rejected:
        if isinstance(r, str):
            parsed = urlparse(r) if r.startswith("http") else None
            if parsed and parsed.netloc:
                rejected_domains.add(parsed.netloc.replace("www.", ""))
            else:
                rejected_domains.add(r.lower())
        elif isinstance(r, dict):
            if r.get("domain"):
                rejected_domains.add(r["domain"])
            if r.get("url"):
                parsed = urlparse(r["url"])
                rejected_domains.add(parsed.netloc.replace("www.", ""))

    skip_domains = existing_domains | seen_domains | rejected_domains

    # Deduplicate and merge
    by_domain = {}
    for source in raw_sources:
        domain = source.get("domain", "").lower()
        if not domain:
            continue

        # Skip if ignored domain
        if is_ignored_domain(domain):
            continue

        # Skip if already known (exact or normalized match)
        if domain in skip_domains:
            continue
        if _normalize_domain_for_dedup(domain) in existing_normalized:
            continue

        if domain in by_domain:
            # Merge evidence
            existing = by_domain[domain]
            existing["evidence"].update(source.get("evidence", {}))
            existing.setdefault("methods", [])
            method = source.get("source_type", "unknown")
            if method not in existing["methods"]:
                existing["methods"].append(method)
        else:
            source.setdefault("methods", [source.get("source_type", "unknown")])
            source.setdefault("evidence", {})
            by_domain[domain] = source

    return list(by_domain.values())


# Cache for dynamic keywords (computed once per process)
_dynamic_keywords_cache = None

def get_dynamic_interest_keywords() -> list:
    """Extract topic keywords from articles Andy has rated 'good'.

    Returns top 30 keywords by frequency, giving discovery scoring
    a personalized signal based on actual taste rather than static lists.
    """
    global _dynamic_keywords_cache
    if _dynamic_keywords_cache is not None:
        return _dynamic_keywords_cache

    stopwords = {
        "the", "a", "an", "of", "to", "in", "and", "is", "for", "on", "with",
        "that", "this", "it", "from", "by", "at", "as", "or", "be", "was",
        "are", "were", "been", "has", "have", "had", "will", "would", "could",
        "should", "not", "but", "if", "so", "than", "then", "no", "do", "did",
        "its", "you", "your", "we", "our", "my", "their", "his", "her", "all",
        "each", "every", "any", "about", "more", "how", "why", "what", "when",
        "where", "who", "which", "can", "new", "one", "two", "just", "like",
        "into", "over", "also", "back", "after", "use", "way", "than", "them",
        "these", "some", "other", "most", "very", "don", "get", "got", "need",
        "been", "being", "does", "much", "well", "too", "out", "still", "only",
    }

    training_log_file = DATA_DIR / "training_log.json"
    if not training_log_file.exists():
        _dynamic_keywords_cache = []
        return []

    try:
        with open(training_log_file, "r") as f:
            training_log = json.load(f)
    except Exception:
        _dynamic_keywords_cache = []
        return []

    # Extract words from titles of approved articles
    word_counts = {}
    for entry in training_log:
        if entry.get("rating") != "good":
            continue
        title = entry.get("title", "")
        words = re.findall(r'[a-z]+', title.lower())
        for word in words:
            if len(word) >= 3 and word not in stopwords:
                word_counts[word] = word_counts.get(word, 0) + 1

    # Return top 30 by frequency (minimum 2 occurrences to avoid noise)
    keywords = sorted(
        [(w, c) for w, c in word_counts.items() if c >= 2],
        key=lambda x: x[1],
        reverse=True,
    )[:30]

    _dynamic_keywords_cache = [w for w, c in keywords]
    return _dynamic_keywords_cache


def score_discovered_source(source: dict) -> float:
    """Score a discovered source. Heavily favors individual essay writers over news/newsletters.

    Scoring philosophy:
    - Individual blogs writing essays on user's interest topics = high score
    - News orgs, corporate blogs, newsletters, roundups = penalized hard
    - Popularity signals (HN points) are secondary to content quality signals
    """
    score = 0.0
    evidence = source.get("evidence", {})
    methods = source.get("methods", [])
    domain = source.get("domain", "")
    name = source.get("name", "").lower()

    # Collect all sample titles for analysis
    all_titles = []
    for key in ["sample_titles", "link_text"]:
        all_titles.extend(evidence.get(key, []))
    titles_lower = " ".join(t.lower() for t in all_titles)

    # === HARD PENALTIES (filter out garbage) ===

    # Penalty: Name contains newsletter/roundup keywords
    name_lower = name.lower()
    for keyword in NON_ESSAY_NAME_KEYWORDS:
        if keyword in name_lower:
            score -= 40
            break

    # Penalty: Titles look like news, not essays
    news_signals = ["announces", "launches", "raises $", "acquires", "ipo", "q1", "q2", "q3", "q4",
                    "earnings", "revenue", "quarterly", "breaking", "report:", "update:"]
    news_hits = sum(1 for sig in news_signals if sig in titles_lower)
    if news_hits >= 2:
        score -= 30

    # Penalty: Looks like a multi-author org/publication (not a personal blog)
    org_signals = ["staff", "team", "editors", "contributors", "newsroom", "editorial board"]
    if any(sig in titles_lower or sig in name_lower for sig in org_signals):
        score -= 25

    # === EVIDENCE QUALITY ===

    # Penalty: no sample titles = no way to judge what they write about
    if not all_titles:
        score -= 15

    # === POSITIVE SIGNALS ===

    # RSS feed found (small bonus — most Substacks have this, not discriminating)
    if source.get("url"):
        score += 5
    else:
        score -= 10

    # Individual Substack (minor — easy to set up, not a quality signal)
    if ".substack.com" in domain and domain != "substack.com":
        score += 3

    # Personal blog domains (independent thinkers tend to self-host)
    personal_tlds = [".me", ".xyz", ".io", ".co", ".net"]
    if any(domain.endswith(tld) for tld in personal_tlds):
        score += 5

    # Multi-method bonus (STRONG — found independently by multiple sources = real signal)
    if len(methods) >= 3:
        score += 30
    elif len(methods) >= 2:
        score += 20

    # Blogroll signals (STRONGEST signal — curated by trusted thinkers)
    blogroll_count = len(evidence.get("found_on_blogrolls", []))
    if blogroll_count >= 2:
        score += 40
    elif blogroll_count >= 1:
        score += 25

    # HN signals (moderate — popular but not necessarily essay quality)
    hn_count = evidence.get("hn_count", 0)
    hn_avg = evidence.get("hn_avg_points", 0)
    score += min(15, hn_count * 3)
    score += min(10, hn_avg * 0.02)
    if evidence.get("user_liked_hn"):
        score += 15

    # Lobsters signals (good — more curated community)
    lob_count = evidence.get("lobsters_count", 0)
    score += min(20, lob_count * 6)

    # Reddit quality subreddit signals
    reddit_count = evidence.get("reddit_count", 0)
    score += min(15, reddit_count * 4)
    if len(evidence.get("subreddits", [])) >= 2:
        score += 10

    # Topic search signals
    query_matches = len(evidence.get("query_matches", []))
    score += min(15, query_matches * 4)

    # Substack recommendation (weak signal — just means someone recommended them)
    if evidence.get("substack_rec"):
        score += 5

    # === TOPIC RELEVANCE (big boost for matching user interests) ===
    interest_hits = 0
    for keyword in INTEREST_KEYWORDS:
        if keyword in titles_lower or keyword in name_lower:
            interest_hits += 1
    if interest_hits >= 3:
        score += 25
    elif interest_hits >= 2:
        score += 15
    elif interest_hits >= 1:
        score += 8

    # === ESSAY QUALITY SIGNALS ===
    essay_hits = sum(1 for kw in ESSAY_TITLE_KEYWORDS if kw in titles_lower)
    if essay_hits >= 2:
        score += 15
    elif essay_hits >= 1:
        score += 8

    # === DYNAMIC INTEREST KEYWORDS (learned from Andy's approvals) ===
    dynamic_keywords = get_dynamic_interest_keywords()
    if dynamic_keywords:
        dynamic_hits = sum(1 for kw in dynamic_keywords if kw in titles_lower or kw in name_lower)
        score += min(15, dynamic_hits * 3)

    return round(max(0, score), 1)


# === DISCOVERY METHODS ===

def discover_via_hn_enhanced() -> list:
    """Enhanced HN mining: lower threshold, more pages, topic-specific searches."""
    sources = []
    domain_stats = {}

    try:
        # A) Generic high-score mining at 50+ points, 5 pages
        for page in range(5):
            params = {
                "tags": "story",
                "numericFilters": "points>50",
                "hitsPerPage": 100,
                "page": page,
            }
            try:
                r = requests.get(HN_API_URL, params=params, timeout=15)
                if r.status_code == 200:
                    for hit in r.json().get("hits", []):
                        url = hit.get("url", "")
                        if not url:
                            continue
                        domain = urlparse(url).netloc.replace("www.", "").lower()
                        if is_ignored_domain(domain) or len(domain) < 5:
                            continue
                        if domain not in domain_stats:
                            domain_stats[domain] = {"count": 0, "total_points": 0, "titles": [], "topics": set()}
                        domain_stats[domain]["count"] += 1
                        domain_stats[domain]["total_points"] += hit.get("points", 0)
                        if len(domain_stats[domain]["titles"]) < 3:
                            domain_stats[domain]["titles"].append(hit.get("title", ""))
            except Exception as e:
                print(f"  HN page {page} error: {e}")
            time.sleep(0.5)

        # B) Topic-specific searches
        for query in HN_TOPIC_QUERIES:
            try:
                params = {
                    "query": query,
                    "tags": "story",
                    "numericFilters": "points>30",
                    "hitsPerPage": 50,
                }
                r = requests.get(HN_API_URL, params=params, timeout=15)
                if r.status_code == 200:
                    for hit in r.json().get("hits", []):
                        url = hit.get("url", "")
                        if not url:
                            continue
                        domain = urlparse(url).netloc.replace("www.", "").lower()
                        if is_ignored_domain(domain) or len(domain) < 5:
                            continue
                        if domain not in domain_stats:
                            domain_stats[domain] = {"count": 0, "total_points": 0, "titles": [], "topics": set()}
                        domain_stats[domain]["count"] += 1
                        domain_stats[domain]["total_points"] += hit.get("points", 0)
                        if len(domain_stats[domain]["titles"]) < 3:
                            domain_stats[domain]["titles"].append(hit.get("title", ""))
                        domain_stats[domain]["topics"].add(query.split()[0])
            except Exception as e:
                print(f"  HN topic '{query}' error: {e}")
            time.sleep(0.5)

        # C) Cross-reference with user-liked domains
        try:
            from bot import get_hn_liked_domains
            liked = get_hn_liked_domains()
        except Exception:
            liked = set()

        # Build source list — require 2+ appearances OR 1 with user-liked
        for domain, stats in domain_stats.items():
            avg_points = stats["total_points"] / max(1, stats["count"])
            is_liked = domain in liked

            if stats["count"] >= 2 or (stats["count"] >= 1 and is_liked):
                sources.append({
                    "name": domain.split(".")[0].title(),
                    "domain": domain,
                    "url": None,
                    "source_type": "hn_enhanced",
                    "discovered_at": datetime.now().isoformat(),
                    "evidence": {
                        "hn_count": stats["count"],
                        "hn_avg_points": round(avg_points, 1),
                        "hn_topics": list(stats["topics"]),
                        "user_liked_hn": is_liked,
                        "sample_titles": stats["titles"],
                    },
                })

    except Exception as e:
        print(f"  Enhanced HN mining error: {e}")

    return sources


def discover_via_topic_search() -> list:
    """Search DuckDuckGo for blogs matching interest topics."""
    sources = []
    domain_hits = {}

    for query in TOPIC_QUERIES:
        try:
            r = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=HEADERS,
                timeout=10,
            )
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            for result in soup.select("a.result__a"):
                href = result.get("href", "")
                if not href:
                    continue

                # DuckDuckGo wraps all URLs — extract the real one first
                if "uddg=" in href:
                    from urllib.parse import parse_qs, urlparse as _parse
                    parsed_href = _parse(href)
                    qs = parse_qs(parsed_href.query)
                    real_url = qs.get("uddg", [href])[0]
                elif "duckduckgo.com" in href:
                    continue  # Skip DDG internal links that aren't search results
                else:
                    real_url = href

                domain = urlparse(real_url).netloc.replace("www.", "").lower()
                if not domain or is_ignored_domain(domain) or len(domain) < 5:
                    continue

                if domain not in domain_hits:
                    domain_hits[domain] = {"queries": [], "titles": []}
                if query not in domain_hits[domain]["queries"]:
                    domain_hits[domain]["queries"].append(query)
                title = result.get_text(strip=True)
                if title and len(domain_hits[domain]["titles"]) < 2:
                    domain_hits[domain]["titles"].append(title[:80])

        except Exception as e:
            print(f"  Search error for '{query}': {e}")

        time.sleep(3)  # Be polite to DuckDuckGo

    for domain, data in domain_hits.items():
        sources.append({
            "name": domain.split(".")[0].title(),
            "domain": domain,
            "url": None,
            "source_type": "topic_search",
            "discovered_at": datetime.now().isoformat(),
            "evidence": {
                "query_matches": data["queries"],
                "sample_titles": data["titles"],
            },
        })

    return sources


def discover_via_blogrolls() -> list:
    """Scrape blogroll/links pages from trusted sources for external links."""
    sources = []
    domain_hits = {}

    for blogroll in BLOGROLL_SOURCES:
        try:
            r = requests.get(blogroll["url"], headers=HEADERS, timeout=10)
            if r.status_code != 200:
                print(f"  Blogroll {blogroll['name']}: {r.status_code}")
                continue

            soup = BeautifulSoup(r.text, "html.parser")

            # Try content-specific areas first, fall back to body
            content = (
                soup.find("article") or soup.find("main") or
                soup.find(class_="content") or soup.find(id="content") or
                soup.find("body")
            )
            if not content:
                continue

            blogroll_domain = urlparse(blogroll["url"]).netloc.replace("www.", "")

            for link in content.find_all("a", href=True):
                href = link.get("href", "")
                if not href.startswith("http"):
                    continue

                domain = urlparse(href).netloc.replace("www.", "").lower()
                if not domain or is_ignored_domain(domain) or len(domain) < 5:
                    continue
                if domain == blogroll_domain:
                    continue

                if domain not in domain_hits:
                    domain_hits[domain] = {"blogrolls": [], "link_text": []}
                if blogroll["name"] not in domain_hits[domain]["blogrolls"]:
                    domain_hits[domain]["blogrolls"].append(blogroll["name"])
                text = link.get_text(strip=True)
                if text and len(domain_hits[domain]["link_text"]) < 2:
                    domain_hits[domain]["link_text"].append(text[:60])

        except Exception as e:
            print(f"  Blogroll {blogroll['name']} error: {e}")

        time.sleep(1)

    for domain, data in domain_hits.items():
        sources.append({
            "name": data["link_text"][0] if data["link_text"] else domain.split(".")[0].title(),
            "domain": domain,
            "url": None,
            "source_type": "blogroll",
            "discovered_at": datetime.now().isoformat(),
            "evidence": {
                "found_on_blogrolls": data["blogrolls"],
                "link_text": data["link_text"],
            },
        })

    return sources


def discover_via_lobsters() -> list:
    """Mine Lobsters for domains appearing in high-quality stories."""
    sources = []
    domain_stats = {}

    # Fetch hottest + tag-specific endpoints
    endpoints = ["https://lobste.rs/hottest.json"]
    for tag in LOBSTERS_TAGS:
        endpoints.append(f"https://lobste.rs/t/{tag}.json")

    for endpoint in endpoints:
        try:
            r = requests.get(endpoint, headers=HEADERS, timeout=10)
            if r.status_code != 200:
                continue

            stories = r.json()
            for story in stories[:50]:  # Top 50 per endpoint
                url = story.get("url", "")
                if not url:
                    continue

                domain = urlparse(url).netloc.replace("www.", "").lower()
                if not domain or is_ignored_domain(domain) or len(domain) < 5:
                    continue

                score = story.get("score", 0)
                tags = [t for t in story.get("tags", []) if t in LOBSTERS_TAGS]

                if domain not in domain_stats:
                    domain_stats[domain] = {"count": 0, "total_score": 0, "tags": set(), "titles": []}
                domain_stats[domain]["count"] += 1
                domain_stats[domain]["total_score"] += score
                domain_stats[domain]["tags"].update(tags)
                if len(domain_stats[domain]["titles"]) < 3:
                    domain_stats[domain]["titles"].append(story.get("title", ""))

        except Exception as e:
            print(f"  Lobsters error ({endpoint}): {e}")

        time.sleep(1)  # Be polite

    for domain, stats in domain_stats.items():
        avg_score = stats["total_score"] / max(1, stats["count"])
        if stats["count"] >= 2 and avg_score > 5:
            sources.append({
                "name": domain.split(".")[0].title(),
                "domain": domain,
                "url": None,
                "source_type": "lobsters",
                "discovered_at": datetime.now().isoformat(),
                "evidence": {
                    "lobsters_count": stats["count"],
                    "lobsters_avg_score": round(avg_score, 1),
                    "lobsters_tags": list(stats["tags"]),
                    "sample_titles": stats["titles"],
                },
            })

    return sources


def discover_via_reddit() -> list:
    """Mine quality subreddits for domains shared with high engagement."""
    sources = []
    domain_stats = {}
    reddit_headers = {"User-Agent": "BrainCandyBot/1.0 (source discovery)"}

    for subreddit in QUALITY_SUBREDDITS:
        try:
            url = f"https://www.reddit.com/r/{subreddit}/top.json?t=month&limit=100"
            r = requests.get(url, headers=reddit_headers, timeout=10)
            if r.status_code != 200:
                print(f"  Reddit r/{subreddit}: {r.status_code}")
                continue

            data = r.json().get("data", {}).get("children", [])
            for post in data:
                post_data = post.get("data", {})

                # Skip self-posts (text posts without external links)
                if post_data.get("is_self", True):
                    continue

                post_url = post_data.get("url", "")
                if not post_url:
                    continue

                domain = urlparse(post_url).netloc.replace("www.", "").lower()
                if not domain or is_ignored_domain(domain) or len(domain) < 5:
                    continue

                ups = post_data.get("ups", 0)
                title = post_data.get("title", "")

                if domain not in domain_stats:
                    domain_stats[domain] = {"count": 0, "total_ups": 0, "subreddits": set(), "titles": []}
                domain_stats[domain]["count"] += 1
                domain_stats[domain]["total_ups"] += ups
                domain_stats[domain]["subreddits"].add(subreddit)
                if len(domain_stats[domain]["titles"]) < 3:
                    domain_stats[domain]["titles"].append(title[:80])

        except Exception as e:
            print(f"  Reddit r/{subreddit} error: {e}")

        time.sleep(2)  # Reddit needs longer delays

    for domain, stats in domain_stats.items():
        avg_ups = stats["total_ups"] / max(1, stats["count"])
        if stats["count"] >= 3 or (stats["count"] >= 2 and avg_ups >= 200):
            sources.append({
                "name": domain.split(".")[0].title(),
                "domain": domain,
                "url": None,
                "source_type": "reddit",
                "discovered_at": datetime.now().isoformat(),
                "evidence": {
                    "reddit_count": stats["count"],
                    "reddit_avg_upvotes": round(avg_ups, 1),
                    "subreddits": list(stats["subreddits"]),
                    "sample_titles": stats["titles"],
                },
            })

    return sources


# === DEEP DISCOVERY ORCHESTRATION ===

def send_discoveries_for_review(top_sources: list):
    """Send discovered sources to Telegram DM for 1/0/x review."""
    for i, source in enumerate(top_sources, 1):
        name = source.get("name", "Unknown")
        domain = source.get("domain", "")
        score = source.get("discovery_score", 0)
        feed = source.get("url", "No RSS found")
        evidence = source.get("evidence", {})
        methods = source.get("methods", [])

        # Build "found via" summary
        found_parts = []
        if "hn_enhanced" in methods:
            ct = evidence.get("hn_count", 0)
            pt = evidence.get("hn_avg_points", 0)
            found_parts.append(f"HN ({ct} stories, avg {pt}pt)")
        if "blogroll" in methods:
            blogs = evidence.get("found_on_blogrolls", [])
            found_parts.append(f"Blogrolls ({', '.join(blogs)})")
        if "lobsters" in methods:
            ct = evidence.get("lobsters_count", 0)
            found_parts.append(f"Lobsters ({ct} stories)")
        if "reddit" in methods:
            subs = evidence.get("subreddits", [])
            found_parts.append(f"Reddit (r/{', r/'.join(subs)})")
        if "topic_search" in methods:
            found_parts.append("Topic search")
        if "substack_recommendation" in methods:
            found_parts.append("Substack recs")

        found_via = ", ".join(found_parts) if found_parts else "Discovery"

        # Get sample title
        sample = ""
        for key in ["sample_titles", "link_text"]:
            titles = evidence.get(key, [])
            if titles:
                sample = titles[0][:60]
                break

        msg = f"🔍 <b>Discovery #{i}</b> (Score: {score})\n\n"
        msg += f"<b>{name}</b> ({domain})\n"
        if feed and feed != "No RSS found":
            msg += f"Feed: {feed}\n"
        msg += f"\nFound via: {found_via}\n"
        if sample:
            msg += f"Sample: <i>{sample}</i>\n"
        msg += "\nReply: <b>1</b>=Add  <b>0</b>=Skip  <b>x</b>=Block"

        send_telegram_message(msg)
        time.sleep(1)


def run_deep_discovery():
    """Run comprehensive source discovery using all methods."""
    print(f"[{datetime.now()}] Running DEEP discovery (all methods)...\n")

    all_sources = []

    # Method 1: Substack Recommendations
    print("--- Method 1: Substack Recommendations ---")
    try:
        substack_feeds = []
        with open(FEEDS_FILE, "r") as f:
            content = f.read()
            substack_urls = re.findall(r'https://[a-zA-Z0-9-]+\.substack\.com/feed', content)
            substack_feeds = list(set(substack_urls))

        existing_domains = get_existing_domains()
        substack_count = 0
        for feed_url in substack_feeds[:20]:
            recs = scrape_substack_recommendations(feed_url)
            for rec in recs:
                domain = rec["domain"]
                if domain not in existing_domains:
                    all_sources.append({
                        "name": rec["name"],
                        "domain": domain,
                        "url": rec.get("url"),
                        "source_type": "substack_recommendation",
                        "discovered_at": datetime.now().isoformat(),
                        "evidence": {"substack_rec": True, "discovered_from": rec.get("discovered_from", "")},
                    })
                    substack_count += 1
        print(f"  Found {substack_count} from Substack recs")
    except Exception as e:
        print(f"  Substack error: {e}")

    # Method 2: Enhanced HN Mining
    print("\n--- Method 2: Enhanced HN Mining ---")
    try:
        hn_sources = discover_via_hn_enhanced()
        all_sources.extend(hn_sources)
        print(f"  Found {len(hn_sources)} from enhanced HN")
    except Exception as e:
        print(f"  HN error: {e}")

    # Method 3: Topic-Based Web Search
    print("\n--- Method 3: Topic-Based Web Search ---")
    try:
        search_sources = discover_via_topic_search()
        all_sources.extend(search_sources)
        print(f"  Found {len(search_sources)} from topic search")
    except Exception as e:
        print(f"  Topic search error: {e}")

    # Method 4: Blogroll Mining
    print("\n--- Method 4: Blogroll Mining ---")
    try:
        blogroll_sources = discover_via_blogrolls()
        all_sources.extend(blogroll_sources)
        print(f"  Found {len(blogroll_sources)} from blogrolls")
    except Exception as e:
        print(f"  Blogroll error: {e}")

    # Method 5: Lobsters Mining
    print("\n--- Method 5: Lobsters Mining ---")
    try:
        lobsters_sources = discover_via_lobsters()
        all_sources.extend(lobsters_sources)
        print(f"  Found {len(lobsters_sources)} from Lobsters")
    except Exception as e:
        print(f"  Lobsters error: {e}")

    # Method 6: Reddit Mining
    print("\n--- Method 6: Reddit Mining ---")
    try:
        reddit_sources = discover_via_reddit()
        all_sources.extend(reddit_sources)
        print(f"  Found {len(reddit_sources)} from Reddit")
    except Exception as e:
        print(f"  Reddit error: {e}")

    # Deduplicate
    print(f"\n--- Deduplication ---")
    print(f"  Raw total: {len(all_sources)}")
    unique_sources = deduplicate_sources(all_sources)
    print(f"  After dedup: {len(unique_sources)}")

    if not unique_sources:
        print("\nNo new sources found.")
        send_telegram_message("🔍 Deep discovery ran — no new sources found.")
        return

    # Probe RSS for top candidates (by pre-score without RSS bonus)
    print(f"\n--- RSS Feed Detection (top 50) ---")
    for source in unique_sources:
        source["_prescore"] = score_discovered_source(source)
    unique_sources.sort(key=lambda x: x["_prescore"], reverse=True)

    probed = 0
    for source in unique_sources[:50]:
        if not source.get("url"):
            feed = find_rss_feed(source["domain"])
            if feed:
                source["url"] = feed
                print(f"  RSS found: {source['domain']} -> {feed}")
                probed += 1
    print(f"  Found RSS for {probed} sources")

    # Fetch sample titles from feeds that have RSS but no titles (enrichment)
    print(f"\n--- Fetching sample titles for top candidates ---")
    import feedparser
    enriched = 0
    for source in unique_sources[:50]:
        evidence = source.get("evidence", {})
        has_titles = any(evidence.get(k) for k in ["sample_titles", "link_text"])
        if has_titles or not source.get("url"):
            continue
        try:
            feed_data = feedparser.parse(source["url"])
            titles = []
            for entry in feed_data.entries[:3]:
                title = entry.get("title", "").strip()
                if title:
                    titles.append(title[:80])
            if titles:
                evidence["sample_titles"] = titles
                enriched += 1
        except Exception:
            pass
    print(f"  Enriched {enriched} sources with sample titles")

    # Final scoring
    for source in unique_sources:
        source["discovery_score"] = score_discovered_source(source)
        source.pop("_prescore", None)

    unique_sources.sort(key=lambda x: x["discovery_score"], reverse=True)

    # Filter: minimum score of 25 (don't send garbage)
    quality_sources = [s for s in unique_sources if s["discovery_score"] >= 25]
    # Prefer sources with RSS, but allow top sources without (we'll find feed later)
    with_rss = [s for s in quality_sources if s.get("url")]
    without_rss = [s for s in quality_sources if not s.get("url")]
    # Fill top 15: prioritize RSS sources, then backfill with non-RSS
    top_sources = with_rss[:15]
    if len(top_sources) < 15:
        top_sources.extend(without_rss[:15 - len(top_sources)])

    if not top_sources:
        print("\nNo quality sources found above threshold.")
        send_telegram_message("🔍 Deep discovery ran — no quality essay sources found this time.")
        return

    # Print summary
    print(f"\n--- Top {len(top_sources)} Discoveries ---")
    for i, s in enumerate(top_sources, 1):
        print(f"  {i}. {s['domain']} (score: {s['discovery_score']}, methods: {', '.join(s.get('methods', []))})")

    # Save all to discovered_sources.json
    discovered = load_discovered()
    discovered["sources"].extend(unique_sources)
    new_seen = {s["domain"] for s in unique_sources}
    discovered["seen_domains"] = list(set(discovered.get("seen_domains", [])) | new_seen)
    save_discovered(discovered)

    # Send top 15 to Telegram for review
    print(f"\nSending top {len(top_sources)} to Telegram for review...")
    send_discoveries_for_review(top_sources)

    # Save pending for --process-reviews
    pending = []
    for source in top_sources:
        pending.append({
            "name": source.get("name", ""),
            "domain": source.get("domain", ""),
            "url": source.get("url"),
            "source_type": ", ".join(source.get("methods", [])),
            "sent_at": datetime.now().isoformat(),
        })

    with open(PENDING_DISCOVERY_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    print(f"\nDone! Review in Telegram, then run: python3 discover.py --process-reviews")


def load_discovered():
    """Load discovered sources from file."""
    if DISCOVERED_FILE.exists():
        with open(DISCOVERED_FILE, "r") as f:
            return json.load(f)
    return {"sources": [], "seen_domains": [], "last_updated": None}


def save_discovered(data):
    """Save discovered sources to file."""
    data["last_updated"] = datetime.now().isoformat()
    with open(DISCOVERED_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_existing_domains():
    """Get domains already in our feeds list."""
    domains = set()
    try:
        with open(FEEDS_FILE, "r") as f:
            content = f.read()
            # Extract URLs from feeds.py
            urls = re.findall(r'https?://[^\s"\',]+', content)
            for url in urls:
                parsed = urlparse(url)
                domain = parsed.netloc.replace("www.", "")
                domains.add(domain)
    except Exception as e:
        print(f"Error reading feeds: {e}")
    return domains


def scrape_substack_recommendations(substack_url: str) -> list:
    """
    Scrape recommended writers from a Substack's recommendations page.
    Returns list of {name, url, domain} dicts.
    """
    recommendations = []

    try:
        # Convert feed URL to recommendations page
        # e.g., https://example.substack.com/feed -> https://example.substack.com/recommendations
        parsed = urlparse(substack_url)
        if "substack.com" not in parsed.netloc:
            return []

        base_url = f"{parsed.scheme}://{parsed.netloc}"
        rec_url = f"{base_url}/recommendations"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }

        response = requests.get(rec_url, headers=headers, timeout=10)
        if response.status_code != 200:
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # Find recommendation links (Substack recommendation pages have links to other Substacks)
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if "substack.com" in href and "/recommendations" not in href:
                # Extract the Substack URL
                parsed_rec = urlparse(href)
                if parsed_rec.netloc and "substack.com" in parsed_rec.netloc:
                    rec_domain = parsed_rec.netloc.replace("www.", "")
                    rec_base = f"https://{rec_domain}"

                    # Get the name from link text or domain
                    name = link.get_text(strip=True)
                    if not name or len(name) > 50:
                        name = rec_domain.replace(".substack.com", "").title()

                    recommendations.append({
                        "name": name,
                        "url": f"{rec_base}/feed",
                        "domain": rec_domain,
                        "discovered_from": substack_url,
                    })

        return recommendations

    except Exception as e:
        print(f"Error scraping {substack_url}: {e}")
        return []


def mine_hn_domains(min_points: int = 150, num_pages: int = 3) -> dict:
    """
    Mine Hacker News for domains that frequently appear in high-scoring posts.
    Returns dict of {domain: {"count": N, "avg_points": X, "sample_titles": [...]}}
    """
    domain_stats = {}

    try:
        for page in range(num_pages):
            params = {
                "tags": "story",
                "numericFilters": f"points>{min_points}",
                "hitsPerPage": 100,
                "page": page,
            }

            response = requests.get(HN_API_URL, params=params, timeout=15)
            if response.status_code != 200:
                continue

            data = response.json()
            hits = data.get("hits", [])

            for hit in hits:
                url = hit.get("url", "")
                if not url:
                    continue

                parsed = urlparse(url)
                domain = parsed.netloc.replace("www.", "").lower()

                # Skip ignored domains
                if is_ignored_domain(domain):
                    continue

                # Skip if too short (probably not a blog)
                if len(domain) < 5:
                    continue

                points = hit.get("points", 0)
                title = hit.get("title", "")

                if domain not in domain_stats:
                    domain_stats[domain] = {
                        "count": 0,
                        "total_points": 0,
                        "sample_titles": [],
                    }

                domain_stats[domain]["count"] += 1
                domain_stats[domain]["total_points"] += points
                if len(domain_stats[domain]["sample_titles"]) < 3:
                    domain_stats[domain]["sample_titles"].append(title)

        # Calculate averages and filter for quality
        quality_domains = {}
        for domain, stats in domain_stats.items():
            if stats["count"] >= 2:  # Appeared at least twice
                avg_points = stats["total_points"] / stats["count"]
                quality_domains[domain] = {
                    "count": stats["count"],
                    "avg_points": round(avg_points, 1),
                    "sample_titles": stats["sample_titles"],
                }

        return quality_domains

    except Exception as e:
        print(f"Error mining HN: {e}")
        return {}


def discover_new_sources():
    """
    Main discovery function. Finds new sources from:
    1. Substack recommendations
    2. Hacker News domain mining

    Returns list of newly discovered sources.
    """
    print(f"[{datetime.now()}] Running source discovery...")

    discovered = load_discovered()
    existing_domains = get_existing_domains()
    seen_domains = set(discovered.get("seen_domains", []))
    new_sources = []

    # 1. Scrape Substack recommendations
    print("Scraping Substack recommendations...")

    # Get Substack URLs from feeds.py
    substack_feeds = []
    try:
        with open(FEEDS_FILE, "r") as f:
            content = f.read()
            substack_urls = re.findall(r'https://[a-zA-Z0-9-]+\.substack\.com/feed', content)
            substack_feeds = list(set(substack_urls))
    except Exception as e:
        print(f"Error reading feeds: {e}")

    for feed_url in substack_feeds[:20]:  # Limit to avoid rate limiting
        recommendations = scrape_substack_recommendations(feed_url)
        for rec in recommendations:
            domain = rec["domain"]
            if domain not in existing_domains and domain not in seen_domains:
                rec["source_type"] = "substack_recommendation"
                rec["discovered_at"] = datetime.now().isoformat()
                new_sources.append(rec)
                seen_domains.add(domain)
                print(f"  Found: {rec['name']} ({domain})")

    # 2. Mine Hacker News for quality domains
    print("Mining Hacker News for quality domains...")

    hn_domains = mine_hn_domains(min_points=150, num_pages=2)

    for domain, stats in sorted(hn_domains.items(), key=lambda x: x[1]["count"], reverse=True):
        if domain not in existing_domains and domain not in seen_domains:
            # Try to construct an RSS feed URL
            feed_url = None
            if "substack.com" in domain:
                feed_url = f"https://{domain}/feed"
            else:
                # Common RSS patterns
                for pattern in ["/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml"]:
                    test_url = f"https://{domain}{pattern}"
                    try:
                        r = requests.head(test_url, timeout=5, allow_redirects=True)
                        if r.status_code == 200:
                            feed_url = test_url
                            break
                    except:
                        continue

            source = {
                "name": domain.split(".")[0].title(),
                "domain": domain,
                "url": feed_url,
                "hn_count": stats["count"],
                "hn_avg_points": stats["avg_points"],
                "sample_titles": stats["sample_titles"],
                "source_type": "hn_mining",
                "discovered_at": datetime.now().isoformat(),
            }
            new_sources.append(source)
            seen_domains.add(domain)
            print(f"  Found: {domain} (HN count: {stats['count']}, avg: {stats['avg_points']})")

    # Save discovered sources
    discovered["sources"].extend(new_sources)
    discovered["seen_domains"] = list(seen_domains)
    save_discovered(discovered)

    print(f"Discovery complete. Found {len(new_sources)} new sources.")
    return new_sources


def get_top_discoveries(n: int = 10) -> list:
    """Get top N discovered sources worth adding."""
    discovered = load_discovered()
    sources = discovered.get("sources", [])

    # Score sources
    scored = []
    for source in sources:
        score = 0

        # Substack recommendations are high signal
        if source.get("source_type") == "substack_recommendation":
            score += 50

        # HN presence is good signal
        hn_count = source.get("hn_count", 0)
        hn_avg = source.get("hn_avg_points", 0)
        score += hn_count * 10
        score += hn_avg * 0.1

        # Has valid RSS feed
        if source.get("url"):
            score += 20

        source["discovery_score"] = score
        scored.append(source)

    # Sort by score and return top N
    scored.sort(key=lambda x: x.get("discovery_score", 0), reverse=True)
    return scored[:n]


def format_discoveries_report() -> str:
    """Format a report of top discoveries for review."""
    top = get_top_discoveries(15)

    if not top:
        return "No new sources discovered yet. Run discovery first."

    report = "🔍 **Top Discovered Sources**\n\n"

    for i, source in enumerate(top, 1):
        name = source.get("name", "Unknown")
        domain = source.get("domain", "")
        source_type = source.get("source_type", "")
        url = source.get("url", "No RSS found")

        report += f"{i}. **{name}** ({domain})\n"
        report += f"   Source: {source_type}\n"

        if source.get("hn_count"):
            report += f"   HN: {source['hn_count']} posts, avg {source['hn_avg_points']} pts\n"

        if source.get("sample_titles"):
            report += f"   Sample: \"{source['sample_titles'][0][:50]}...\"\n"

        report += f"   Feed: {url}\n\n"

    return report


def send_telegram_message(text: str) -> bool:
    """Send a message via Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        print("No Telegram token configured")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ANDY_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending Telegram message: {e}")
        return False


def auto_add_top_sources(n: int = 5) -> list:
    """
    Automatically add top N discovered sources to feeds.py.
    Only adds sources that have valid RSS feeds.
    Returns list of added sources.
    """
    top = get_top_discoveries(n * 2)  # Get more to filter
    existing_domains = get_existing_domains()
    added = []

    for source in top:
        if len(added) >= n:
            break

        domain = source.get("domain", "")
        url = source.get("url")
        name = source.get("name", domain.split(".")[0].title())

        # Skip if no RSS feed found or already exists
        if not url or domain in existing_domains:
            continue

        # Verify the feed works
        try:
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                continue
            if "xml" not in response.headers.get("content-type", "").lower() and "<rss" not in response.text[:500] and "<feed" not in response.text[:500]:
                continue
        except:
            continue

        # Add to feeds.py
        try:
            with open(FEEDS_FILE, "r") as f:
                content = f.read()

            # Find the end of the FEEDS list (before BLOCKED_DOMAINS)
            insert_point = content.find("BLOCKED_DOMAINS")
            if insert_point == -1:
                continue

            # Go back to find the last feed entry
            last_bracket = content.rfind("},", 0, insert_point)
            if last_bracket == -1:
                continue

            # Determine category
            category = "Essays"
            if "substack" in domain:
                category = "Discovered"

            new_entry = f'\n    {{"name": "{name}", "url": "{url}", "category": "{category}"}},  # Auto-discovered'

            new_content = content[:last_bracket + 2] + new_entry + content[last_bracket + 2:]

            with open(FEEDS_FILE, "w") as f:
                f.write(new_content)

            added.append({"name": name, "url": url, "domain": domain})
            print(f"Added: {name} ({domain})")

        except Exception as e:
            print(f"Error adding {domain}: {e}")

    return added


def run_weekly_discovery():
    """
    Run weekly discovery (via GitHub Actions).
    Note: Only HN mining works on GHA (Substack blocks cloud IPs).
    Scores sources properly and sends top 10 for DM review.
    """
    print(f"[{datetime.now()}] Running weekly discovery...")

    new_sources = discover_new_sources()
    print(f"Found {len(new_sources)} new potential sources")

    # Score all discovered sources with the proper scoring function
    discovered = load_discovered()
    existing_domains = get_existing_domains()
    rejected = set()
    rejected_file = DATA_DIR / "rejected_sources.json"
    if rejected_file.exists():
        try:
            with open(rejected_file, "r") as f:
                rej_data = json.load(f)
            rejected = set(rej_data.get("domains", []))
        except Exception:
            pass

    # Score and filter
    candidates = []
    for source in discovered.get("sources", []):
        domain = source.get("domain", "")
        if domain in existing_domains or domain in rejected:
            continue
        source["discovery_score"] = score_discovered_source(source)
        if source["discovery_score"] >= 25 and source.get("url"):
            candidates.append(source)

    candidates.sort(key=lambda x: x["discovery_score"], reverse=True)
    top = candidates[:10]

    if not top:
        print("No quality sources found above threshold.")
        send_telegram_message("Weekly discovery ran — no quality sources to review.")
        return {"new_sources": len(new_sources), "sent_for_review": 0}

    # Send for DM review instead of auto-adding
    print(f"Sending top {len(top)} discoveries for review...")
    send_discoveries_for_review(top)

    # Save pending for review processing
    pending = []
    for source in top:
        pending.append({
            "name": source.get("name", ""),
            "domain": source.get("domain", ""),
            "url": source.get("url"),
            "source_type": ", ".join(source.get("methods", [])),
            "sent_at": datetime.now().isoformat(),
        })

    with open(PENDING_DISCOVERY_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    save_discovered(discovered)
    print(f"Sent {len(top)} discoveries for review via Telegram.")
    return {"new_sources": len(new_sources), "sent_for_review": len(top)}


PENDING_DISCOVERY_FILE = DATA_DIR / "pending_discovery_review.json"


def run_local_interactive():
    """Run discovery locally with interactive Telegram review.
    Use this when on your local machine (Substack works from local IPs)."""
    import time

    print(f"[{datetime.now()}] Running LOCAL interactive discovery...")
    print("(Substack scraping + HN mining both work locally)")

    # Full discovery (Substack + HN both work locally)
    new_sources = discover_new_sources()

    if not new_sources:
        print("No new sources found.")
        send_telegram_message("Discovery ran locally — no new sources found.")
        return

    # Get top discoveries
    top = get_top_discoveries(10)

    if not top:
        print("No high-quality sources to review.")
        send_telegram_message("Discovery found sources but none met quality threshold.")
        return

    # Send each to Andy for review via DM
    for i, source in enumerate(top, 1):
        name = source.get("name", "Unknown")
        domain = source.get("domain", "")
        source_type = source.get("source_type", "")
        url = source.get("url", "No RSS")

        info = f"🔍 <b>Discovery #{i}</b>\n\n"
        info += f"<b>{name}</b> ({domain})\n"
        info += f"Type: {source_type}\n"

        if source.get("hn_count"):
            info += f"HN: {source['hn_count']} posts, avg {source['hn_avg_points']} pts\n"

        if source.get("sample_titles"):
            info += f"Sample: {source['sample_titles'][0][:60]}\n"

        info += f"Feed: {url}\n\n"
        info += "Reply: <b>1</b>=Add  <b>0</b>=Skip  <b>x</b>=Block"

        send_telegram_message(info)
        time.sleep(1)

    # Save pending for review processing
    pending = []
    for source in top:
        pending.append({
            "name": source.get("name", ""),
            "domain": source.get("domain", ""),
            "url": source.get("url"),
            "source_type": source.get("source_type", ""),
            "sent_at": datetime.now().isoformat(),
        })

    with open(PENDING_DISCOVERY_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    print(f"Sent {len(top)} discoveries for review via Telegram.")
    print("Run 'python discover.py --process-reviews' after responding.")


def process_discovery_reviews():
    """Process Andy's responses to discovery review DMs."""
    if not PENDING_DISCOVERY_FILE.exists():
        print("No pending discovery reviews.")
        return

    with open(PENDING_DISCOVERY_FILE, "r") as f:
        pending = json.load(f)

    if not pending:
        print("No pending discovery reviews.")
        return

    from bot import get_updates, add_rejected_source

    updates = get_updates()
    processed = 0

    for update in updates:
        message = update.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip().lower()

        if chat_id != ANDY_CHAT_ID:
            continue

        ratings = []
        clean_text = text.replace(",", "").replace(" ", "")
        for char in clean_text:
            if char == "1":
                ratings.append("add")
            elif char == "0":
                ratings.append("skip")
            elif char == "x":
                ratings.append("block")

        if ratings:
            for i, rating in enumerate(ratings):
                if i < len(pending):
                    source = pending[i]
                    if rating == "add" and source.get("url"):
                        auto_add_top_sources_single(source)
                        send_telegram_message(f"Added: {source['name']} ({source['domain']})")
                        print(f"ADDED: {source['name']} ({source['domain']})")
                    elif rating == "block":
                        add_rejected_source(source.get("name", ""),
                                            f"https://{source.get('domain', '')}")
                        send_telegram_message(f"Blocked: {source['name']}")
                        print(f"BLOCKED: {source['name']} ({source['domain']})")
                    else:
                        print(f"SKIPPED: {source['name']} ({source['domain']})")

                    processed += 1

            pending = pending[len(ratings):]

    # Save remaining
    with open(PENDING_DISCOVERY_FILE, "w") as f:
        json.dump(pending, f, indent=2)

    # Clear processed updates
    if updates:
        last_update_id = updates[-1]["update_id"]
        get_updates(offset=last_update_id + 1)

    print(f"Processed {processed} discovery reviews. {len(pending)} remaining.")


def auto_add_top_sources_single(source: dict) -> bool:
    """Add a single discovered source to feeds.py."""
    domain = source.get("domain", "")
    url = source.get("url")
    name = source.get("name", domain.split(".")[0].title())

    if not url:
        print(f"No RSS feed for {name}")
        return False

    existing_domains = get_existing_domains()
    if domain in existing_domains:
        print(f"Already in feeds: {name}")
        return False

    # Verify feed works
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return False
    except Exception:
        return False

    # Add to feeds.py
    try:
        with open(FEEDS_FILE, "r") as f:
            content = f.read()

        insert_point = content.find("BLOCKED_DOMAINS")
        if insert_point == -1:
            return False

        last_bracket = content.rfind("},", 0, insert_point)
        if last_bracket == -1:
            return False

        category = "Discovered"
        new_entry = f'\n    {{"name": "{name}", "url": "{url}", "category": "{category}"}},  # Andy-approved'

        new_content = content[:last_bracket + 2] + new_entry + content[last_bracket + 2:]

        with open(FEEDS_FILE, "w") as f:
            f.write(new_content)

        print(f"Added to feeds.py: {name} ({domain})")
        return True

    except Exception as e:
        print(f"Error adding {domain}: {e}")
        return False


def rescore_all_discovered():
    """Re-score all discovered sources and send top ones for review.

    Use this to clean up the backlog after improving the scoring algorithm.
    Run: python discover.py --rescore
    """
    discovered = load_discovered()
    sources = discovered.get("sources", [])
    print(f"Rescoring {len(sources)} discovered sources...")

    existing_domains = get_existing_domains()
    rejected = set()
    rejected_file = DATA_DIR / "rejected_sources.json"
    if rejected_file.exists():
        try:
            with open(rejected_file, "r") as f:
                rej_data = json.load(f)
            rejected = set(rej_data.get("domains", []))
        except Exception:
            pass

    # Score all sources
    for source in sources:
        source["discovery_score"] = score_discovered_source(source)

    # Filter out garbage, already-added, and rejected
    kept = [
        s for s in sources
        if s.get("discovery_score", 0) > 0
        and s.get("domain", "") not in existing_domains
        and s.get("domain", "") not in rejected
    ]
    kept.sort(key=lambda x: x["discovery_score"], reverse=True)

    removed = len(sources) - len(kept)
    print(f"Kept {len(kept)} sources, removed {removed} (score=0, already added, or rejected)")

    # Save cleaned list
    discovered["sources"] = kept
    save_discovered(discovered)

    # Send top 15 for review
    top = [s for s in kept if s.get("url")][:15]
    if top:
        print(f"\nSending top {len(top)} for review...")
        send_discoveries_for_review(top)

        pending = []
        for source in top:
            pending.append({
                "name": source.get("name", ""),
                "domain": source.get("domain", ""),
                "url": source.get("url"),
                "source_type": ", ".join(source.get("methods", [])),
                "sent_at": datetime.now().isoformat(),
            })
        with open(PENDING_DISCOVERY_FILE, "w") as f:
            json.dump(pending, f, indent=2)
        print(f"Review the top {len(top)} in Telegram, then run: python3 discover.py --process-reviews")
    else:
        print("No sources with RSS feeds to review.")

    # Print top 20 summary
    print(f"\nTop 20 scored sources:")
    for i, s in enumerate(kept[:20], 1):
        print(f"  {i:2d}. {s.get('discovery_score', 0):5.1f}  {s.get('domain', '?')}  ({s.get('name', '?')})")


if __name__ == "__main__":
    import sys

    if "--rescore" in sys.argv:
        rescore_all_discovered()
    elif "--deep" in sys.argv:
        run_deep_discovery()
    elif "--weekly" in sys.argv:
        run_weekly_discovery()
    elif "--local" in sys.argv:
        run_local_interactive()
    elif "--process-reviews" in sys.argv:
        process_discovery_reviews()
    else:
        new_sources = discover_new_sources()
        print("\n" + "=" * 50)
        print(format_discoveries_report())
