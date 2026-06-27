import re
import time
import logging
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional
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
}


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
        "linkedin_contacts": []
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

        if progress_cb:
            progress_cb(f"Searching LinkedIn for '{query_name}'...")
        logger.info(f"LinkedIn search: {query_name}")
        query_c = f'site:linkedin.com/company {query_name}'
        all_results.extend(search_query(query_c, max_results=8))

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

    for r in relevant:
        link = r.get("link", "")
        parsed = urlparse(link)
        domain = parsed.netloc.lower()
        clean_domain = domain[4:] if domain.startswith("www.") else domain
        is_ignored = any(clean_domain == d or clean_domain.endswith("." + d) for d in IGNORE_DOMAINS)
        if not is_ignored and parsed.scheme in ("http", "https"):
            result["website"] = f"{parsed.scheme}://{parsed.netloc}"
            logger.info(f"Resolved website: {result['website']}")
            break

    for r in relevant:
        link = r.get("link", "").lower()
        if "linkedin.com/company/" in link:
            snippet = r.get("snippet", "").lower()
            size_regexes = [
                r"(\d+-\d+ dipendenti|\d+-\d+ employees|\d+ dipendenti|\d+ employees)",
                r"dimensione dell.azienda:\s*([^\n,|.]+)",
                r"company size:\s*([^\n,|.]+)"
            ]
            for regex in size_regexes:
                match = re.search(regex, snippet)
                if match:
                    result["size_estimate"] = match.group(1).strip().capitalize()
                    break
            logger.info(f"Company size: {result['size_estimate']}")

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
                    result["linkedin_contacts"].append({
                        "name": name_part,
                        "role": role_part,
                        "url": link,
                        "snippet": snippet
                    })

    if result["website"]:
        try:
            if progress_cb:
                progress_cb(f"Crawling {result['website']} for contact details, services, and payment systems...")
            logger.info(f"Crawling: {result['website']}")
            scraper = WebScraper(result["website"])
            pages = scraper.crawl(progress_cb=progress_cb)

            if pages:
                if progress_cb:
                    progress_cb("Analyzing website content...")
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
                            logger.warning(f"Website {result['website']} may not be related to '{query_name}'")
                            if progress_cb:
                                progress_cb(f"⚠️ Warning: '{query_name}' not found on the resolved website. Data may be from a different company.")

                if not result["vat_number"] or (result["vat_number"] and not result["vies_valid"]):
                    web_vat = extractor.extract_vat()
                    if web_vat and web_vat != result.get("vat_number"):
                        result["vat_number"] = web_vat
                        if progress_cb:
                            progress_cb(f"Found P.IVA {web_vat} on website. Validating with VIES...")
                        logger.info(f"VAT from website: {web_vat}")
                        vies_data = check_vat(web_vat)
                        result["vies_valid"] = vies_data.get("valid", False)
                        if vies_data.get("valid"):
                            result["official_name"] = vies_data.get("company_name")
                            result["official_address"] = vies_data.get("address")
                        else:
                            result["vies_error"] = vies_data.get("error")

        except Exception as e:
            logger.error(f"Failed to scrape website: {e}")

    if not result["linkedin_contacts"] and query_name:
        if progress_cb:
            progress_cb(f"Performing targeted LinkedIn contact search for '{query_name}'...")
        logger.info(f"Targeted LinkedIn search: {query_name}")
        time.sleep(0.5)
        people_query = f'site:linkedin.com/in "{query_name}" CEO Founder CTO'
        people_results = search_query(people_query, max_results=5)
        relevant_people = _filter_relevant(people_results, query_name)

        for r in relevant_people:
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
                        result["linkedin_contacts"].append({
                            "name": name_part,
                            "role": role_part,
                            "url": link,
                            "snippet": snippet
                        })

    return result
