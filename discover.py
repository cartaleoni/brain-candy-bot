"""
Source Discovery Module for Brain Candy Bot

Discovers new sources through:
1. Substack Recommendations - scrapes "recommended" writers from existing Substacks
2. Hacker News Domain Mining - tracks domains that frequently appear in high-scoring posts
"""

import requests
import json
import re
import os
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime
from bs4 import BeautifulSoup

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
IGNORE_DOMAINS = {
    # Social media
    "twitter.com", "x.com", "youtube.com", "reddit.com", "old.reddit.com",
    "medium.com", "linkedin.com", "facebook.com", "instagram.com", "tiktok.com",
    "threads.net", "mastodon.social", "bsky.app",

    # Code/developer sites
    "github.com", "github.blog", "gitlab.com", "stackoverflow.com",
    "docs.google.com", "drive.google.com", "notion.so", "figma.com",

    # News sites
    "nytimes.com", "wsj.com", "bloomberg.com", "ft.com", "economist.com",
    "bbc.com", "bbc.co.uk", "cnn.com", "reuters.com", "apnews.com",
    "theguardian.com", "washingtonpost.com", "news.ycombinator.com",
    "techmeme.com", "techcrunch.com", "theverge.com", "wired.com",
    "arstechnica.com", "engadget.com", "vice.com", "vox.com",

    # Corporate sites (not blogs)
    "apple.com", "google.com", "microsoft.com", "amazon.com", "meta.com",
    "openai.com", "anthropic.com", "nvidia.com", "intel.com", "amd.com",
    "stripe.com", "shopify.com", "salesforce.com", "oracle.com",

    # Academic/reference
    "arxiv.org", "wikipedia.org", "wikimedia.org", "archive.org",
    "scholar.google.com", "semanticscholar.org", "researchgate.net",

    # Other non-blog sites
    "imgur.com", "gfycat.com", "giphy.com", "pastebin.com",
    "dropbox.com", "wetransfer.com", "mega.nz",
}


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
                if domain in IGNORE_DOMAINS:
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
    Run weekly discovery and send report to Telegram.
    Also auto-adds top 3 high-quality sources.
    """
    print(f"[{datetime.now()}] Running weekly discovery...")

    # Run discovery
    new_sources = discover_new_sources()

    # Auto-add top 3 sources
    added = auto_add_top_sources(n=3)

    # Format report for Telegram
    report = "🔍 <b>Weekly Source Discovery Report</b>\n\n"
    report += f"Found {len(new_sources)} new potential sources.\n\n"

    if added:
        report += "<b>Auto-added to feeds:</b>\n"
        for source in added:
            report += f"• {source['name']} ({source['domain']})\n"
        report += "\n"

    # Show top discoveries for manual review
    top = get_top_discoveries(5)
    if top:
        report += "<b>Top discoveries to review:</b>\n"
        for source in top[:5]:
            name = source.get("name", "Unknown")
            domain = source.get("domain", "")
            report += f"• {name} - {domain}\n"

    # Send to Telegram
    if TELEGRAM_BOT_TOKEN:
        send_telegram_message(report)
        print("Sent discovery report to Telegram")
    else:
        print(report)

    return {"new_sources": len(new_sources), "added": added}


if __name__ == "__main__":
    import sys

    if "--weekly" in sys.argv:
        # Run weekly discovery with Telegram report
        run_weekly_discovery()
    else:
        # Run discovery only
        new_sources = discover_new_sources()
        print("\n" + "=" * 50)
        print(format_discoveries_report())
