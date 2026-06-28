import re
import asyncio
import logging
import httpx
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import Dict, List, Any, Optional, Tuple
from .search import asearch_query
from .vies import acheck_vat
from .scraper import WebScraper
from .extractor import InformationExtractor
from .utils import make_async_client, USER_AGENT, strip_diacritics
from .news import aexternal_news_lookup
from .config import Config

logger = logging.getLogger("agency_finder.core")

IGNORE_DOMAINS = {
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "youtu.be", "paginegialle.it", "paginebianche.it",
    "ufficiocamerale.it", "reportaziende.it", "registroimprese.it",
    "tuttitalia.it", "guidamonaci.it", "yelp.it", "tripadvisor.it",
    "glassdoor.it", "comuni-italiani.it",
    "wikipedia.org", "wikimedia.org", "w3.org", "schema.org",
    "google.com", "google.it",
    "github.com", "gitlab.com", "bitbucket.org",
    "europages.it", "kompass.com", "europages.com",
    "trustpilot.com", "crunchbase.com",
    "medium.com", "behance.net", "dribbble.com",
    "sortlist.it", "sortlist.com",
    "ecommerceitalia.info", "ecommerceitalia.it",
    "semrush.com", "hubspot.com",
    "yandex.com", "yandex.ru", "yandex.net",
    "tadviser.ru", "tadviser.com",
    "finance.rambler.ru", "lenta.ru",
    "klerk.ru", "tumgik.com",
    "belka.ai",
    "belkasoft.com",
    "bing.com", "duckduckgo.com", "qwant.com", "startpage.com",
    "ecosia.org", "search.brave.com", "brave.com",
    "yahoo.com", "yahoo.it", "search.yahoo.com",
    "aol.com", "ask.com", "baidu.com",
}

PARKING_SIGNALS = [
    "plesk", "cpanel", "default website", "web server's default page",
    "this domain is parked", "domain for sale",
    "coming soon", "under construction",
    "welcome to nginx", "apache2",
    "index of /",
    "questo dominio è in attesa",
    "hosted by",
]


def _is_parking_page(html: str) -> bool:
    lower = html.lower()[:5000]
    signal_count = sum(1 for s in PARKING_SIGNALS if s in lower)
    if signal_count >= 1 and len(html) < 6000:
        return True
    if len(html) < 800 and "default" in lower:
        return True
    return False


def _score_html(html: str, final_url: str, url: str, name_lower: str, name_words: List[str]) -> Dict[str, Any]:
    final_domain = urlparse(final_url).netloc.lower()
    clean_domain = final_domain[4:] if final_domain.startswith("www.") else final_domain

    if len(html) < 500:
        return {"score": -30, "url": url, "final_url": final_url, "reason": "too short"}

    if _is_parking_page(html):
        return {"score": -100, "url": url, "final_url": final_url, "reason": "parking page"}

    title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""

    text_body = re.sub(r'<[^>]+>', ' ', html)
    text_body = re.sub(r'\s+', ' ', text_body).strip()[:5000]

    score = 0
    name_combined = name_lower.replace(" ", "")

    name_in_domain = name_combined in clean_domain
    all_words_in_domain = all(w in clean_domain for w in name_words) if name_words else False
    name_in_title = name_lower in title.lower() or (
        name_words and sum(1 for w in name_words if w in title.lower()) >= max(2, len(name_words) - 1)
    )

    if not name_in_domain and not all_words_in_domain and not name_in_title:
        return {"score": -10, "url": url, "final_url": final_url, "reason": f"name not in domain/title, title='{title[:30]}'"}

    if name_in_domain:
        score += 50
    elif all_words_in_domain:
        score += 35
    elif any(w in clean_domain for w in name_words):
        if len(name_words) <= 1:
            score += 30
        elif sum(1 for w in name_words if w in clean_domain) >= len(name_words) - 1:
            score += 20
        else:
            score += 5

    if clean_domain.count(".") > 1:
        score -= 10

    if name_lower in title.lower():
        score += 10
    elif name_words and any(w in title.lower() for w in name_words):
        score += 5

    if name_lower in text_body.lower():
        score += 15

    industry_terms = ["web agency", "ecommerce", "agenzia", "digital", "software", "sviluppo", "consulenza", "sviluppo web"]
    if any(t in text_body.lower() for t in industry_terms):
        score += 5

    link_count = len(re.findall(r'href="[^"]*"', html))
    if link_count > 10:
        score += 5
    elif link_count > 3:
        score += 3

    if len(html) > 10000:
        score += 5
    elif len(html) > 3000:
        score += 3

    platform_domains = ["infobel", "yelp", "sortlist", "ecommerceitalia", "kompass", "trovaprezzi", "semrush", "alladvertising"]
    if any(p in clean_domain for p in platform_domains):
        score -= 40

    return {"score": score, "url": url, "final_url": final_url, "reason": f"score={score}, title='{title[:30]}'"}


async def _ascore_website(url: str, agency_name: str) -> Dict[str, Any]:
    name_lower = agency_name.lower().strip()
    name_words = [w for w in name_lower.split() if len(w) > 2]

    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.9"})
        final_url = str(resp.url)

        if "text/html" not in resp.headers.get("Content-Type", ""):
            return {"score": -50, "url": url, "final_url": final_url, "reason": "non-html"}

        return _score_html(resp.text, final_url, url, name_lower, name_words)
    except Exception as e:
        return {"score": -50, "url": url, "final_url": url, "reason": f"error: {e}"}


def _score_website(url: str, agency_name: str) -> Dict[str, Any]:
    name_lower = agency_name.lower().strip()
    name_words = [w for w in name_lower.split() if len(w) > 2]

    try:
        with httpx.Client(timeout=8, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.9"})
        final_url = str(resp.url)

        if "text/html" not in resp.headers.get("Content-Type", ""):
            return {"score": -50, "url": url, "final_url": final_url, "reason": "non-html"}

        return _score_html(resp.text, final_url, url, name_lower, name_words)
    except Exception as e:
        return {"score": -50, "url": url, "final_url": url, "reason": f"error: {e}"}


def _collect_candidates(results: List[Dict[str, str]]) -> List[str]:
    seen = set()
    candidates = []
    for r in results:
        link = r.get("link", "")
        parsed = urlparse(link)
        domain = parsed.netloc.lower()
        clean_domain = domain[4:] if domain.startswith("www.") else domain
        is_ignored = any(clean_domain == d or clean_domain.endswith("." + d) for d in IGNORE_DOMAINS)
        if not is_ignored and parsed.scheme in ("http", "https") and clean_domain not in seen:
            seen.add(clean_domain)
            candidates.append(f"{parsed.scheme}://{parsed.netloc}")
    return candidates


async def _acheck_vat_on_page(url: str, vat: str, name_lower: str = "") -> bool:
    """Check whether a page displays the given VAT number near the agency name."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.9"})
        if resp.status_code != 200:
            return False
        if "text/html" not in resp.headers.get("Content-Type", ""):
            return False
        text = resp.text
        if vat not in text:
            return False
        if not name_lower:
            return True
        idx = text.find(vat)
        window = text[max(0, idx - 500):idx + len(vat) + 500].lower()
        name_words = [w for w in name_lower.split() if len(w) > 2]
        if name_lower in window:
            return True
        if name_words and sum(1 for w in name_words if w in window) >= max(2, len(name_words) - 1):
            return True
        return False
    except Exception:
        return False


async def _avat_bonus(candidates: List[str], vat: str, name_lower: str = "") -> Dict[str, int]:
    """For each candidate URL, return +25 if it displays the VAT near the agency name, else 0."""
    if not candidates or not vat:
        return {}
    checks = await asyncio.gather(*[_acheck_vat_on_page(c, vat, name_lower) for c in candidates])
    return {c: (25 if found else 0) for c, found in zip(candidates, checks)}


def _has_meaningful_data(result: Dict[str, Any]) -> bool:
    return bool(result.get("emails")) or bool(result.get("telephones")) or bool(result.get("services")) or bool(result.get("extracted_address"))


async def _aextract(website: str, query_name: str, result: Dict[str, Any], progress_cb=None) -> bool:
    try:
        logger.info(f"Crawling: {website}")
        scraper = WebScraper(website)
        pages = await scraper.acrawl(progress_cb=progress_cb)

        if not pages:
            logger.warning(f"No pages crawled from {website}")
            return False

        extractor = InformationExtractor(pages)
        result["emails"] = extractor.extract_emails()
        result["telephones"] = extractor.extract_telephones()
        result["extracted_address"] = extractor.extract_address()
        result["services"] = extractor.extract_services()
        result["portfolio_sites"] = extractor.extract_client_websites_v2()
        result["payment_integration"] = extractor.extract_payment_integrations()

        try:
            from .scraper import afind_portfolio_websites
            sitemap_clients = await afind_portfolio_websites(
                name=query_name or "",
                url=website,
                max_sites=25,
            )
            if sitemap_clients:
                existing = set(result.get("portfolio_sites") or [])
                for d in sitemap_clients:
                    domain = d.get("domain", "")
                    if domain and domain not in existing:
                        result["portfolio_sites"].append(domain)
                        existing.add(domain)
                result["sitemap_portfolio"] = sitemap_clients
                logger.info(f"Sitemap portfolio discovery found {len(sitemap_clients)} clients")
        except Exception as e:
            logger.warning(f"Sitemap portfolio discovery failed: {e}")

        if query_name:
            scraped_text = " ".join(p.get("text", "") for p in pages).lower()
            name_lower = query_name.lower()
            if name_lower not in scraped_text:
                name_words = [w for w in name_lower.split() if len(w) > 3]
                if name_words and not any(w in scraped_text for w in name_words):
                    result["website_suspect"] = True
                    logger.warning(f"Website {website} may not be related to '{query_name}'")

        if not result["vat_number"] or (result["vat_number"] and not result["vies_valid"]):
            web_vat = extractor.extract_vat()
            if web_vat and web_vat != result.get("vat_number"):
                result["vat_number"] = web_vat
                logger.info(f"VAT from website: {web_vat}")
                vies_data = await acheck_vat(web_vat)
                result["vies_valid"] = vies_data.get("valid", False)
                if vies_data.get("valid"):
                    result["official_name"] = vies_data.get("company_name")
                    result["official_address"] = vies_data.get("address")
                else:
                    result["vies_error"] = vies_data.get("error")

        return True
    except Exception as e:
        logger.error(f"Failed to scrape {website}: {e}")
        return False


def _result_is_relevant(result: Dict[str, str], agency_name: str) -> bool:
    if not agency_name:
        return True
    name_lower = agency_name.lower().strip()
    haystack = (
        result.get("title", "") + " " +
        result.get("snippet", "") + " " +
        result.get("link", "")
    ).lower()
    if name_lower in haystack:
        return True
    name_words = [w for w in name_lower.split() if len(w) > 2]
    if not name_words:
        return True
    link_lower = result.get("link", "").lower()
    if any(w in link_lower for w in name_words):
        return True
    if len(name_words) <= 2:
        return any(w in haystack for w in name_words)
    matches = sum(1 for w in name_words if w in haystack)
    return matches >= max(2, len(name_words) // 2)


def _filter_relevant(results: List[Dict[str, str]], agency_name: str) -> List[Dict[str, str]]:
    return [r for r in results if _result_is_relevant(r, agency_name)]


_LINKEDIN_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def ascrape_linkedin_company_page(
    linkedin_company_url: str,
    max_results: int = 15,
) -> List[Dict[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(linkedin_company_url, headers=_LINKEDIN_HEADERS)
    except Exception as e:
        logger.debug(f"LinkedIn company page fetch failed ({linkedin_company_url}): {e}")
        return []

    if resp.status_code != 200:
        logger.debug(f"LinkedIn company page returned HTTP {resp.status_code}: {linkedin_company_url}")
        return []

    html = resp.text
    lower = html.lower()
    block_signals = ["captcha", "verify you are human", "sign in", "join linkedin"]
    if any(s in lower for s in block_signals):
        logger.debug(f"LinkedIn company page blocked (auth/captcha): {linkedin_company_url}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    seen: set = set()
    contacts: List[Dict[str, str]] = []

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "/in/" not in href:
            continue
        full_url = href if href.startswith("http") else "https://www.linkedin.com" + href
        path = urlparse(full_url).path.rstrip("/")
        if path in seen:
            continue
        seen.add(path)
        name_text = a_tag.get_text(strip=True)
        if not name_text or len(name_text) < 2:
            continue
        title_text = ""
        parent = a_tag.parent
        if parent:
            next_el = parent.find_next_sibling()
            if next_el:
                title_text = next_el.get_text(strip=True)
            elif parent.parent:
                siblings = parent.parent.find_all(["span", "p", "div"], limit=5)
                for sib in siblings:
                    t = sib.get_text(strip=True)
                    if t and t != name_text and len(t) < 100:
                        title_text = t
                        break
        contacts.append({
            "name": name_text,
            "role": title_text or "Professional",
            "url": full_url,
            "snippet": title_text,
        })
        if len(contacts) >= max_results:
            break
    return contacts


def scrape_linkedin_company_page(
    linkedin_company_url: str,
    max_results: int = 15,
) -> List[Dict[str, str]]:
    return asyncio.run(ascrape_linkedin_company_page(linkedin_company_url, max_results))


def _role_tier(role_text: str) -> int:
    """Rank a contact's role for display ordering (1=commercial-facing, 2=other manager, 3=IC).
    Never rejects — every contact gets a tier, just affects sort order."""
    r = role_text.lower().strip()
    if not r or r == "professional":
        return 2

    tier1_kw = (
        "director", "head of", "responsabile", "country manager",
        "managing director", "ceo", "founder", "co-founder", "owner",
        "amministratore", "direttore", "president", "vp",
        "sales", "business development", "marketing", "partnerships",
        "account manager", "account executive", "growth",
        "commercial", "chief revenue officer", "cro",
    )
    tier3_kw = (
        "developer", "engineer", "designer", "analyst", "consultant",
        "specialist", "intern", "stage", "tirocinante", "junior",
        "senior developer", "senior engineer", "devops",
        "architect", "qa", "tester", "technical",
    )

    if any(kw in r for kw in tier1_kw):
        return 1
    if any(kw in r for kw in tier3_kw):
        return 3
    return 2


ROLE_CLAUSE = (
    'CEO OR founder OR co-founder OR owner OR direttore OR amministratore OR '
    '"managing director" OR "commercial director" OR "sales director" OR '
    '"marketing director" OR "business development" OR "partnerships" OR '
    '"country manager" OR "client director" OR "head of sales" OR '
    '"head of marketing" OR "head of business development" OR '
    '"sales manager" OR "marketing manager" OR "account manager" OR '
    '"growth manager" OR "responsabile commerciale" OR "responsabile vendite" OR '
    '"responsabile marketing" OR "responsabile sviluppo"'
)


async def afind_linkedin_employees(
    query_name: str,
    linkedin_company_url: str,
    website_domain: str = "",
    max_results: int = 15,
) -> List[Dict[str, str]]:
    slug = linkedin_company_url.rstrip("/").split("/")[-1]
    queries: List[str] = [
        f'"{slug}" site:linkedin.com/in',
        f'site:linkedin.com/in "{slug}"',
        f'site:linkedin.com/in "{slug}" ({ROLE_CLAUSE})',
        f'"{query_name}" site:linkedin.com/in',
        f'site:linkedin.com/in "{query_name}" ({ROLE_CLAUSE})',
        f'site:linkedin.com/in "{query_name}"',
    ]

    seen_urls: set = set()
    candidates: List[Dict[str, str]] = []
    name_tokens = [w for w in query_name.lower().split() if len(w) > 2]
    domain_stem = website_domain.lower().split(".")[0] if website_domain else ""

    for q in queries:
        try:
            results = await asearch_query(q, max_results=8)
        except Exception as e:
            logger.warning(f"LinkedIn employee query failed ({q[:60]}…): {e}")
            continue

        for r in results:
            link = r.get("link", "")
            if "linkedin.com/in/" not in link.lower():
                continue
            profile_path = urlparse(link).path.rstrip("/")
            if profile_path in seen_urls:
                continue
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            haystack = (title + " " + snippet).lower()
            name_match = any(t in haystack for t in name_tokens) if name_tokens else False
            domain_match = domain_stem and domain_stem in haystack
            if not name_match and not domain_match:
                continue
            title_clean = re.sub(r"\s*\|\s*LinkedIn", "", title, flags=re.IGNORECASE)
            parts = [p.strip() for p in title_clean.split("-")]
            name_part = parts[0] if parts else ""
            role_part = parts[1] if len(parts) > 1 else "Professional"
            if not name_part or name_part.lower().startswith("site:"):
                continue
            seen_urls.add(profile_path)
            candidates.append({
                "name": name_part,
                "role": role_part,
                "url": link,
                "snippet": snippet,
            })
            if len(candidates) >= max_results:
                return candidates
    return candidates


def find_linkedin_employees(
    query_name: str,
    linkedin_company_url: str,
    website_domain: str = "",
    max_results: int = 15,
) -> List[Dict[str, str]]:
    return asyncio.run(afind_linkedin_employees(query_name, linkedin_company_url, website_domain, max_results))


_IGNORE_DOMAINS_SET = set(IGNORE_DOMAINS) | {
    "clutch.co", "designrush.com", "themanifest.com", "upcity.com",
    "goodfirms.co", "sortlist.it", "sortlist.com", "europages.com",
    "behance.net", "dribbble.com",
}


async def _afetch_clutch_profile(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search Clutch.co for the agency and extract client/portfolio info."""
    results = await asearch_query(f'site:clutch.co "{agency_name}"', max_results=3)
    clients = []
    for r in results:
        link = r.get("link", "")
        if "clutch.co" not in link:
            continue
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                resp = await client.get(link, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup.find_all("a", href=True):
                href = tag["href"]
                parsed = urlparse(href)
                if parsed.netloc and not any(
                    parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
                ):
                    clean_domain = parsed.netloc.lower()
                    clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                    clients.append({
                        "domain": clean_domain,
                        "url": f"{parsed.scheme}://{parsed.netloc}",
                        "source": "clutch.co",
                    })
        except Exception as e:
            logger.debug(f"Clutch profile fetch failed ({link}): {e}")
    return clients


async def _afetch_behance_portfolio(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search Behance for the agency's projects and extract linked client sites."""
    results = await asearch_query(f'site:behance.net "{agency_name}"', max_results=3)
    clients = []
    for r in results:
        link = r.get("link", "")
        if "behance.net" not in link:
            continue
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        for m in re.finditer(r'https?://[^\s<>"]+', snippet):
            url = m.group(0)
            parsed = urlparse(url)
            if parsed.netloc and not any(
                parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            ):
                clean_domain = parsed.netloc.lower()
                clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                clients.append({
                    "domain": clean_domain,
                    "url": url,
                    "source": "behance.net",
                })
    return clients


async def _afetch_dribbble_portfolio(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search Dribbble for the agency's projects and extract linked client sites."""
    results = await asearch_query(f'site:dribbble.com "{agency_name}"', max_results=3)
    clients = []
    for r in results:
        link = r.get("link", "")
        if "dribbble.com" not in link:
            continue
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        for m in re.finditer(r'https?://[^\s<>"]+', snippet):
            url = m.group(0)
            parsed = urlparse(url)
            if parsed.netloc and not any(
                parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            ):
                clean_domain = parsed.netloc.lower()
                clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                clients.append({
                    "domain": clean_domain,
                    "url": url,
                    "source": "dribbble.com",
                })
    return clients


async def _afetch_linkedin_case_studies(
    linkedin_company_url: str, progress_cb=None,
) -> List[Dict[str, str]]:
    """Scrape the LinkedIn company page for case-study / featured-post external links."""
    if not linkedin_company_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(linkedin_company_url, headers=_LINKEDIN_HEADERS)
        if resp.status_code != 200:
            return []
        lower = resp.text.lower()
        if any(s in lower for s in ("captcha", "verify you are human", "sign in")):
            return []
        soup = BeautifulSoup(resp.text, "html.parser")
        clients = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            parsed = urlparse(href)
            if parsed.netloc and not any(
                parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            ):
                clean_domain = parsed.netloc.lower()
                clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                clients.append({
                    "domain": clean_domain,
                    "url": href,
                    "source": "linkedin.com",
                })
        return clients
    except Exception as e:
        logger.debug(f"LinkedIn case study fetch failed: {e}")
        return []


async def _afetch_generic_portfolio(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Broad DDG search for portfolio / case-study mentions of the agency."""
    queries = [
        f'"{agency_name}" "realizzato per" OR "case study" OR "portfolio" OR "clienti"',
        f'"{agency_name}" "progetto realizzato" OR "client" OR "progetti"',
    ]
    clients = []
    seen_domains = set()
    for q in queries:
        try:
            results = await asearch_query(q, max_results=5)
        except Exception:
            continue
        for r in results:
            link = r.get("link", "")
            parsed = urlparse(link)
            if not parsed.netloc:
                continue
            clean_domain = parsed.netloc.lower()
            clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
            if clean_domain in seen_domains:
                continue
            is_ignored = any(
                clean_domain == d or clean_domain.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            )
            if is_ignored:
                continue
            seen_domains.add(clean_domain)
            clients.append({
                "domain": clean_domain,
                "url": f"{parsed.scheme}://{parsed.netloc}",
                "source": "generic_search",
            })
    return clients


_verified_cache: Dict[str, bool] = {}


async def _averify_client_link(candidate: Dict[str, str], agency_name: str) -> bool:
    """Fetch the source page and check that the agency name actually appears on it."""
    url = candidate.get("url", "")
    if not url:
        return False
    cache_key = url.rstrip("/")
    if cache_key in _verified_cache:
        return _verified_cache[cache_key]
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200:
            _verified_cache[cache_key] = False
            return False
        html_text = resp.text
        name_lower = agency_name.lower()
        name_plain = strip_diacritics(name_lower)
        text_lower = html_text.lower()
        text_plain = strip_diacritics(text_lower)
        found = name_lower in text_lower or name_plain in text_plain
        _verified_cache[cache_key] = found
        return found
    except Exception as e:
        logger.debug(f"Client link verification failed ({url}): {e}")
        _verified_cache[cache_key] = False
        return False


async def _verify_clients(clients: List[Dict[str, str]], agency_name: str) -> List[Dict[str, str]]:
    """Verify a list of client candidates in parallel, keeping only verified ones."""
    if not clients:
        return []
    results = await asyncio.gather(
        *[_averify_client_link(c, agency_name) for c in clients],
        return_exceptions=True,
    )
    return [
        c for c, ok in zip(clients, results)
        if ok and not isinstance(ok, Exception)
    ]


async def _afetch_awwwards_portfolio(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search Awwwards for the agency and extract linked project sites."""
    results = await asearch_query(f'site:awwwards.com "{agency_name}"', max_results=3)
    clients = []
    for r in results:
        link = r.get("link", "")
        if "awwwards.com" not in link:
            continue
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        for m in re.finditer(r'https?://[^\s<>"]+', snippet):
            url = m.group(0)
            parsed = urlparse(url)
            if parsed.netloc and not any(
                parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            ):
                clean_domain = parsed.netloc.lower()
                clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                clients.append({
                    "domain": clean_domain,
                    "url": url,
                    "source": "awwwards.com",
                })
    return clients


async def _afetch_designrush_profile(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search DesignRush for the agency and extract client/portfolio links."""
    results = await asearch_query(f'site:designrush.com "{agency_name}"', max_results=3)
    clients = []
    for r in results:
        link = r.get("link", "")
        if "designrush.com" not in link:
            continue
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        for m in re.finditer(r'https?://[^\s<>"]+', snippet):
            url = m.group(0)
            parsed = urlparse(url)
            if parsed.netloc and not any(
                parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            ):
                clean_domain = parsed.netloc.lower()
                clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                clients.append({
                    "domain": clean_domain,
                    "url": url,
                    "source": "designrush.com",
                })
    return clients


async def _afetch_themanifest_profile(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search The Manifest for the agency and extract client/portfolio links."""
    results = await asearch_query(f'site:themanifest.com "{agency_name}"', max_results=3)
    clients = []
    for r in results:
        link = r.get("link", "")
        if "themanifest.com" not in link:
            continue
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        for m in re.finditer(r'https?://[^\s<>"]+', snippet):
            url = m.group(0)
            parsed = urlparse(url)
            if parsed.netloc and not any(
                parsed.netloc == d or parsed.netloc.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            ):
                clean_domain = parsed.netloc.lower()
                clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
                clients.append({
                    "domain": clean_domain,
                    "url": url,
                    "source": "themanifest.com",
                })
    return clients


async def _afetch_agenzie_digitali(agency_name: str, progress_cb=None) -> List[Dict[str, str]]:
    """Search Italian digital agency directories for portfolio mentions."""
    queries = [
        f'"{agency_name}" agenzia digitale portfolio',
        f'"{agency_name}" "realizzato da" OR "progetto" OR "portfolio"',
    ]
    clients = []
    seen_domains: set = set()
    for q in queries:
        try:
            results = await asearch_query(q, max_results=5)
        except Exception:
            continue
        for r in results:
            link = r.get("link", "")
            parsed = urlparse(link)
            if not parsed.netloc:
                continue
            clean_domain = parsed.netloc.lower()
            clean_domain = clean_domain[4:] if clean_domain.startswith("www.") else clean_domain
            if clean_domain in seen_domains:
                continue
            is_ignored = any(
                clean_domain == d or clean_domain.endswith("." + d) for d in _IGNORE_DOMAINS_SET
            )
            if is_ignored:
                continue
            seen_domains.add(clean_domain)
            clients.append({
                "domain": clean_domain,
                "url": f"{parsed.scheme}://{parsed.netloc}",
                "source": "generic_search",
            })
    return clients


async def aexternal_portfolio_lookup(
    agency_name: str,
    linkedin_company_url: str = "",
    progress_cb=None,
) -> List[Dict[str, str]]:
    """
    Fetch agency portfolio/client lists from external sources in parallel.
    Returns a list of dicts with keys: domain, url, source.
    """
    if not agency_name:
        return []

    tasks = [
        _afetch_clutch_profile(agency_name, progress_cb),
        _afetch_behance_portfolio(agency_name, progress_cb),
        _afetch_dribbble_portfolio(agency_name, progress_cb),
        _afetch_linkedin_case_studies(linkedin_company_url, progress_cb),
        _afetch_generic_portfolio(agency_name, progress_cb),
        _afetch_awwwards_portfolio(agency_name, progress_cb),
        _afetch_designrush_profile(agency_name, progress_cb),
        _afetch_themanifest_profile(agency_name, progress_cb),
        _afetch_agenzie_digitali(agency_name, progress_cb),
    ]

    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    seen = set()
    merged = []
    for res in results_list:
        if isinstance(res, Exception):
            continue
        for item in res:
            domain = item["domain"]
            if domain not in seen:
                seen.add(domain)
                merged.append(item)

    verified = await _verify_clients(merged, agency_name)

    SOURCE_PRIORITY = {
        "clutch.co": 5, "awwwards.com": 5, "behance.net": 4,
        "dribbble.com": 4, "designrush.com": 4, "themanifest.com": 3,
        "linkedin.com": 3, "generic_search": 1,
    }
    verified.sort(key=lambda c: (SOURCE_PRIORITY.get(c.get("source"), 0), c.get("domain", "")), reverse=True)
    return verified


async def alookup_agency(name: Optional[str] = None, vat: Optional[str] = None, progress_cb=None) -> Dict[str, Any]:
    if not name and not vat:
        return {"error": "Provide at least a company name or a VAT number."}

    result = {
        "search_name": name,
        "search_vat": vat,
        "official_name": "",
        "official_address": "",
        "vat_number": vat or "",
        "website": "",
        "website_suspect": False,
        "vies_valid": False,
        "vies_error": None,
        "emails": [],
        "telephones": [],
        "extracted_address": "",
        "size_estimate": "Unknown",
        "services": [],
        "payment_integration": {
            "provides_payment_integration": False,
            "payment_providers": [],
            "associated_services": []
        },
        "portfolio_sites": [],
        "portfolio_detail": [],
        "linkedin_contacts": [],
        "linkedin_company_url": "",
        "latest_news": [],
    }

    if result["vat_number"]:
        if progress_cb:
            progress_cb(f"Validating provided P.IVA {result['vat_number']} via VIES...")
        logger.info(f"Validating VAT via VIES: {result['vat_number']}")
        vies_data = await acheck_vat(result["vat_number"])
        result["vies_valid"] = vies_data.get("valid", False)
        if vies_data.get("valid"):
            result["official_name"] = vies_data.get("company_name")
            result["official_address"] = vies_data.get("address")
        else:
            result["vies_error"] = vies_data.get("error")

    query_name = result["official_name"] or name
    all_results = []

    if query_name:
        ai_query_task = None
        if Config.AI_ENABLED:
            try:
                from .ai_config import configured_providers as _cp
                from .ai_pipeline import aoptimize_search_query as _opt
                if _cp():
                    ai_provider = Config.AI_PROVIDER or None
                    ai_model = Config.AI_MODEL or None
                    ai_query_task = asyncio.create_task(
                        _opt(query_name, provider=ai_provider, model=ai_model)
                    )
            except ImportError:
                logger.debug("AI layer not available for query optimization")

        if progress_cb:
            progress_cb(f"Searching for official website of '{query_name}'...")
        logger.info(f"Website search: {query_name}")
        query_a = f'{query_name} web agency contatti'
        all_results.extend(await asearch_query(query_a, max_results=8))

        if not result["vat_number"]:
            if progress_cb:
                progress_cb(f"Searching corporate registries for P.IVA of '{query_name}'...")
            logger.info(f"VAT search: {query_name}")
            query_b = f'{query_name} partita iva'
            all_results.extend(await asearch_query(query_b, max_results=5))

        if ai_query_task is not None:
            try:
                done, _ = await asyncio.wait([ai_query_task], timeout=5)
                if done:
                    ai_queries = ai_query_task.result()
                    for q in ai_queries:
                        if q and q not in (query_a, query_b):
                            all_results.extend(await asearch_query(q, max_results=5))
            except Exception:
                pass

    seen_links = set()
    unique_results = []
    for r in all_results:
        link = r.get("link", "")
        if link and link not in seen_links:
            seen_links.add(link)
            unique_results.append(r)

    relevant = _filter_relevant(unique_results, query_name) if query_name else unique_results
    logger.info(f"Results: {len(unique_results)} total, {len(relevant)} relevant to '{query_name}'")

    if not result["vat_number"] and relevant:
        vat_regex = r"\b\d{11}\b"
        for r in relevant:
            snippet = r.get("snippet", "") + " " + r.get("title", "")
            matches = re.findall(vat_regex, snippet)
            if matches:
                discovered_vat = matches[0]
                result["vat_number"] = discovered_vat
                if progress_cb:
                    progress_cb(f"Discovered P.IVA {discovered_vat} from search. Validating with VIES...")
                logger.info(f"Found VAT in search: {discovered_vat}")
                vies_data = await acheck_vat(discovered_vat)
                result["vies_valid"] = vies_data.get("valid", False)
                if vies_data.get("valid"):
                    result["official_name"] = vies_data.get("company_name")
                    result["official_address"] = vies_data.get("address")
                    break
                else:
                    result["vies_error"] = vies_data.get("error")
                    result["vat_number"] = ""

    candidates = _collect_candidates(relevant)
    scored_candidates = []
    if candidates and query_name:
        if progress_cb:
            progress_cb(f"Evaluating {len(candidates)} candidate websites...")
        scored_candidates = list(await asyncio.gather(*[_ascore_website(url, query_name) for url in candidates]))

        if result["vat_number"] and result["vies_valid"]:
            vat = result["vat_number"]
            name_lower_vat = (query_name or "").lower().strip()
            if progress_cb:
                progress_cb(f"Checking which candidate website displays P.IVA {vat}...")
            vat_bonuses = await _avat_bonus([sc["url"] for sc in scored_candidates], vat, name_lower_vat)
            for sc in scored_candidates:
                sc["score"] += vat_bonuses.get(sc["url"], 0)
            logger.info(f"VAT {vat} bonus applied: { {k: v for k, v in vat_bonuses.items()} }")

        scored_candidates.sort(key=lambda x: (x["score"], -len(x["url"])), reverse=True)
        candidate_log = [f'{s["url"]} (score={s["score"]})' for s in scored_candidates]
        logger.info(f"Website candidates: {candidate_log}")
        for sc in scored_candidates:
            if sc["score"] >= 10:
                result["website"] = sc["final_url"] or sc["url"]
                break

    if not result["website"]:
        name_lower_fb = (query_name or "").lower()
        name_words_fb = [w for w in name_lower_fb.split() if len(w) > 2]
        for r in relevant:
            link = r.get("link", "")
            parsed = urlparse(link)
            domain = parsed.netloc.lower()
            clean_domain = domain[4:] if domain.startswith("www.") else domain
            is_ignored = any(clean_domain == d or clean_domain.endswith("." + d) for d in IGNORE_DOMAINS)
            if is_ignored or parsed.scheme not in ("http", "https"):
                continue
            title_lower = r.get("title", "").lower()
            name_in_domain = name_lower_fb.replace(" ", "") in clean_domain
            name_in_title = name_lower_fb in title_lower if name_lower_fb else False
            name_words_in_title = sum(1 for w in name_words_fb if w in title_lower) >= max(2, len(name_words_fb) - 1) if name_words_fb else False
            if not name_in_domain and not name_in_title and not name_words_in_title:
                logger.debug(f"Fallback website skipped (name not in domain/title): {clean_domain}")
                continue
            result["website"] = f"{parsed.scheme}://{parsed.netloc}"
            logger.info(f"Fallback website (name-verified): {result['website']}")
            break
        else:
            if query_name:
                logger.warning(f"No verified website found for '{query_name}'")

    website_domain = urlparse(result["website"]).netloc.replace("www.", "") if result["website"] else ""
    size_regexes = [
        r"(\d+-\d+ dipendenti|\d+-\d+ employees|\d+ dipendenti|\d+ employees)",
        r"dimensione dell.azienda:\s*([^\n,|.]+)",
        r"company size:\s*([^\n,|.]+)"
    ]

    linkedin_company_results = []
    if website_domain:
        if progress_cb:
            progress_cb(f"Searching for LinkedIn company page of {website_domain}...")
        li_query = f'site:linkedin.com/company "{website_domain}"'
        linkedin_company_results = await asearch_query(li_query, max_results=5)
        for r in linkedin_company_results:
            link = r.get("link", "")
            if "linkedin.com/company/" in link.lower():
                if _result_is_relevant(r, query_name) or _result_is_relevant(r, website_domain):
                    result["linkedin_company_url"] = link
                    snippet = r.get("snippet", "").lower()
                    for regex in size_regexes:
                        match = re.search(regex, snippet)
                        if match:
                            result["size_estimate"] = match.group(1).strip().capitalize()
                            break
                    break

    if (not result["linkedin_company_url"] or result["size_estimate"] == "Unknown") and query_name:
        if progress_cb:
            progress_cb(f"Searching LinkedIn by name for '{query_name}'...")
        li_query = f'site:linkedin.com/company "{query_name}"'
        name_linkedin_results = await asearch_query(li_query, max_results=5)
        for r in name_linkedin_results:
            link = r.get("link", "")
            if "linkedin.com/company/" in link.lower():
                if _result_is_relevant(r, query_name):
                    if not result["linkedin_company_url"]:
                        result["linkedin_company_url"] = link
                    if result["size_estimate"] == "Unknown":
                        snippet = r.get("snippet", "").lower()
                        for regex in size_regexes:
                            match = re.search(regex, snippet)
                            if match:
                                result["size_estimate"] = match.group(1).strip().capitalize()
                                break
                    break

    if result["linkedin_company_url"] and query_name:
        if progress_cb:
            progress_cb(f"Anchoring contact search to LinkedIn company page: {result['linkedin_company_url']}")
        logger.info(f"LinkedIn employee search (company-page-anchored): {result['linkedin_company_url']}")

        page_contacts = await ascrape_linkedin_company_page(result["linkedin_company_url"])
        for c in page_contacts:
            c["source"] = "company_page"

        search_contacts = await afind_linkedin_employees(
            query_name, result["linkedin_company_url"], website_domain, max_results=20
        )
        for c in search_contacts:
            c["source"] = "company_page"

        all_contacts = page_contacts + search_contacts
        seen_urls: set = set()
        deduped: List[Dict[str, Any]] = []
        for c in all_contacts:
            key = c["url"].rstrip("/")
            if key not in seen_urls:
                seen_urls.add(key)
                deduped.append(c)

        deduped.sort(key=lambda c: (
            _role_tier(c.get("role", "")),
            0 if c.get("source") == "company_page" else 1,
            c.get("name", "").lower(),
        ))
        result["linkedin_contacts"] = deduped[:20]

    if result["linkedin_company_url"]:
        broad_contacts: List[Dict[str, Any]] = []
        for r in relevant:
            link = r.get("link", "")
            if "linkedin.com/in/" in link.lower():
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                title_clean = re.sub(r"\s*\|\s*LinkedIn", "", title, flags=re.IGNORECASE)
                parts = [p.strip() for p in title_clean.split("-")]
                if len(parts) >= 1:
                    name_part = parts[0]
                    role_part = parts[1] if len(parts) > 1 else "Professional"
                    if name_part and not name_part.lower().startswith("site:"):
                        broad_contacts.append({
                            "name": name_part,
                            "role": role_part,
                            "url": link,
                            "snippet": snippet,
                            "source": "broad",
                        })
        seen_urls = {c["url"].rstrip("/") for c in result["linkedin_contacts"]}
        for c in broad_contacts:
            if c["url"].rstrip("/") not in seen_urls:
                result["linkedin_contacts"].append(c)
                seen_urls.add(c["url"].rstrip("/"))

    if result["linkedin_company_url"] and result["website"] and not result.get("website_suspect"):
        for r in linkedin_company_results:
            if r.get("link") == result["linkedin_company_url"]:
                haystack = (r.get("title", "") + " " + r.get("snippet", "")).lower()
                if website_domain not in haystack:
                    first_name_word = query_name.lower().split()[0] if query_name else ""
                    if not first_name_word or first_name_word not in haystack:
                        logger.warning(f"LinkedIn page may not match website: {result['linkedin_company_url']}")
                        result["website_suspect"] = True
                break

    urls_to_try = []
    for sc in scored_candidates:
        if sc["score"] >= -30:
            urls_to_try.append(sc["url"])
    if not urls_to_try and result["website"]:
        urls_to_try.append(result["website"])

    external_portfolio_task = None
    external_news_task = None
    if query_name:
        if progress_cb:
            progress_cb("Searching external sources for portfolio/client sites (Clutch, Behance, Dribbble, LinkedIn, Awwwards, DesignRush, The Manifest)...")
        external_portfolio_task = asyncio.ensure_future(
            aexternal_portfolio_lookup(query_name, result.get("linkedin_company_url", ""), progress_cb)
        )
        external_news_task = asyncio.ensure_future(
            aexternal_news_lookup(query_name, website_domain, result.get("linkedin_company_url", ""), progress_cb)
        )

    scraped_ok = False
    for url in urls_to_try:
        if progress_cb:
            progress_cb(f"Crawling {url} for contact details, services, and payment systems...")
        if await _aextract(url, query_name, result, progress_cb=progress_cb):
            if result.get("website_suspect"):
                logger.info(f"Website {url} is suspect (name not found on site), rejecting...")
                result["emails"] = []
                result["telephones"] = []
                result["services"] = []
                result["portfolio_sites"] = []
                result["extracted_address"] = ""
                result["payment_integration"] = {
                    "provides_payment_integration": False,
                    "payment_providers": [],
                    "associated_services": []
                }
                result["website_suspect"] = False
                continue
            scraped_ok = True
            if _has_meaningful_data(result):
                break
            logger.info(f"Website {url} yielded limited data, trying next...")
        result["emails"] = []
        result["telephones"] = []
        result["services"] = []
        result["portfolio_sites"] = []
        result["extracted_address"] = ""
        result["payment_integration"] = {
            "provides_payment_integration": False,
            "payment_providers": [],
            "associated_services": []
        }
        result["website_suspect"] = False

    if external_portfolio_task is not None:
        try:
            external_clients = await external_portfolio_task
            if external_clients:
                external_domains = [c["domain"] for c in external_clients]
                existing = set(result["portfolio_sites"])
                for d in external_domains:
                    if d not in existing:
                        result["portfolio_sites"].append(d)
                        existing.add(d)
                result["portfolio_detail"] = external_clients
                logger.info(f"Verified portfolio lookup found {len(external_clients)} client domains")
        except Exception as e:
            logger.warning(f"External portfolio lookup failed: {e}")

    if external_news_task is not None:
        try:
            result["latest_news"] = await external_news_task
            if result["latest_news"]:
                logger.info(f"Found {len(result['latest_news'])} news items for '{query_name}'")
        except Exception as e:
            logger.warning(f"External news lookup failed: {e}")

    if not scraped_ok and result["website"]:
        result["website"] = ""

    if not result["emails"] and result["website"]:
        if progress_cb:
            progress_cb("No emails found — trying /contatti and /contact pages...")
        contact_paths = [
            "/contatti", "/contattaci", "/contact", "/contact-us",
            "/it/contatti", "/chi-siamo/contatti",
        ]
        website_base = result["website"].rstrip("/")
        semaphore = asyncio.Semaphore(3)

        async def _fetch_contact_page(path: str) -> Optional[str]:
            url = f"{website_base}{path}"
            try:
                async with semaphore:
                    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
                        resp = await client.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.9"})
                    if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
                        return resp.text
            except Exception:
                pass
            return None

        contact_tasks = [_fetch_contact_page(p) for p in contact_paths]
        contact_results = await asyncio.gather(*contact_tasks)
        for html in contact_results:
            if not html:
                continue
            from .extractor import InformationExtractor
            temp_extractor = InformationExtractor([{"html": html, "text": "", "url": "", "type": "contact", "external_links": []}])
            emails = temp_extractor.extract_emails()
            if emails:
                result["emails"] = emails
                logger.info(f"Found {len(emails)} emails from contact page")
                break

    if not result["linkedin_contacts"] and query_name and result["linkedin_company_url"]:
        if progress_cb:
            progress_cb(f"Performing fallback LinkedIn contact search for '{query_name}'...")
        logger.info(f"Targeted LinkedIn search (fallback): {query_name}")
        people_query = f'site:linkedin.com/in "{query_name}" ({ROLE_CLAUSE})'
        people_results = await asearch_query(people_query, max_results=10)
        relevant_people = _filter_relevant(people_results, query_name)

        seen_urls = set()
        for r in relevant_people:
            link = r.get("link", "")
            if "linkedin.com/in/" in link.lower():
                if link.rstrip("/") in seen_urls:
                    continue
                seen_urls.add(link.rstrip("/"))
                title = r.get("title", "")
                snippet = r.get("snippet", "")
                title_clean = re.sub(r"\s*\|\s*LinkedIn", "", title, flags=re.IGNORECASE)
                parts = [p.strip() for p in title_clean.split("-")]
                if len(parts) >= 1:
                    name_part = parts[0]
                    role_part = parts[1] if len(parts) > 1 else "Professional"
                    if name_part and not name_part.lower().startswith("site:"):
                        result["linkedin_contacts"].append({
                            "name": name_part,
                            "role": role_part,
                            "url": link,
                            "snippet": snippet,
                            "source": "fallback",
                        })

    if Config.AI_ENABLED:
        try:
            from .ai_config import configured_providers as _cp
            from .ai_pipeline import aprocess_full as _full
            if _cp():
                if progress_cb:
                    progress_cb("Running AI enhancement...")
                logger.info("Starting AI post-extraction enhancement")
                ai_provider = Config.AI_PROVIDER or None
                ai_model = Config.AI_MODEL or None
                result = await _full(result, provider=ai_provider, model=ai_model, sender_company=Config.SENDER_COMPANY, timeout=45)
        except ImportError:
            logger.debug("AI layer not available for post-extraction enhancement")
        except Exception as e:
            logger.warning(f"AI enhancement failed: {e}")

    return result


def lookup_agency(name: Optional[str] = None, vat: Optional[str] = None, progress_cb=None) -> Dict[str, Any]:
    return asyncio.run(alookup_agency(name=name, vat=vat, progress_cb=progress_cb))
