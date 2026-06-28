import asyncio
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import logging
import re
from typing import Dict, Set, List, Any, Optional
from .config import Config
from .utils import make_async_client, USER_AGENT

logger = logging.getLogger("agency_finder.scraper")


class WebScraper:
    def __init__(self, base_url: str):
        self.base_url = self._normalize_base_url(base_url)
        self.domain = urlparse(self.base_url).netloc
        self.visited_urls: Set[str] = set()
        self.scraped_pages: List[Dict[str, Any]] = []

    def _normalize_base_url(self, url: str) -> str:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def is_same_domain(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.netloc == self.domain or parsed.netloc.endswith("." + self.domain)

    def clean_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def get_internal_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        links = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
                continue
            full_url = urljoin(current_url, href)
            cleaned_url = self.clean_url(full_url)
            if self.is_same_domain(cleaned_url) and cleaned_url not in self.visited_urls:
                links.append(cleaned_url)
        return list(set(links))

    def get_external_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        links = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
                continue
            full_url = urljoin(current_url, href)
            parsed = urlparse(full_url)
            if parsed.netloc and not self.is_same_domain(full_url):
                domain = parsed.netloc.lower()
                clean_domain = domain[4:] if domain.startswith("www.") else domain
                is_ignored = any(
                    clean_domain == d or clean_domain.endswith("." + d) for d in _IGNORE_DOMAINS
                )
                if not is_ignored:
                    cleaned_external = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    links.append(cleaned_external)
        return list(set(links))

    def _parse_page(self, url: str, html: str, final_url: str) -> Dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        for element in soup(["script", "style"]):
            element.decompose()
        title = soup.title.get_text(strip=True) if soup.title else ""
        text = soup.get_text(separator="\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        external_links = self.get_external_links(soup, url)

        path = urlparse(url).path.lower()
        page_type = "generic"
        if any(k in path for k in ["contact", "contatt", "dove-siamo"]):
            page_type = "contact"
        elif any(k in path for k in ["serviz", "cosa-facciam", "skills", "soluzion", "competenz", "services"]):
            page_type = "services"
        elif any(k in path for k in [
            "portfolio", "progett", "lavori", "cases", "case-stud", "case_stud",
            "works", "clienti", "client", "customers", "showcase",
            "referenz", "realizzazion", "our-work", "recent-work",
            "success", "stories", "testimon", "custom",
        ]):
            page_type = "portfolio"
        elif any(k in path for k in ["privacy", "cookie", "legal", "note-legal"]):
            page_type = "legal"

        return {
            "url": url,
            "title": title,
            "text": text,
            "html": str(soup),
            "external_links": external_links,
            "type": page_type,
        }

    async def ascrape_page(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        try:
            logger.info(f"Scraping page: {url}")
            response = await client.get(url, follow_redirects=True)
            final_url = str(response.url)
            if not self.is_same_domain(final_url):
                return {}
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return {}
            return self._parse_page(url, response.text, final_url)
        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")
            return {}

    def scrape_page(self, url: str) -> Dict[str, Any]:
        try:
            logger.info(f"Scraping page: {url}")
            with httpx.Client(timeout=Config.TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
                response = client.get(url, follow_redirects=True)
            final_url = str(response.url)
            if not self.is_same_domain(final_url):
                return {}
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return {}
            return self._parse_page(url, response.text, final_url)
        except Exception as e:
            logger.error(f"Error scraping {url}: {e}")
            return {}

    async def acrawl(self, progress_cb=None) -> List[Dict[str, Any]]:
        homepage_url = self.clean_url(self.base_url)
        self.visited_urls.add(homepage_url)

        async with make_async_client(timeout=Config.TIMEOUT) as client:
            if progress_cb:
                progress_cb(f"Crawling website homepage: {homepage_url}")
            homepage_data = await self.ascrape_page(homepage_url, client)
            if not homepage_data:
                parsed_url = urlparse(homepage_url)
                if parsed_url.netloc.startswith("www."):
                    alternative_url = f"{parsed_url.scheme}://{parsed_url.netloc[4:]}{parsed_url.path}"
                else:
                    alternative_url = f"{parsed_url.scheme}://www.{parsed_url.netloc}{parsed_url.path}"
                logger.info(f"Homepage failed. Trying alternative URL: {alternative_url}")
                if progress_cb:
                    progress_cb(f"Homepage failed. Trying alternative: {alternative_url}")
                self.visited_urls.add(self.clean_url(alternative_url))
                homepage_data = await self.ascrape_page(alternative_url, client)
                if not homepage_data:
                    logger.warning(f"Could not reach homepage: {self.base_url}")
                    return []

            self.scraped_pages.append(homepage_data)

            soup = BeautifulSoup(homepage_data["html"], "html.parser")
            internal_links = self.get_internal_links(soup, homepage_data["url"])

            priority_links = []
            other_links = []
            for link in internal_links:
                path = urlparse(link).path.lower()
                is_priority = any(k in path for k in [
                    "contact", "contatt", "serviz", "cosa-facciam", "skills",
                    "portfolio", "progett", "lavori", "cases", "case-stud", "case_stud",
                    "works", "clienti", "client", "customers", "showcase",
                    "referenz", "realizzazion", "our-work", "recent-work",
                    "success", "stories", "testimon",
                    "chi-siam", "about",
                ])
                if is_priority:
                    priority_links.append(link)
                else:
                    other_links.append(link)

            crawl_queue = priority_links + other_links
            max_pages = Config.MAX_PAGES
            semaphore = asyncio.Semaphore(10)

            async def _crawl_one(url: str):
                async with semaphore:
                    if url in self.visited_urls or len(self.scraped_pages) >= max_pages:
                        return None
                    self.visited_urls.add(url)
                    if progress_cb:
                        progress_cb(f"Crawling subpage ({len(self.scraped_pages) + 1}/{max_pages}): {url}")
                    return await self.ascrape_page(url, client)

            tasks = [_crawl_one(url) for url in crawl_queue if url not in self.visited_urls]
            for result in asyncio.as_completed(tasks):
                page_data = await result
                if page_data and len(self.scraped_pages) < max_pages:
                    self.scraped_pages.append(page_data)

        return self.scraped_pages

    def crawl(self, progress_cb=None) -> List[Dict[str, Any]]:
        return asyncio.run(self.acrawl(progress_cb=progress_cb))


_IGNORE_DOMAINS = {
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "vimeo.com", "github.com", "google.com", "maps.google.com",
    "apple.com", "microsoft.com", "whatsapp.com", "t.me", "telegram.org",
    "pinterest.com", "tiktok.com", "adobe.com", "behance.net", "dribbble.com",
    "medium.com", "wordpress.org", "wordpress.com", "w3.org", "schema.org", "optimizely.com",
    "cookiebot.com", "iubenda.com", "google-analytics.com", "googletagmanager.com",
    "jsdelivr.net", "unpkg.com", "bootstrapcdn.com", "cloudflare.com", "cloudfront.net",
    "amazonaws.com", "wix.com", "squarespace.com", "shopify.com", "magento.com",
    "paypal.com", "stripe.com", "schema.org", "fonts.googleapis.com", "fonts.gstatic.com",
    "maps.google", "schema.org", "garanteprivacy.it",
}


_CLIENT_FACING_KEYWORDS = [
    "portfolio", "clienti", "lavori", "work", "progetti",
    "case-study", "realizzazioni", "referenze", "references",
    "ecommerce", "negozi", "shop", "siti-realizzati", "casi-studio",
    "migrazione", "web-agency", "showcase", "success-stories",
]

_ALLOWED_CLIENT_TLDS = {"it", "com", "eu", "net", "io", "shop", "store", "biz", "co"}

_MAX_SITEMAP_PAGES = 40


def _client_tld_allowed(netloc: str) -> bool:
    clean = netloc.lower()
    if clean.startswith("www."):
        clean = clean[4:]
    parts = clean.rsplit(".", 1)
    if len(parts) != 2:
        return False
    tld = parts[1]
    return tld in _ALLOWED_CLIENT_TLDS


def _is_skipped_domain(netloc: str, agency_domain: str) -> bool:
    clean = netloc.lower()
    if clean.startswith("www."):
        clean = clean[4:]
    if clean == agency_domain:
        return True
    for d in _IGNORE_DOMAINS:
        if clean == d or clean.endswith("." + d):
            return True
    return False


def _score_portfolio_page(url: str) -> int:
    path = urlparse(url).path.lower()
    score = 0
    for kw in _CLIENT_FACING_KEYWORDS:
        if kw in path:
            score += 2
    if "/blog" in path or "/news" in path:
        score -= 3
    return score


def _normalize_agency_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _parse_sitemap_locs(xml_text: str) -> List[str]:
    urls: List[str] = []
    try:
        from xml.etree import ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception:
        return urls
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    for elem in root.iter():
        tag = elem.tag.split("}", 1)[-1]
        if tag == "loc" and elem.text:
            urls.append(elem.text.strip())
    return urls


async def _fetch_sitemap_urls(agency_url: str, client: httpx.AsyncClient) -> List[str]:
    parsed = urlparse(agency_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
    ]
    all_locs: List[str] = []
    for sitemap_url in candidates:
        try:
            resp = await client.get(sitemap_url, follow_redirects=True, timeout=8)
            if resp.status_code != 200:
                continue
            text = resp.text
            if not text or "<" not in text:
                continue
            locs = _parse_sitemap_locs(text)
            if not locs:
                continue
            if any("sitemap" in u.lower() for u in locs) and all(
                u.lower().endswith(".xml") for u in locs
            ):
                for nested in locs[:10]:
                    try:
                        r2 = await client.get(nested, follow_redirects=True, timeout=8)
                        if r2.status_code == 200:
                            all_locs.extend(_parse_sitemap_locs(r2.text))
                    except Exception:
                        continue
            else:
                all_locs.extend(locs)
            if all_locs:
                break
        except Exception:
            continue
    if not all_locs:
        try:
            resp = await client.get(agency_url, follow_redirects=True, timeout=8)
            if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
                soup = BeautifulSoup(resp.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    if href.startswith(("http://", "https://")):
                        all_locs.append(href)
        except Exception:
            pass
    return list(dict.fromkeys(all_locs))


def _extract_external_domains(html: str, agency_domain: str) -> List[Dict[str, str]]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    found: Dict[str, Dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
            continue
        if not href.startswith(("http://", "https://")):
            continue
        try:
            parsed = urlparse(href)
        except Exception:
            continue
        if not parsed.netloc:
            continue
        if _is_skipped_domain(parsed.netloc, agency_domain):
            continue
        if not _client_tld_allowed(parsed.netloc):
            continue
        clean_domain = parsed.netloc.lower()
        if clean_domain.startswith("www."):
            clean_domain = clean_domain[4:]
        if clean_domain in found:
            continue
        anchor_text = a.get_text(strip=True)[:120]
        scheme = parsed.scheme or "https"
        final_url = f"{scheme}://{clean_domain}{parsed.path}".rstrip("/")
        found[clean_domain] = {"url": final_url, "anchor": anchor_text}
    return list(found.values())


def _extract_direct_candidate_urls(html: str, agency_domain: str) -> List[Dict[str, str]]:
    """For homepage fallback: extract external links directly as candidate client URLs."""
    return _extract_external_domains(html, agency_domain)


async def _fetch_client_name(url: str, client: httpx.AsyncClient) -> str:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=6)
        if resp.status_code != 200:
            return ""
        if "text/html" not in resp.headers.get("Content-Type", ""):
            return ""
        m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    except Exception:
        pass
    return ""


def _guess_name_from_domain(domain: str) -> str:
    base = domain.split(".")[0]
    return base.replace("-", " ").replace("_", " ").title()


def _pick_best_name(anchor: str, fetched_title: str, domain: str) -> str:
    if fetched_title and len(fetched_title) > 2:
        return fetched_title[:80]
    if anchor and len(anchor) > 2 and not re.search(r"https?://|\.it|\.com|www\.|qui|leggi|scopri|vedi|read more|view", anchor.lower()):
        return anchor[:80]
    return _guess_name_from_domain(domain)


async def afind_portfolio_websites(
    name: str,
    url: str,
    max_sites: int = 25,
) -> List[Dict[str, str]]:
    """Discover client websites from the agency's sitemap + portfolio pages."""
    if not url:
        return []
    agency_url = _normalize_agency_url(url)
    agency_domain = urlparse(agency_url).netloc.lower()
    if agency_domain.startswith("www."):
        agency_domain = agency_domain[4:]

    async with make_async_client(timeout=10) as client:
        all_sitemap_urls = await _fetch_sitemap_urls(agency_url, client)
        if not all_sitemap_urls:
            return []

        agency_pages = []
        direct_candidates = []
        for u in all_sitemap_urls:
            parsed = urlparse(u)
            dom = parsed.netloc.lower()
            if dom.startswith("www."):
                dom = dom[4:]
            if dom == agency_domain:
                agency_pages.append(u)
            else:
                direct_candidates.append(u)

        ranked = sorted(agency_pages, key=_score_portfolio_page, reverse=True)
        to_crawl = ranked[:_MAX_SITEMAP_PAGES]

        semaphore = asyncio.Semaphore(10)

        async def _fetch_page_html(page_url: str) -> str:
            async with semaphore:
                try:
                    parsed = urlparse(page_url)
                    if parsed.netloc.lower().lstrip("www.") != agency_domain:
                        return ""
                    resp = await client.get(page_url, follow_redirects=True, timeout=8)
                    if resp.status_code != 200:
                        return ""
                    if "text/html" not in resp.headers.get("Content-Type", ""):
                        return ""
                    return resp.text
                except Exception:
                    return ""

        htmls = await asyncio.gather(*(_fetch_page_html(u) for u in to_crawl))

        by_domain: Dict[str, Dict[str, str]] = {}
        for cand in direct_candidates:
            entry = _extract_external_domains_from_url(cand, agency_domain)
            if entry:
                domain = urlparse(entry["url"]).netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                if domain not in by_domain:
                    by_domain[domain] = entry
        for html in htmls:
            if not html:
                continue
            for entry in _extract_external_domains(html, agency_domain):
                domain = urlparse(entry["url"]).netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                if domain in by_domain:
                    continue
                by_domain[domain] = entry

        if not by_domain:
            return []

        candidates = list(by_domain.values())[:max_sites]
        names = await asyncio.gather(
            *(_fetch_client_name(c["url"], client) for c in candidates)
        )

        results: List[Dict[str, str]] = []
        for cand, fetched_title in zip(candidates, names):
            domain = urlparse(cand["url"]).netloc.lower()
            if domain.startswith("www."):
                domain = domain[4:]
            results.append({
                "nome": _pick_best_name(cand.get("anchor", ""), fetched_title, domain),
                "url": cand["url"],
                "domain": domain,
            })
        return results


def _extract_external_domains_from_url(url: str, agency_domain: str) -> Optional[Dict[str, str]]:
    """For homepage fallback: turn an external URL directly into a candidate entry."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    if not parsed.netloc:
        return None
    if _is_skipped_domain(parsed.netloc, agency_domain):
        return None
    if not _client_tld_allowed(parsed.netloc):
        return None
    clean_domain = parsed.netloc.lower()
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]
    if clean_domain == agency_domain:
        return None
    scheme = parsed.scheme or "https"
    final_url = f"{scheme}://{clean_domain}{parsed.path}".rstrip("/")
    return {"url": final_url, "anchor": ""}
