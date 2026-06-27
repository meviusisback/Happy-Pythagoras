import requests
import time
import logging
import urllib.parse
import hashlib
import random
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
from .config import Config

logger = logging.getLogger("agency_finder.search")

search_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
CACHE_TTL = 300

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

last_search_error: Optional[str] = None


def _cache_key(query: str, max_results: int) -> str:
    return hashlib.md5(f"{query}:{max_results}".encode()).hexdigest()


def _cache_get(query: str, max_results: int) -> Optional[List[Dict[str, str]]]:
    key = _cache_key(query, max_results)
    entry = search_cache.get(key)
    if entry:
        ts, results = entry
        if time.time() - ts < CACHE_TTL:
            return results
        del search_cache[key]
    return None


def _cache_set(query: str, max_results: int, results: List[Dict[str, str]]):
    key = _cache_key(query, max_results)
    search_cache[key] = (time.time(), results)


def _rand_headers() -> Dict[str, str]:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it,en-US;q=0.7,en;q=0.3",
    }


def _retry(
    url: str,
    method: str = "GET",
    *,
    params: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: Optional[int] = None,
) -> Optional[requests.Response]:
    max_retries = 3
    timeout = timeout or Config.TIMEOUT

    for attempt in range(max_retries + 1):
        try:
            if method == "GET":
                resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            elif method == "POST":
                resp = requests.post(url, data=params, headers=headers, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")

            if resp.status_code == 429:
                wait = (2 ** attempt) * 2
                logger.warning(f"Rate limited (429) on {url}. Retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                continue

            if resp.status_code >= 500 and attempt < max_retries:
                wait = (2 ** attempt) * 2
                logger.warning(f"Server error {resp.status_code} on {url}. Retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                continue

            return resp

        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt < max_retries:
                wait = (2 ** attempt) * 2
                logger.warning(f"Network error on {url}: {e}. Retry {attempt+1}/{max_retries} in {wait}s")
                time.sleep(wait)
                continue
            return None

        except Exception as e:
            logger.error(f"Request error on {url}: {e}")
            return None

    return None


def _is_captcha(text: str) -> bool:
    lower = text.lower()
    triggers = [
        "captcha", "verify you are human", "verify your identity",
        "blocked", "access denied", "too many requests",
        "please try again later", "automated queries",
    ]
    return any(t in lower for t in triggers)


def _set_error(msg: str):
    global last_search_error
    last_search_error = msg
    logger.warning(msg)


def search_query(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    global last_search_error
    last_search_error = None

    cached = _cache_get(query, max_results)
    if cached is not None:
        logger.debug(f"Cache hit for query: {query[:60]}")
        return cached

    if Config.SEARCH_ENGINE == "serpapi" or (Config.SERPAPI_KEY and Config.SEARCH_ENGINE == "duckduckgo"):
        if Config.SERPAPI_KEY:
            results = _search_serpapi(query, max_results)
            if results:
                _cache_set(query, max_results, results)
                return results
            logger.warning("SerpAPI returned no results, falling back.")

    if Config.SEARCH_ENGINE == "google" or (Config.GOOGLE_API_KEY and Config.GOOGLE_CX):
        if Config.GOOGLE_API_KEY and Config.GOOGLE_CX:
            results = _search_google_custom(query, max_results)
            if results:
                _cache_set(query, max_results, results)
                return results
            logger.warning("Google Custom Search returned no results, falling back.")

    results = _search_duckduckgo(query, max_results)
    if results:
        _cache_set(query, max_results, results)
        return results

    results = _search_ddg_lite(query, max_results)
    if results:
        _cache_set(query, max_results, results)
        return results

    results = _search_ddg_html(query, max_results)
    if results:
        _cache_set(query, max_results, results)
        return results

    results = _search_bing_html(query, max_results)
    if results:
        _cache_set(query, max_results, results)
        return results

    _set_error("All search backends returned no results. Try again later or configure SerpAPI/Google API keys in the sidebar.")
    return []


def _search_ddg_lite(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    url = "https://lite.duckduckgo.com/lite/"
    headers = _rand_headers()
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    headers["Referer"] = "https://lite.duckduckgo.com/"

    try:
        time.sleep(0.5)
        response = _retry(url, method="POST", params={"q": query}, headers=headers)

        if response is None:
            _set_error("DuckDuckGo Lite search failed after retries (network error).")
            return []

        if response.status_code != 200:
            _set_error(f"DuckDuckGo Lite search returned HTTP {response.status_code}.")
            return []

        if _is_captcha(response.text):
            _set_error("DuckDuckGo is blocking the search (CAPTCHA or rate limit).")
            return []

        soup = BeautifulSoup(response.content, "html.parser")
        links = soup.find_all("a", class_="result-link")

        if not links:
            _set_error("DuckDuckGo Lite returned no results for this query.")
            return []

        results = []
        snippets = soup.find_all("td", class_="result-snippet")

        for idx, link_tag in enumerate(links[:max_results]):
            title = link_tag.get_text(strip=True)
            link = link_tag.get("href", "")

            snippet = ""
            if idx < len(snippets):
                snippet = snippets[idx].get_text().strip()
                snippet = re.sub(r"\s+", " ", snippet)

            results.append({
                "title": title,
                "link": link,
                "snippet": snippet,
            })

        return results
    except Exception as e:
        logger.error(f"DuckDuckGo Lite scrape error: {e}")
        return []


def _search_duckduckgo(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    try:
        import os as _os
        try:
            import certifi as _certifi
            _os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
        except Exception:
            pass

        from ddgs import DDGS

        time.sleep(0.5)

        with DDGS(timeout=Config.TIMEOUT) as ddgs:
            ddg_results = list(ddgs.text(query, max_results=max_results))
            if ddg_results:
                results = []
                for r in ddg_results:
                    results.append({
                        "title": r.get("title", ""),
                        "link": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    })
                if results:
                    return results
    except Exception as e:
        logger.error(f"DuckDuckGo library error: {e}")

    logger.info("DDG library returned no results, falling back to HTML scraping.")
    return _search_ddg_html(query, max_results)


def _search_ddg_html(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    url = "https://html.duckduckgo.com/html/"
    headers = _rand_headers()
    headers["Referer"] = "https://duckduckgo.com/"
    params = {"q": query}

    try:
        time.sleep(1.0)
        response = _retry(url, params=params, headers=headers)

        if response is None:
            _set_error("DuckDuckGo HTML search failed after retries (network error).")
            return []

        if response.status_code != 200:
            _set_error(f"DuckDuckGo HTML search returned HTTP {response.status_code}.")
            return []

        if _is_captcha(response.text):
            _set_error("DuckDuckGo is blocking the search (CAPTCHA or rate limit). Try again later or use a different search backend.")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        search_divs = soup.find_all("div", class_="result")

        if not search_divs:
            if soup.find("div", class_="no-results"):
                _set_error("DuckDuckGo returned no results for this query.")
            else:
                _set_error("DuckDuckGo HTML structure may have changed — could not parse results.")
            return []

        results = []
        for div in search_divs[:max_results]:
            title_a = div.find("a", class_="result__a")
            if not title_a:
                continue

            title = title_a.get_text(strip=True)
            raw_link = title_a.get("href", "")

            link = raw_link
            if "/l/?" in raw_link:
                parsed_url = urllib.parse.urlparse(raw_link)
                queries = urllib.parse.parse_qs(parsed_url.query)
                if "uddg" in queries:
                    link = queries["uddg"][0]

            snippet_a = div.find("a", class_="result__snippet")
            snippet = snippet_a.get_text(strip=True) if snippet_a else ""

            results.append({
                "title": title,
                "link": link,
                "snippet": snippet,
            })

        return results
    except Exception as e:
        logger.error(f"DuckDuckGo HTML scrape error: {e}")
        return []


def _search_bing_html(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    url = "https://www.bing.com/search"
    headers = _rand_headers()
    headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.8"
    params = {"q": query, "count": min(max_results, 20)}

    try:
        time.sleep(1.0)
        response = _retry(url, params=params, headers=headers)

        if response is None:
            return []

        if response.status_code != 200:
            logger.warning(f"Bing search returned HTTP {response.status_code}")
            return []

        if _is_captcha(response.text):
            logger.warning("Bing is blocking the search request.")
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results = []

        for el in soup.select("li.b_algo")[:max_results]:
            title_link = el.select_one("h2 a")
            if not title_link:
                continue

            title = title_link.get_text(strip=True)
            link = title_link.get("href", "")

            snippet_el = el.select_one(".b_caption p") or el.select_one("p.b_lineclamp2")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""

            if title and link:
                results.append({
                    "title": title,
                    "link": link,
                    "snippet": snippet,
                })

        return results
    except Exception as e:
        logger.error(f"Bing HTML scrape error: {e}")
        return []


def _search_serpapi(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    try:
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "api_key": Config.SERPAPI_KEY,
            "engine": "google",
            "num": max_results,
            "hl": "it",
            "gl": "it",
        }
        response = _retry(url, params=params)

        if response and response.status_code == 200:
            data = response.json()
            results = []
            for item in data.get("organic_results", [])[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results
        elif response:
            logger.error(f"SerpAPI HTTP {response.status_code}: {response.text[:200]}")
        else:
            logger.error("SerpAPI request failed (no response).")
    except Exception as e:
        logger.error(f"SerpAPI search error: {e}")
    return []


def _search_google_custom(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "key": Config.GOOGLE_API_KEY,
            "cx": Config.GOOGLE_CX,
            "num": min(max_results, 10),
            "hl": "it",
        }
        response = _retry(url, params=params)

        if response and response.status_code == 200:
            data = response.json()
            results = []
            for item in data.get("items", []):
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                })
            return results
        elif response:
            logger.error(f"Google Custom Search HTTP {response.status_code}")
        else:
            logger.error("Google Custom Search request failed (no response).")
    except Exception as e:
        logger.error(f"Google Custom Search error: {e}")
    return []
