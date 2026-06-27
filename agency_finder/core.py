import re
import time
import logging
import requests as _requests
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional, Tuple
from .search import search_query
from .vies import check_vat
from .scraper import WebScraper
from .extractor import InformationExtractor

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


def _score_website(url: str, agency_name: str) -> Dict[str, Any]:
    name_lower = agency_name.lower().strip()
    name_words = [w for w in name_lower.split() if len(w) > 2]

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,*/*;q=0.9",
    }

    try:
        resp = _requests.get(url, headers=headers, timeout=8, allow_redirects=True)
        final_url = resp.url
        final_domain = urlparse(final_url).netloc.lower()
        clean_domain = final_domain[4:] if final_domain.startswith("www.") else final_domain

        if "text/html" not in resp.headers.get("Content-Type", ""):
            return {"score": -50, "url": url, "final_url": final_url, "reason": "non-html"}

        html = resp.text
        if len(html) < 500:
            return {"score": -30, "url": url, "final_url": final_url, "reason": "too short"}

        if _is_parking_page(html):
            return {"score": -100, "url": url, "final_url": final_url, "reason": "parking page"}

        title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        text_body = re.sub(r'<[^>]+>', ' ', html)
        text_body = re.sub(r'\s+', ' ', text_body).strip()[:5000]

        score = 0

        domain_has_name = any(w in clean_domain for w in name_words)
        all_words_in_domain = all(w in clean_domain for w in name_words) if name_words else False
        name_combined = name_lower.replace(" ", "")

        if name_combined in clean_domain:
            score += 40
        elif all_words_in_domain:
            score += 35
        elif domain_has_name:
            if len(name_words) <= 1:
                score += 30
            elif sum(1 for w in name_words if w in clean_domain) >= len(name_words) - 1:
                score += 20
            else:
                score += 5

        if clean_domain.count(".") > 1:
            score -= 10

        if name_lower in title.lower():
            score += 20
        elif name_words and any(w in title.lower() for w in name_words):
            score += 10

        if name_lower in text_body.lower():
            score += 15

        industry_terms = ["web agency", "ecommerce", "agenzia", "digital", "software", "sviluppo", "consulenza", "sviluppo web"]
        if any(t in text_body.lower() for t in industry_terms):
            score += 10

        link_count = len(re.findall(r'href="[^"]*"', html))
        if link_count > 10:
            score += 15
        elif link_count > 3:
            score += 5

        if len(html) > 10000:
            score += 10
        elif len(html) > 3000:
            score += 5

        platform_domains = ["infobel", "yelp", "sortlist", "ecommerceitalia", "kompass", "trovaprezzi", "semrush", "alladvertising"]
        if any(p in clean_domain for p in platform_domains):
            score -= 40

        return {"score": score, "url": url, "final_url": final_url, "reason": f"score={score}, title='{title[:30]}'"}
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


def _has_meaningful_data(result: Dict[str, Any]) -> bool:
    return bool(result.get("emails")) or bool(result.get("telephones")) or bool(result.get("services")) or bool(result.get("extracted_address"))


def _extract(website: str, query_name: str, result: Dict[str, Any], progress_cb=None) -> bool:
    """Scrape and extract data from a website. Returns True if useful data was found."""
    try:
        logger.info(f"Crawling: {website}")
        scraper = WebScraper(website)
        pages = scraper.crawl(progress_cb=progress_cb)

        if not pages:
            logger.warning(f"No pages crawled from {website}")
            return False

        extractor = InformationExtractor(pages)

        result["emails"] = extractor.extract_emails()
        result["telephones"] = extractor.extract_telephones()
        result["extracted_address"] = extractor.extract_address()
        result["services"] = extractor.extract_services()
        result["portfolio_sites"] = extractor.extract_client_websites()
        result["payment_integration"] = extractor.extract_payment_integrations()

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
                vies_data = check_vat(web_vat)
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
    if len(name_words) <= 2:
        return all(w in haystack for w in name_words)
    matches = sum(1 for w in name_words if w in haystack)
    return matches >= max(2, len(name_words) // 2)


def _filter_relevant(results: List[Dict[str, str]], agency_name: str) -> List[Dict[str, str]]:
    return [r for r in results if _result_is_relevant(r, agency_name)]


ROLE_CLAUSE = (
    'CEO OR founder OR CTO OR direttore OR owner OR '
    '"marketing manager" OR "sales manager" OR "IT manager" OR '
    'amministratore OR responsabile'
)


def find_linkedin_employees(
    query_name: str,
    linkedin_company_url: str,
    website_domain: str = "",
    max_results: int = 15,
) -> List[Dict[str, str]]:
    """Search for people connected to a LinkedIn company page.

    Runs multiple targeted queries anchored to the company page and merges
    results, deduplicating by LinkedIn profile URL.
    """
    slug = linkedin_company_url.rstrip("/").split("/")[-1]

    queries: List[str] = [
        f'site:linkedin.com/in "{query_name}" ({ROLE_CLAUSE})',
        f'site:linkedin.com/in "{query_name}"',
        f'"{slug}" site:linkedin.com/in',
        f'site:linkedin.com/in "{query_name}" employees',
    ]

    seen_urls: set = set()
    candidates: List[Dict[str, str]] = []

    name_tokens = [w for w in query_name.lower().split() if len(w) > 2]
    domain_stem = website_domain.lower().split(".")[0] if website_domain else ""

    for q in queries:
        time.sleep(0.5)
        try:
            results = search_query(q, max_results=8)
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


def lookup_agency(name: Optional[str] = None, vat: Optional[str] = None, progress_cb=None) -> Dict[str, Any]:
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
        "linkedin_contacts": [],
        "linkedin_company_url": ""
    }

    if result["vat_number"]:
        if progress_cb:
            progress_cb(f"Validating provided P.IVA {result['vat_number']} via VIES...")
        logger.info(f"Validating VAT via VIES: {result['vat_number']}")
        vies_data = check_vat(result["vat_number"])
        result["vies_valid"] = vies_data.get("valid", False)
        if vies_data.get("valid"):
            result["official_name"] = vies_data.get("company_name")
            result["official_address"] = vies_data.get("address")
        else:
            result["vies_error"] = vies_data.get("error")

    query_name = result["official_name"] or name
    all_results = []

    if query_name:
        if progress_cb:
            progress_cb(f"Searching for official website of '{query_name}'...")
        logger.info(f"Website search: {query_name}")
        query_a = f'{query_name} web agency contatti'
        all_results.extend(search_query(query_a, max_results=8))

        if not result["vat_number"]:
            if progress_cb:
                progress_cb(f"Searching corporate registries for P.IVA of '{query_name}'...")
            logger.info(f"VAT search: {query_name}")
            query_b = f'{query_name} partita iva'
            all_results.extend(search_query(query_b, max_results=5))

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
                vies_data = check_vat(discovered_vat)
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
        scored_candidates = [_score_website(url, query_name) for url in candidates]
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        candidate_log = [f'{s["url"]} (score={s["score"]})' for s in scored_candidates]
        logger.info(f"Website candidates: {candidate_log}")
        for sc in scored_candidates:
            if sc["score"] >= 0:
                result["website"] = sc["url"]
                break

    if not result["website"]:
        for r in relevant:
            link = r.get("link", "")
            parsed = urlparse(link)
            domain = parsed.netloc.lower()
            clean_domain = domain[4:] if domain.startswith("www.") else domain
            is_ignored = any(clean_domain == d or clean_domain.endswith("." + d) for d in IGNORE_DOMAINS)
            if not is_ignored and parsed.scheme in ("http", "https"):
                result["website"] = f"{parsed.scheme}://{parsed.netloc}"
                logger.info(f"Fallback website (no scored candidate): {result['website']}")
                break

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
        linkedin_company_results = search_query(li_query, max_results=5)
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
        name_linkedin_results = search_query(li_query, max_results=5)
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
            progress_cb(f"Finding LinkedIn contacts anchored to company page of '{query_name}'...")
        logger.info(f"LinkedIn employee search (company-page-anchored): {result['linkedin_company_url']}")
        anchored = find_linkedin_employees(
            query_name, result["linkedin_company_url"], website_domain, max_results=15
        )
        for c in anchored:
            c["source"] = "company_page"
        result["linkedin_contacts"] = anchored

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

    scraped_ok = False
    for url in urls_to_try:
        if progress_cb:
            progress_cb(f"Crawling {url} for contact details, services, and payment systems...")
        if _extract(url, query_name, result, progress_cb=progress_cb):
            scraped_ok = True
            if _has_meaningful_data(result) and not result.get("website_suspect"):
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

    if not scraped_ok and result["website"]:
        result["website"] = ""

    if not result["linkedin_contacts"] and query_name and result["linkedin_company_url"]:
        if progress_cb:
            progress_cb(f"Performing fallback LinkedIn contact search for '{query_name}'...")
        logger.info(f"Targeted LinkedIn search (fallback): {query_name}")
        time.sleep(0.5)
        people_query = f'site:linkedin.com/in "{query_name}" CEO Founder CTO'
        people_results = search_query(people_query, max_results=5)
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

    return result
