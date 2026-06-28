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
    headers = _browser_headers("ddg")
    for backend in ("auto", "html", "lite"):
        try:
            def _run():
                with DDGS(headers=headers, timeout=Config.TIMEOUT) as ddgs:
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


def _slugify_name(name: str) -> Tuple[str, str]:
    from .utils import strip_diacritics
    cleaned = strip_diacritics(name.lower())
    no_space = re.sub(r"[^a-z0-9]", "", cleaned)
    hyphen = re.sub(r"[^a-z0-9]+", "-", cleaned).strip("-")
    return no_space, hyphen


def _extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip())
    return ""


async def _aguess_direct_domains(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    first_token = query.split()[0] if query else ""
    name = re.sub(r"[^A-Za-zÀ-ÿ0-9 ]", "", first_token).strip()
    if len(name) < 3:
        return []
    no_space, hyphen = _slugify_name(name)
    if not no_space:
        return []
    candidates = [
        f"https://{no_space}.it",
        f"https://www.{no_space}.it",
        f"https://{hyphen}.it",
        f"https://www.{hyphen}.it",
        f"https://{no_space}.com",
        f"https://{hyphen}.com",
    ]
    seen = set()
    for url in candidates:
        if url in seen or not url:
            continue
        seen.add(url)
        try:
            resp = await _aretry(url, method="GET", timeout=6, max_retries=0)
            if resp and resp.status_code == 200 and not _is_captcha(resp.text):
                title = _extract_title(resp.text) or url
                return [{"title": title, "link": url, "snippet": "Direct domain guess"}]
        except Exception:
            continue
    return []


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
    tasks.append(asyncio.create_task(_asearch_bing_html(query, max_results)))
    tasks.append(asyncio.create_task(_aguess_direct_domains(query, max_results)))

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED, timeout=15)
    except (asyncio.TimeoutError, TimeoutError):
        for t in tasks:
            t.cancel()
        done, pending = set(), set()

    for task in done:
        try:
            result = task.result()
            if result:
                _cache_set(query, max_results, result)
                return result
        except Exception:
            continue

    for p in pending:
        p.cancel()

    _set_error("All search backends returned no results. Try again later or configure SerpAPI/Google API keys in the sidebar.")
    return []


def search_query(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    return asyncio.run(asearch_query(query, max_results))
