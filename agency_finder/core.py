import re
import logging
from urllib.parse import urlparse
from typing import Dict, List, Any, Optional
from .search import search_query
from .vies import check_vat
from .scraper import WebScraper
from .extractor import InformationExtractor

logger = logging.getLogger("agency_finder.core")

def resolve_agency_website(agency_name: str) -> Optional[str]:
    """
    Search web results to locate the official corporate website of the agency.
    """
    query = f'"{agency_name}" ("web agency" OR "system integrator" OR "sito ufficiale" OR "contatti")'
    results = search_query(query, max_results=8)
    
    # Domains we should ignore when finding the official website
    ignore_domains = {
        "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
        "youtube.com", "paginegialle.it", "paginebianche.it", "ufficiocamerale.it",
        "reportaziende.it", "registroimprese.it", "tuttitalia.it", "guidamonaci.it",
        "yelp.it", "tripadvisor.it", "glassdoor.it", "comuni-italiani.it"
    }
    
    for r in results:
        link = r.get("link", "")
        parsed = urlparse(link)
        domain = parsed.netloc.lower()
        clean_domain = domain[4:] if domain.startswith("www.") else domain
        
        # Check if the domain is not in the ignore list
        is_ignored = any(clean_domain == d or clean_domain.endswith("." + d) for d in ignore_domains)
        if not is_ignored and parsed.scheme in ("http", "https"):
            # Return base domain url
            return f"{parsed.scheme}://{parsed.netloc}"
            
    return None


def find_vat_by_name(agency_name: str) -> Optional[str]:
    """
    Search corporate directories to discover the Partita IVA (VAT) of the agency.
    """
    query = f'"{agency_name}" ("partita iva" OR "p.iva" OR "ufficiocamerale" OR "reportaziende")'
    results = search_query(query, max_results=5)
    
    vat_regex = r"\b\d{11}\b"
    for r in results:
        snippet = r.get("snippet", "") + " " + r.get("title", "")
        matches = re.findall(vat_regex, snippet)
        if matches:
            # Return the first found 11 digit number (VAT)
            return matches[0]
            
    return None


def fetch_linkedin_data(agency_name: str) -> Dict[str, Any]:
    """
    Discovers key employees on LinkedIn and extracts approximate company size.
    """
    # 1. Query for Company page (size info)
    company_query = f'site:linkedin.com/company "{agency_name}"'
    company_results = search_query(company_query, max_results=3)
    
    size_estimate = "Unknown (Could not find LinkedIn Company Page)"
    size_regexes = [
        r"(\d+-\d+ dipendenti|\d+-\d+ employees|\d+ dipendenti|\d+ employees)",
        r"dimensione dell.azienda:\s*([^\n,|.]+)",
        r"company size:\s*([^\n,|.]+)"
    ]
    
    for r in company_results:
        snippet = r.get("snippet", "").lower()
        for regex in size_regexes:
            match = re.search(regex, snippet)
            if match:
                size_estimate = match.group(1).strip().capitalize()
                break
        if size_estimate != "Unknown (Could not find LinkedIn Company Page)":
            break

    # 2. Query for Points of Contact (people)
    people_query = f'site:linkedin.com/in "{agency_name}" AND ("CEO" OR "Founder" OR "CTO" OR "Owner" OR "Developer" OR "Manager" OR "Director" OR "HR")'
    people_results = search_query(people_query, max_results=10)
    
    contacts = []
    for r in people_results:
        title = r.get("title", "")
        link = r.get("link", "")
        snippet = r.get("snippet", "")
        
        # Clean title: e.g. "Mario Rossi - CEO - Agency Name | LinkedIn" -> Name: Mario Rossi, Role: CEO
        # Standard LinkedIn Title format: "Name - Role - Company | LinkedIn"
        title_clean = re.sub(r"\s*\|\s*LinkedIn", "", title, flags=re.IGNORECASE)
        parts = [p.strip() for p in title_clean.split("-")]
        
        if len(parts) >= 1:
            name = parts[0]
            role = parts[1] if len(parts) > 1 else "Professional"
            # Double check if Name / Role is not blank
            if name and not name.lower().startswith("site:"):
                contacts.append({
                    "name": name,
                    "role": role,
                    "url": link,
                    "snippet": snippet
                })
                
    return {
        "size_estimate": size_estimate,
        "contacts": contacts
    }


def lookup_agency(name: Optional[str] = None, vat: Optional[str] = None, progress_cb = None) -> Dict[str, Any]:
    """
    Main orchestrator that gathers intelligence on a single agency.
    At least one of `name` or `vat` must be provided.
    """
    if not name and not vat:
        return {"error": "Provide at least a company name or a VAT number."}

    result = {
        "search_name": name,
        "search_vat": vat,
        "official_name": "",
        "official_address": "",
        "vat_number": vat or "",
        "website": "",
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

    # 1. VAT Validation and Retrieval
    if result["vat_number"]:
        if progress_cb:
            progress_cb(f"Validating VAT number {result['vat_number']} via VIES...")
        logger.info(f"Validating VAT number via VIES: {result['vat_number']}")
        vies_data = check_vat(result["vat_number"])
        result["vies_valid"] = vies_data.get("valid", False)
        
        if vies_data.get("valid"):
            result["official_name"] = vies_data.get("company_name")
            result["official_address"] = vies_data.get("address")
        else:
            result["vies_error"] = vies_data.get("error")

    # 2. Find VAT if not provided (directory search)
    if not result["vat_number"] and name:
        if progress_cb:
            progress_cb(f"Searching corporate registries for P.IVA of '{name}'...")
        logger.info(f"Searching for VAT number for: {name}")
        discovered_vat = find_vat_by_name(name)
        if discovered_vat:
            result["vat_number"] = discovered_vat
            if progress_cb:
                progress_cb(f"Discovered P.IVA: {discovered_vat}. Checking VIES registry...")
            logger.info(f"Found VAT number: {discovered_vat}, verifying with VIES")
            vies_data = check_vat(discovered_vat)
            result["vies_valid"] = vies_data.get("valid", False)
            if vies_data.get("valid"):
                result["official_name"] = vies_data.get("company_name")
                result["official_address"] = vies_data.get("address")
            else:
                result["vies_error"] = vies_data.get("error")

    # 3. Resolve Website URL
    query_name = result["official_name"] or name
    if progress_cb:
        progress_cb(f"Searching for official website of '{query_name}'...")
    logger.info(f"Resolving website URL for: {query_name}")
    resolved_url = resolve_agency_website(query_name)
    if resolved_url:
        result["website"] = resolved_url
        logger.info(f"Resolved website: {resolved_url}")
    else:
        # Fallback: if VAT is provided, search website by VAT
        if result["vat_number"]:
            if progress_cb:
                progress_cb(f"Website not found by name. Searching by VAT {result['vat_number']}...")
            logger.info(f"Resolving website URL by VAT: {result['vat_number']}")
            resolved_url = resolve_agency_website(result["vat_number"])
            if resolved_url:
                result["website"] = resolved_url

    # 4. Scrape Website and Extract Details
    if result["website"]:
        try:
            logger.info(f"Crawling website: {result['website']}")
            scraper = WebScraper(result["website"])
            pages = scraper.crawl(progress_cb=progress_cb)
            
            if pages:
                if progress_cb:
                    progress_cb("Analyzing text content and extracting contact cards, services, and integrations...")
                extractor = InformationExtractor(pages)
                
                # Extract Contact Details
                result["emails"] = extractor.extract_emails()
                result["telephones"] = extractor.extract_telephones()
                result["extracted_address"] = extractor.extract_address()
                
                # Extract Services
                result["services"] = extractor.extract_services()
                
                # Extract Portfolio Client Sites
                result["portfolio_sites"] = extractor.extract_client_websites()
                
                # Extract Payment integrations
                result["payment_integration"] = extractor.extract_payment_integrations()
                
                # If we still didn't have a VAT, check if website has it
                if not result["vat_number"]:
                    web_vat = extractor.extract_vat()
                    if web_vat:
                        result["vat_number"] = web_vat
                        if progress_cb:
                            progress_cb(f"Found P.IVA {web_vat} in website footer. Validating with VIES...")
                        logger.info(f"Found VAT on website: {web_vat}, verifying with VIES")
                        vies_data = check_vat(web_vat)
                        result["vies_valid"] = vies_data.get("valid", False)
                        if vies_data.get("valid"):
                            result["official_name"] = vies_data.get("company_name")
                            result["official_address"] = vies_data.get("address")
                        else:
                            result["vies_error"] = vies_data.get("error")
                            
        except Exception as e:
            logger.error(f"Failed to scrape website: {str(e)}")

    # 5. LinkedIn lookup for contacts and company size
    target_linkedin_name = result["official_name"] or name
    if target_linkedin_name:
        if progress_cb:
            progress_cb(f"Searching LinkedIn pages for points of contact and employee size...")
        logger.info(f"Looking up LinkedIn contacts & size for: {target_linkedin_name}")
        linkedin_data = fetch_linkedin_data(target_linkedin_name)
        result["size_estimate"] = linkedin_data.get("size_estimate", "Unknown")
        result["linkedin_contacts"] = linkedin_data.get("contacts", [])

    return result
