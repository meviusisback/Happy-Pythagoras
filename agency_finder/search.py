import requests
import time
import logging
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Any
from .config import Config

logger = logging.getLogger("agency_finder.search")

def search_query(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Performs a web search using the configured search engine and returns
    a list of results containing 'title', 'link', and 'snippet'.
    """
    # 1. Try SerpAPI if key is configured
    if Config.SEARCH_ENGINE == "serpapi" or (Config.SERPAPI_KEY and Config.SEARCH_ENGINE == "duckduckgo"):
        if Config.SERPAPI_KEY:
            results = _search_serpapi(query, max_results)
            if results:
                return results
            logger.warning("SerpAPI failed or returned no results, falling back.")

    # 2. Try Google Custom Search if API Key & CX are configured
    if Config.SEARCH_ENGINE == "google" or (Config.GOOGLE_API_KEY and Config.GOOGLE_CX):
        if Config.GOOGLE_API_KEY and Config.GOOGLE_CX:
            results = _search_google_custom(query, max_results)
            if results:
                return results
            logger.warning("Google Custom Search failed or returned no results, falling back.")

    # 3. Default/Fallback: DuckDuckGo HTML scraper (thread-safe, zero deadlock risk)
    return _search_ddg_html(query, max_results)


def _search_duckduckgo(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Queries DuckDuckGo using the duckduckgo_search library.
    Falls back to direct HTML scraping of DuckDuckGo Lite/HTML if the library fails or returns no results.
    """
    try:
        from duckduckgo_search import DDGS
        
        results = []
        # Add delay to avoid aggressive rate limiting
        time.sleep(0.5)
        
        with DDGS(timeout=Config.TIMEOUT) as ddgs:
            ddg_results = ddgs.text(query, max_results=max_results)
            if ddg_results:
                for r in ddg_results:
                    results.append({
                        "title": r.get("title", ""),
                        "link": r.get("href", ""),
                        "snippet": r.get("body", "")
                    })
        if results:
            return results
    except Exception as e:
        logger.error(f"DuckDuckGo library search error: {str(e)}")
        
    # Fallback to direct HTML scraper
    logger.info(f"DuckDuckGo library search returned no results. Falling back to direct HTML scraping for query: {query}")
    return _search_ddg_html(query, max_results)


def _search_ddg_html(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Directly scrapes html.duckduckgo.com/html/ which requires no JavaScript or API keys.
    """
    results = []
    url = "https://html.duckduckgo.com/html/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "it,en-US;q=0.7,en;q=0.3",
        "Referer": "https://duckduckgo.com/"
    }
    params = {"q": query}
    
    try:
        # Prevent rapid requests
        time.sleep(0.5)
        response = requests.get(url, params=params, headers=headers, timeout=Config.TIMEOUT)
        
        if response.status_code != 200:
            logger.warning(f"DuckDuckGo HTML search returned HTTP {response.status_code}")
            return []
            
        soup = BeautifulSoup(response.content, "html.parser")
        search_divs = soup.find_all("div", class_="result")
        
        for div in search_divs[:max_results]:
            # Title & Link
            title_a = div.find("a", class_="result__a")
            if not title_a:
                continue
                
            title = title_a.get_text(strip=True)
            raw_link = title_a.get("href", "")
            
            # Parse link redirects (uddg parameter extraction)
            link = raw_link
            if "/l/?" in raw_link:
                parsed_url = urllib.parse.urlparse(raw_link)
                queries = urllib.parse.parse_qs(parsed_url.query)
                if "uddg" in queries:
                    link = queries["uddg"][0]
            
            # Snippet
            snippet_a = div.find("a", class_="result__snippet")
            snippet = snippet_a.get_text(strip=True) if snippet_a else ""
            
            results.append({
                "title": title,
                "link": link,
                "snippet": snippet
            })
            
        return results
    except Exception as e:
        logger.error(f"DuckDuckGo HTML scrape error: {str(e)}")
        return []


def _search_serpapi(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Queries SerpAPI for Google Search results.
    """
    try:
        url = "https://serpapi.com/search"
        params = {
            "q": query,
            "api_key": Config.SERPAPI_KEY,
            "engine": "google",
            "num": max_results,
            "hl": "it",
            "gl": "it"
        }
        response = requests.get(url, params=params, timeout=Config.TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            organic = data.get("organic_results", [])
            results = []
            for item in organic[:max_results]:
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", "")
                })
            return results
        else:
            logger.error(f"SerpAPI HTTP Error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"SerpAPI search error: {str(e)}")
    return []


def _search_google_custom(query: str, max_results: int = 10) -> List[Dict[str, str]]:
    """
    Queries the official Google Custom Search JSON API.
    """
    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "key": Config.GOOGLE_API_KEY,
            "cx": Config.GOOGLE_CX,
            "num": min(max_results, 10),  # Google API limit for num is 10
            "hl": "it"
        }
        response = requests.get(url, params=params, timeout=Config.TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            items = data.get("items", [])
            results = []
            for item in items:
                results.append({
                    "title": item.get("title", ""),
                    "link": item.get("link", ""),
                    "snippet": item.get("snippet", "")
                })
            return results
        else:
            logger.error(f"Google Custom Search HTTP Error {response.status_code}: {response.text}")
    except Exception as e:
        logger.error(f"Google Custom Search error: {str(e)}")
    return []
