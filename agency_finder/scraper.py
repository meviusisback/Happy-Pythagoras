import asyncio
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import logging
import re
from typing import Dict, Set, List, Any
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
    "medium.com", "wordpress.org", "w3.org", "schema.org", "optimizely.com",
    "cookiebot.com", "iubenda.com", "google-analytics.com", "googletagmanager.com",
}
