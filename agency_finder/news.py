import re
import asyncio
import logging
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, quote_plus
from bs4 import BeautifulSoup
from .search import asearch_query
from .utils import USER_AGENT

logger = logging.getLogger("agency_finder.news")

NEWS_CACHE: Dict[str, tuple] = {}
NEWS_CACHE_TTL = 86400

_IGNORE_NEWS_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "youtu.be", "google.com", "google.it",
    "github.com", "gitlab.com", "medium.com", "behance.net", "dribbble.com",
    "pinterest.com", "tiktok.com", "wikipedia.org", "wikimedia.org",
}


def _cache_key(*args) -> str:
    import hashlib
    return hashlib.md5("|".join(str(a) for a in args).encode()).hexdigest()


def _is_relevant_news(item: Dict[str, str], agency_name: str, website_domain: str) -> bool:
    """Strict relevance filter: item must mention the agency name or website domain."""
    haystack = (item.get("title", "") + " " + item.get("snippet", "") + " " + item.get("url", "")).lower()
    name_lower = agency_name.lower()
    domain_stem = website_domain.lower().split(".")[0] if website_domain else ""

    if name_lower in haystack:
        return True
    if domain_stem and len(domain_stem) > 3 and domain_stem in haystack:
        return True
    name_words = [w for w in name_lower.split() if len(w) > 3]
    if name_words and name_words[0] in haystack:
        return True
    return False


def _parse_date(date_str: str) -> Optional[str]:
    """Try to parse various date formats into ISO-ish strings for display."""
    if not date_str:
        return ""
    # Try RFC 2822 (Google News pubDate)
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass
    # Try ISO format
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Return as-is if we can't parse (e.g. "3 hours ago")
    return date_str


async def afetch_google_news_rss(
    agency_name: str, website_domain: str = "", progress_cb=None
) -> List[Dict[str, str]]:
    """Fetch recent news from Google News RSS feed."""
    query = f'"{agency_name}"'
    encoded = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=it&gl=IT&ceid=IT:it"
    url_en = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"

    items = []
    seen_urls: set = set()
    for feed_url in (url, url_en):
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(feed_url, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                continue
            for item_el in channel.findall("item"):
                title = (item_el.findtext("title") or "").strip()
                link = (item_el.findtext("link") or "").strip()
                pub_date = (item_el.findtext("pubDate") or "").strip()
                source_el = item_el.find("source")
                source_name = source_el.text.strip() if source_el is not None and source_el.text else ""
                snippet = (item_el.findtext("description") or "").strip()
                # Strip HTML from snippet
                if snippet:
                    snippet = BeautifulSoup(snippet, "html.parser").get_text(strip=True)[:300]
                if not title or not link:
                    continue
                if link.rstrip("/") in seen_urls:
                    continue
                seen_urls.add(link.rstrip("/"))
                parsed = urlparse(link)
                if parsed.netloc in _IGNORE_NEWS_DOMAINS:
                    continue
                items.append({
                    "title": title,
                    "url": link,
                    "source": source_name or parsed.netloc,
                    "date": _parse_date(pub_date),
                    "snippet": snippet,
                })
        except Exception as e:
            logger.debug(f"Google News RSS failed ({feed_url[:80]}): {e}")
            continue

    return [i for i in items if _is_relevant_news(i, agency_name, website_domain)]


async def afetch_ddg_news(
    agency_name: str, website_domain: str = "", progress_cb=None
) -> List[Dict[str, str]]:
    """Use DuckDuckGo search to find recent news and press releases."""
    queries = [
        f'"{agency_name}" news',
        f'"{agency_name}" "comunicato stampa" OR "press release" OR "annuncia"',
    ]
    items = []
    seen_urls: set = set()
    for q in queries:
        try:
            results = await asearch_query(q, max_results=5)
        except Exception as e:
            logger.debug(f"DDG news search failed ({q[:50]}): {e}")
            continue
        for r in results:
            link = r.get("link", "")
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            parsed = urlparse(link)
            if parsed.netloc in _IGNORE_NEWS_DOMAINS or not parsed.netloc:
                continue
            if not title:
                continue
            if link.rstrip("/") in seen_urls:
                continue
            seen_urls.add(link.rstrip("/"))
            # Try to extract a date from snippet (e.g. "Published: 2025-01-15")
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', snippet)
            date_str = date_match.group(1) if date_match else ""
            items.append({
                "title": re.sub(r"\s*\|\s*.*$", "", title).strip(),
                "url": link,
                "source": parsed.netloc,
                "date": date_str,
                "snippet": snippet[:300],
            })
    return [i for i in items if _is_relevant_news(i, agency_name, website_domain)]


async def afetch_linkedin_posts(
    linkedin_company_url: str, agency_name: str = "", website_domain: str = "",
    progress_cb=None,
) -> List[Dict[str, str]]:
    """Scrape the LinkedIn company page for public posts mentioning external URLs."""
    if not linkedin_company_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(
                linkedin_company_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
        if resp.status_code != 200:
            return []
        lower = resp.text.lower()
        if any(s in lower for s in ("captcha", "verify you are human", "sign in", "join linkedin")):
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        items = []

        for post in soup.find_all(["article", "div"], attrs={"data-id": True}):
            post_text = post.get_text(" ", strip=True)[:500]
            post_date = ""
            time_tag = post.find("time")
            if time_tag:
                post_date = time_tag.get("datetime", "") or time_tag.get_text(strip=True)
            external_links = []
            for a in post.find_all("a", href=True):
                href = a["href"]
                parsed = urlparse(href)
                if parsed.netloc and not any(
                    parsed.netloc == d or parsed.netloc.endswith("." + d)
                    for d in ("linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com")
                ):
                    external_links.append(href)

            if external_links:
                for ext_url in external_links[:2]:
                    parsed_ext = urlparse(ext_url)
                    items.append({
                        "title": post_text[:120] + ("..." if len(post_text) > 120 else ""),
                        "url": ext_url,
                        "source": "linkedin.com",
                        "date": _parse_date(post_date),
                        "snippet": post_text[:300],
                    })

        if agency_name and items:
            items = [i for i in items if _is_relevant_news(i, agency_name, website_domain)]
        return items

    except Exception as e:
        logger.debug(f"LinkedIn posts scrape failed: {e}")
        return []


async def aexternal_news_lookup(
    agency_name: str,
    website_domain: str = "",
    linkedin_company_url: str = "",
    progress_cb=None,
) -> List[Dict[str, str]]:
    """
    Fetch news about the agency from multiple sources in parallel.
    Returns a deduplicated, relevance-filtered list sorted by date desc, top 10.
    """
    if not agency_name:
        return []

    cache_k = _cache_key("news", agency_name, website_domain, linkedin_company_url)
    cached = NEWS_CACHE.get(cache_k)
    if cached:
        ts, data = cached
        import time
        if time.time() - ts < NEWS_CACHE_TTL:
            return data

    if progress_cb:
        progress_cb("Fetching latest news about the agency...")

    tasks = [
        afetch_google_news_rss(agency_name, website_domain, progress_cb),
        afetch_ddg_news(agency_name, website_domain, progress_cb),
        afetch_linkedin_posts(linkedin_company_url, agency_name, website_domain, progress_cb),
    ]

    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    seen_urls: set = set()
    merged: List[Dict[str, str]] = []
    for res in results_list:
        if isinstance(res, Exception):
            continue
        for item in res:
            url = item.get("url", "").rstrip("/")
            if url and url not in seen_urls:
                seen_urls.add(url)
                merged.append(item)

    def _sort_key(item: Dict[str, str]) -> str:
        d = item.get("date", "")
        return d if d else "0000-00-00"

    merged.sort(key=_sort_key, reverse=True)

    result = merged[:10]

    import time
    NEWS_CACHE[cache_k] = (time.time(), result)

    return result
