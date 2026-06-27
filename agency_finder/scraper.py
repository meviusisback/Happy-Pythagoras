import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import logging
import re
import time
from typing import Dict, Set, List, Any
from .config import Config

logger = logging.getLogger("agency_finder.scraper")

class WebScraper:
    def __init__(self, base_url: str):
        self.base_url = self._normalize_base_url(base_url)
        self.domain = urlparse(self.base_url).netloc
        self.visited_urls: Set[str] = set()
        self.scraped_pages: List[Dict[str, Any]] = []
        self.session = requests.Session()
        self.session.headers.update(Config.get_headers())

    def _normalize_base_url(self, url: str) -> str:
        """Ensure the URL has a scheme (http/https)."""
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def is_same_domain(self, url: str) -> bool:
        """Check if the URL belongs to the target domain or its subdomains."""
        parsed = urlparse(url)
        return parsed.netloc == self.domain or parsed.netloc.endswith("." + self.domain)

    def clean_url(self, url: str) -> str:
        """Clean URL by removing query strings and fragments."""
        parsed = urlparse(url)
        # Reconstruct URL without query parameters or fragments
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def get_internal_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extract all valid internal links from a BeautifulSoup object."""
        links = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            # Ignore anchor links, javascript, telephone, mailto
            if href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
                continue
            
            full_url = urljoin(current_url, href)
            cleaned_url = self.clean_url(full_url)
            
            if self.is_same_domain(cleaned_url) and cleaned_url not in self.visited_urls:
                links.append(cleaned_url)
        return list(set(links))

    def get_external_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extract all outgoing links (potential portfolio websites)."""
        links = []
        # Common social media or tool domains to ignore in portfolio lookup
        ignore_domains = {
            "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
            "youtube.com", "vimeo.com", "github.com", "google.com", "maps.google.com",
            "apple.com", "microsoft.com", "whatsapp.com", "t.me", "telegram.org",
            "pinterest.com", "tiktok.com", "adobe.com", "behance.net", "dribbble.com",
            "medium.com", "wordpress.org", "w3.org", "schema.org", "optimizely.com",
            "cookiebot.com", "iubenda.com", "google-analytics.com", "googletagmanager.com"
        }
        
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:", "sms:")):
                continue
            
            full_url = urljoin(current_url, href)
            parsed = urlparse(full_url)
            
            # Check if it is an external link
            if parsed.netloc and not self.is_same_domain(full_url):
                domain = parsed.netloc.lower()
                # Remove 'www.' prefix for filtering
                clean_domain = domain[4:] if domain.startswith("www.") else domain
                
                # Check if it belongs to ignored domains
                is_ignored = any(clean_domain == d or clean_domain.endswith("." + d) for d in ignore_domains)
                
                if not is_ignored:
                    # Clean out query params and fragments for external sites
                    cleaned_external = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                    links.append(cleaned_external)
        return list(set(links))

    def scrape_page(self, url: str) -> Dict[str, Any]:
        """Scrapes a single page and returns its text, html, and links."""
        try:
            logger.info(f"Scraping page: {url}")
            response = self.session.get(url, timeout=Config.TIMEOUT, allow_redirects=True)
            
            # If the response redirect took us to a different domain, handle with caution
            final_url = response.url
            if not self.is_same_domain(final_url):
                # E.g., redirected to a third-party portal or social media page
                return {}

            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                return {}

            soup = BeautifulSoup(response.content, "html.parser")
            
            # Remove script and style elements
            for element in soup(["script", "style", "noscript", "svg", "header", "footer"]):
                # Note: We keep header and footer on homepage to extract VAT and Contact Info
                # but might discard them on subpages to avoid duplicating text.
                # However, let's keep them and let extractor handle it, just remove script/style
                if element.name in ("script", "style"):
                    element.decompose()

            # Get title
            title = soup.title.get_text(strip=True) if soup.title else ""
            
            # Extract plain text content
            text = soup.get_text(separator="\n")
            # Clean extra white space
            text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
            
            # Find outbound/portfolio websites
            external_links = self.get_external_links(soup, url)
            
            # Identify page type based on URL path
            path = urlparse(url).path.lower()
            page_type = "generic"
            if any(k in path for k in ["contact", "contatt", "dove-siamo"]):
                page_type = "contact"
            elif any(k in path for k in ["serviz", "cosa-facciam", "skills", "soluzion", "competenz", "services"]):
                page_type = "services"
            elif any(k in path for k in ["portfolio", "progett", "lavori", "cases", "works", "clienti"]):
                page_type = "portfolio"
            elif any(k in path for k in ["privacy", "cookie", "legal", "note-legal"]):
                page_type = "legal"
                
            return {
                "url": url,
                "title": title,
                "text": text,
                "html": str(soup),
                "external_links": external_links,
                "type": page_type
            }
        except Exception as e:
            logger.error(f"Error scraping {url}: {str(e)}")
            return {}

    def crawl(self, progress_cb=None) -> List[Dict[str, Any]]:
        """Starts crawling from the base URL and returns all collected data."""
        homepage_url = self.clean_url(self.base_url)
        self.visited_urls.add(homepage_url)
        
        # Scrape Homepage
        if progress_cb:
            progress_cb(f"Crawling website homepage: {homepage_url}")
        homepage_data = self.scrape_page(homepage_url)
        if not homepage_data:
            # If homepage fails, try with and without www
            parsed_url = urlparse(homepage_url)
            alternative_url = ""
            if parsed_url.netloc.startswith("www."):
                alternative_url = f"{parsed_url.scheme}://{parsed_url.netloc[4:]}{parsed_url.path}"
            else:
                alternative_url = f"{parsed_url.scheme}://www.{parsed_url.netloc}{parsed_url.path}"
            
            logger.info(f"Homepage failed. Trying alternative URL: {alternative_url}")
            if progress_cb:
                progress_cb(f"Homepage failed. Trying alternative: {alternative_url}")
            self.visited_urls.add(self.clean_url(alternative_url))
            homepage_data = self.scrape_page(alternative_url)
            
            if not homepage_data:
                logger.warning(f"Could not reach homepage: {self.base_url}")
                return []
        
        self.scraped_pages.append(homepage_data)
        
        # Parse internal links from homepage
        soup = BeautifulSoup(homepage_data["html"], "html.parser")
        internal_links = self.get_internal_links(soup, homepage_data["url"])
        
        # Queue internal links for crawling. Prioritize contact, services, and portfolio pages
        priority_links = []
        other_links = []
        
        for link in internal_links:
            path = urlparse(link).path.lower()
            is_priority = any(k in path for k in [
                "contact", "contatt", "serviz", "cosa-facciam", "skills",
                "portfolio", "progett", "lavori", "cases", "works", "chi-siam", "about"
            ])
            if is_priority:
                priority_links.append(link)
            else:
                other_links.append(link)
                
        crawl_queue = priority_links + other_links
        
        # Process the crawl queue (up to max crawl limits)
        max_pages = Config.MAX_PAGES
        for url in crawl_queue:
            if len(self.scraped_pages) >= max_pages:
                break
            
            if url in self.visited_urls:
                continue
                
            self.visited_urls.add(url)
            time.sleep(0.3)  # Polite delay
            
            if progress_cb:
                progress_cb(f"Crawling subpage ({len(self.scraped_pages) + 1}/{max_pages}): {url}")
            page_data = self.scrape_page(url)
            if page_data:
                self.scraped_pages.append(page_data)
                
        return self.scraped_pages
