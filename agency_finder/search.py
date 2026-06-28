import asyncio
import httpx
import time
import logging
import urllib.parse
import hashlib
import random
import re
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional, Tuple
from .config import Config
from .utils import _browser_headers

logger = logging.getLogger("agency_finder.search")

try:
    from ddgs import DDGS
    from ddgs.exceptions import RatelimitException, TimeoutException as DDGTimeoutException
    _DDG_EXCEPTIONS_AVAILABLE = True
except ImportError:
    DDGS = None
    RatelimitException = Exception
    DDGTimeoutException = Exception
    _DDG_EXCEPTIONS_AVAILABLE = False

search_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
CACHE_TTL = 300

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


def _rand_headers(backend: str = "google") -> Dict[str, str]:
    return _browser_headers(backend)


async def _aretry(
    url: str,
    method: str = "GET",
    *,
    params: Optional[Dict] = None,
    data: Optional[Dict] = None,
    headers: Optional[Dict] = None,
    timeout: Optional[int] = None,
    max_retries: int = 1,
) -> Optional[httpx.Response]:
    timeout = timeout or 4

    for attempt in range(max_retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, http2=True) as client:
                if method == "GET":
                    resp = await client.get(url, params=params, headers=headers)
                elif method == "POST":
                    resp = await client.post(url, data=data, headers=headers)
                else:
                    raise ValueError(f"Unsupported method: {method}")

            if resp.status_code == 429:
                if attempt < max_retries:
                    wait = (2 ** attempt) * 2
                    logger.warning(f"Rate limited (429) on {url}. Retry {attempt+1}/{max_retries} in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                return None

            if resp.status_code >= 500 and attempt < max_retries:
                wait = (2 ** attempt) * 2
                logger.warning(f"Server error {resp.status_code} on {url}. Retry {attempt+1}/{max_retries} in {wait}s")
                await asyncio.sleep(wait)
                continue

            return resp

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            if attempt < max_retries:
                logger.warning(f"Network error on {url}: {e}. Retry {attempt+1}/{max_retries}")
                continue
            return None

        except Exception as e:
            logger.error(f"Request error on {url}: {e}")
            return None

    return None


def _is_captcha(text: str) -> bool:
    """Detect captcha/block pages. Uses conservative triggers to avoid false positives."""
    if len(text) > 20000:
        return False
    lower = text.lower()
    strong_triggers = [
        "verify you are human", "verify your identity",
        "access denied", "too many requests",
        "please try again later", "automated queries",
        "are you a robot", "human verification",
        "security check", "please complete the security check",
    ]
    if any(t in lower for t in strong_triggers):
        return True
    if "captcha" in lower and len(text) < 5000:
        return True
    return False


def _set_error(msg: str):
    global last_search_error
    last_search_error = msg
    logger.warning(msg)


async def _asearch_ddg_lite(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    url = "https://lite.duckduckgo.com/lite/"

    for attempt in range(2):
        headers = _rand_headers("ddg")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Referer"] = "https://lite.duckduckgo.com/"

        try:
            response = await _aretry(url, method="POST", data={"q": query}, headers=headers)
            if response is None:
                if attempt == 0:
                    logger.warning("DuckDuckGo Lite returned no response, retrying with fresh headers")
                    await asyncio.sleep(0.5)
                    continue
                _set_error("DuckDuckGo Lite search failed after retries (network error).")
                return []
            if response.status_code != 200:
                if response.status_code == 202:
                    logger.warning("DuckDuckGo Lite returned HTTP 202 (challenge page) — not retrying")
                    return []
                if attempt == 0:
                    logger.warning(f"DuckDuckGo Lite HTTP {response.status_code}, retrying")
                    await asyncio.sleep(0.5)
                    continue
                _set_error(f"DuckDuckGo Lite search returned HTTP {response.status_code}.")
                return []
            if _is_captcha(response.text):
                if attempt == 0:
                    logger.warning("DuckDuckGo Lite captcha, retrying with fresh headers")
                    await asyncio.sleep(0.5)
                    continue
                _set_error("DuckDuckGo is blocking the search (CAPTCHA or rate limit).")
                return []

            soup = BeautifulSoup(response.content, "html.parser")
            links = soup.find_all("a", class_="result-link")
            if not links:
                if attempt == 0:
                    logger.warning("DuckDuckGo Lite no results, retrying with fresh headers")
                    await asyncio.sleep(0.5)
                    continue
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
                results.append({"title": title, "link": link, "snippet": snippet})
            return results
        except Exception as e:
            if attempt == 0:
                logger.warning(f"DuckDuckGo Lite error: {e}, retrying")
                await asyncio.sleep(0.5)
                continue
            logger.error(f"DuckDuckGo Lite scrape error: {e}")
            return []
    return []


async def _asearch_duckduckgo(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    for backend in ("auto", "html", "lite"):
        try:
            def _run():
                try:
                    with DDGS(timeout=Config.TIMEOUT) as ddgs:
                        return list(ddgs.text(query, region="it-it", backend=backend, max_results=max_results))
                except TypeError:
                    with DDGS() as ddgs:
                        return list(ddgs.text(query, region="it-it", backend=backend, max_results=max_results))

            ddg_results = await asyncio.to_thread(_run)
            if ddg_results:
                return [
                    {
                        "title": r.get("title", ""),
                        "link": r.get("href", ""),
                        "snippet": r.get("body", ""),
                    }
                    for r in ddg_results
                ]
            logger.info(f"DDG backend '{backend}' returned no results")
        except RatelimitException:
            logger.warning(f"DDG backend '{backend}' rate limited")
        except DDGTimeoutException:
            logger.warning(f"DDG backend '{backend}' timed out")
        except Exception as e:
            logger.warning(f"DDG backend '{backend}' error: {e}")
        await asyncio.sleep(0.5)
    return []


async def _asearch_ddg_html(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    url = "https://html.duckduckgo.com/html/"

    for attempt in range(2):
        headers = _rand_headers("ddg")
        headers["Referer"] = "https://duckduckgo.com/"

        try:
            response = await _aretry(url, params={"q": query}, headers=headers)
            if response is None:
                if attempt == 0:
                    logger.warning("DuckDuckGo HTML returned no response, retrying with fresh headers")
                    await asyncio.sleep(0.5)
                    continue
                _set_error("DuckDuckGo HTML search failed after retries (network error).")
                return []
            if response.status_code != 200:
                if response.status_code == 202:
                    logger.warning("DuckDuckGo HTML returned HTTP 202 (challenge page) — not retrying")
                    return []
                if attempt == 0:
                    logger.warning(f"DuckDuckGo HTML HTTP {response.status_code}, retrying")
                    await asyncio.sleep(0.5)
                    continue
                _set_error(f"DuckDuckGo HTML search returned HTTP {response.status_code}.")
                return []
            if _is_captcha(response.text):
                if attempt == 0:
                    logger.warning("DuckDuckGo HTML captcha, retrying with fresh headers")
                    await asyncio.sleep(0.5)
                    continue
                _set_error("DuckDuckGo is blocking the search (CAPTCHA or rate limit). Try again later or use a different search backend.")
                return []

            soup = BeautifulSoup(response.text, "html.parser")
            search_divs = soup.find_all("div", class_="result")
            if not search_divs:
                if attempt == 0:
                    logger.warning("DuckDuckGo HTML no results, retrying with fresh headers")
                    await asyncio.sleep(0.5)
                    continue
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
                results.append({"title": title, "link": link, "snippet": snippet})
            return results
        except Exception as e:
            if attempt == 0:
                logger.warning(f"DuckDuckGo HTML error: {e}, retrying")
                await asyncio.sleep(0.5)
                continue
            logger.error(f"DuckDuckGo HTML scrape error: {e}")
            return []
    return []


_ITALIAN_ARTICLES = {"di", "del", "della", "dello", "dei", "degli", "delle", "e", "ed", "the", "and"}


def _slugify_name(name: str) -> Tuple[str, str]:
    from .utils import strip_diacritics
    cleaned = strip_diacritics(name.lower())
    no_articles = re.sub(r"\b(" + "|".join(_ITALIAN_ARTICLES) + r")\b", "", cleaned)
    no_articles = re.sub(r"\s+", " ", no_articles).strip()
    no_space = re.sub(r"[^a-z0-9]", "", cleaned)
    hyphen = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    no_space_na = re.sub(r"[^a-z0-9]", "", no_articles)
    hyphen_na = re.sub(r"[^a-z0-9]+", "-", no_articles).strip("-")
    variants = {no_space, hyphen}
    if no_space_na and no_space_na != no_space:
        variants.add(no_space_na)
    if hyphen_na and hyphen_na != hyphen:
        variants.add(hyphen_na)
    ordered = sorted(variants, key=len)
    return ordered[0] if ordered else no_space, ordered[1] if len(ordered) > 1 else hyphen


_SEARCH_ONLY_MODIFIERS = [
    "web agency contatti", "web agency", "agenzia web",
    "partita iva", "p.iva", "p. iva",
    "linkedin", "contatti", "contatto",
    "italia", "italy", "italian", "italiano",
    "milano", "milan", "roma", "rome", "torino", "turin", "napoli", "naples",
    "srl", "s.r.l", "s.r.l.", "spa", "s.p.a", "s.p.a.", "snc", "s.n.c",
]

_DOMAIN_RELEVANT_MODIFIERS = [
    "agenzia", "studio", "digitale", "digital", "comunicazione", "marketing",
]


def _clean_agency_name(query: str) -> str:
    """Strip search-only modifiers to get the bare agency name (keeps domain-relevant terms)."""
    cleaned = query
    for mod in _SEARCH_ONLY_MODIFIERS:
        cleaned = re.sub(r"(?i)\b" + re.escape(mod) + r"\b", "", cleaned)
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ0-9 ]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_agency_name_stripped(query: str) -> str:
    """Strip ALL modifiers including domain-relevant ones (for search query use)."""
    cleaned = query
    for mod in _SEARCH_ONLY_MODIFIERS + _DOMAIN_RELEVANT_MODIFIERS:
        cleaned = re.sub(r"(?i)\b" + re.escape(mod) + r"\b", "", cleaned)
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ0-9 ]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _generate_name_variants(query: str) -> List[str]:
    """Generate multiple cleaned name variants for domain guessing.

    Returns unique cleaned names, e.g. for "Studio Web Creativo Milano":
    - "Studio Web Creativo" (keeps domain-relevant terms, strips city)
    - "Web Creativo" (strips everything)
    """
    full = _clean_agency_name(query)
    stripped = _clean_agency_name_stripped(query)
    variants = []
    for v in (full, stripped):
        v = v.strip()
        if v and len(v) >= 3 and v not in variants:
            variants.append(v)
    return variants or [full] if full else []


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())
    return ""


def _name_appears_in_page(name_lower: str, name_words: List[str], html: str) -> bool:
    """Check if the agency name appears in the page title or body text."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).lower().strip() if title_match else ""
    if name_lower in title:
        return True
    if name_words and sum(1 for w in name_words if w in title) >= max(2, len(name_words) - 1):
        return True
    body = re.sub(r"<[^>]+>", " ", html[:4000]).lower()
    if name_lower in body:
        return True
    if name_words and sum(1 for w in name_words if w in body) >= max(2, len(name_words) - 1):
        return True
    return False


async def _aguess_direct_domains(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    variants = _generate_name_variants(query)
    if not variants:
        return []
    primary_name = variants[0].lower().strip()
    primary_words = [w for w in primary_name.split() if len(w) > 2]

    tlds = ["it", "com", "eu", "net", "org", "io"]
    candidates = []
    for name in variants:
        no_space, hyphen = _slugify_name(name)
        if not no_space:
            continue
        for tld in tlds:
            for slug in (no_space, hyphen):
                for prefix in ("", "www."):
                    url = f"https://{prefix}{slug}.{tld}"
                    if url not in candidates:
                        candidates.append(url)

    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            resp = await _aretry(url, method="GET", timeout=5, max_retries=0)
            if resp and resp.status_code == 200 and not _is_captcha(resp.text):
                if _name_appears_in_page(primary_name, primary_words, resp.text):
                    title = _extract_title(resp.text) or url
                    results.append({"title": title, "link": url, "snippet": "Direct domain guess"})
                    if len(results) >= 3:
                        return results
        except Exception:
            continue
    return results


async def _asearch_bing_html(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    url = "https://www.bing.com/search"
    params = {"q": query, "count": min(max_results, 20), "brdr": 1, "setmkt": "it-IT"}
    mobile_ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1"

    for attempt, ua_suffix in [(1, ""), (2, " (mobile fallback)")]:
        headers = _rand_headers("bing")
        if attempt == 2:
            headers["User-Agent"] = mobile_ua

        try:
            response = await _aretry(url, params=params, headers=headers)
            if response is None:
                if attempt == 1:
                    logger.warning("Bing search returned None — retrying with mobile UA")
                    continue
                return []
            if response.status_code != 200:
                if attempt == 1:
                    logger.warning(f"Bing search returned HTTP {response.status_code} — retrying with mobile UA")
                    continue
                return []
            if _is_captcha(response.text):
                if attempt == 1:
                    logger.warning("Bing is blocking the search request — retrying with mobile UA")
                    continue
                logger.warning("Bing search blocked even with mobile UA")
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
                    results.append({"title": title, "link": link, "snippet": snippet})
            if results:
                return results
            if attempt == 1:
                logger.warning("Bing search returned empty results — retrying with mobile UA")
                continue
            return results
        except Exception as e:
            if attempt == 1:
                logger.warning(f"Bing scrape error: {e} — retrying with mobile UA")
                continue
            logger.error(f"Bing HTML scrape error (mobile): {e}")
            return []


async def _asearch_serpapi(query: str, max_results: int = 10) -> List[Dict[str, str]]:
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
        response = await _aretry(url, params=params)
        if response and response.status_code == 200:
            data = response.json()
            return [
                {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
                for item in data.get("organic_results", [])[:max_results]
            ]
        elif response:
            logger.error(f"SerpAPI HTTP {response.status_code}: {response.text[:200]}")
        else:
            logger.error("SerpAPI request failed (no response).")
    except Exception as e:
        logger.error(f"SerpAPI search error: {e}")
    return []


async def _asearch_google_custom(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "key": Config.GOOGLE_API_KEY,
            "cx": Config.GOOGLE_CX,
            "num": min(max_results, 10),
            "hl": "it",
        }
        response = await _aretry(url, params=params)
        if response and response.status_code == 200:
            data = response.json()
            return [
                {
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", ""),
                }
                for item in data.get("items", [])
            ]
        elif response:
            logger.error(f"Google Custom Search HTTP {response.status_code}")
        else:
            logger.error("Google Custom Search request failed (no response).")
    except Exception as e:
        logger.error(f"Google Custom Search error: {e}")
    return []


async def _asearch_wikipedia(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Search Italian Wikipedia. No anti-bot, always reachable."""
    url = "https://it.wikipedia.org/w/api.php"
    try:
        response = await _aretry(
            url,
            params={
                "action": "query",
                "format": "json",
                "list": "search",
                "srsearch": query,
                "srlimit": min(max_results, 10),
                "srprop": "snippet",
            },
            timeout=8,
            max_retries=0,
        )
        if response is None or response.status_code != 200:
            return []
        data = response.json()
        results = []
        for item in data.get("query", {}).get("search", []):
            title = item.get("title", "")
            snippet_html = item.get("snippet", "")
            snippet = re.sub(r"<[^>]+>", "", snippet_html)
            snippet = re.sub(r"\s+", " ", snippet).strip()
            page_url = "https://it.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            results.append({"title": title, "link": page_url, "snippet": snippet})
        return results
    except Exception as e:
        logger.warning(f"Wikipedia search error: {e}")
        return []


async def _asearch_mojeek(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """Search Mojeek (privacy-focused, minimal anti-bot)."""
    url = "https://www.mojeek.com/search"
    headers = _rand_headers("google")
    try:
        response = await _aretry(url, params={"q": query}, headers=headers, timeout=8, max_retries=0)
        if response is None or response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for li in soup.select("li.ob")[:max_results]:
            title_a = li.select_one("a.title")
            if not title_a:
                continue
            title = title_a.get_text(strip=True)
            link = title_a.get("href", "")
            snippet_el = li.select_one("p.s")
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            if title and link:
                results.append({"title": title, "link": link, "snippet": snippet})
        return results
    except Exception as e:
        logger.warning(f"Mojeek search error: {e}")
        return []


async def asearch_query(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    global last_search_error
    last_search_error = None

    cached = _cache_get(query, max_results)
    if cached is not None:
        logger.debug(f"Cache hit for query: {query[:60]}")
        return cached

    tasks: List[asyncio.Task] = []

    if Config.SERPAPI_KEY and (Config.SEARCH_ENGINE == "serpapi" or Config.SEARCH_ENGINE == "duckduckgo"):
        tasks.append(asyncio.create_task(_asearch_serpapi(query, max_results)))

    if Config.GOOGLE_API_KEY and Config.GOOGLE_CX and (Config.SEARCH_ENGINE == "google" or Config.SEARCH_ENGINE == "duckduckgo"):
        tasks.append(asyncio.create_task(_asearch_google_custom(query, max_results)))

    tasks.append(asyncio.create_task(_asearch_duckduckgo(query, max_results)))
    tasks.append(asyncio.create_task(_asearch_ddg_lite(query, max_results)))
    tasks.append(asyncio.create_task(_asearch_ddg_html(query, max_results)))
    tasks.append(asyncio.create_task(_asearch_mojeek(query, max_results)))
    tasks.append(asyncio.create_task(_asearch_bing_html(query, max_results)))
    tasks.append(asyncio.create_task(_asearch_wikipedia(query, max_results)))
    tasks.append(asyncio.create_task(_aguess_direct_domains(query, max_results)))

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=30,
        )
    except (asyncio.TimeoutError, TimeoutError):
        for t in tasks:
            t.cancel()
        results = []

    seen_links: set[str] = set()
    merged: List[Dict[str, str]] = []
    for result in results:
        if isinstance(result, BaseException):
            continue
        if not result:
            continue
        for r in result:
            link = r.get("link", "")
            if link and link not in seen_links:
                seen_links.add(link)
                merged.append(r)

    if merged:
        _cache_set(query, max_results, merged)
        return merged

    _set_error("All search backends returned no results. Try again later or configure SerpAPI/Google API keys in the sidebar.")
    return []


def search_query(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    return asyncio.run(asearch_query(query, max_results))
