import re
from urllib.parse import urlparse
from typing import List, Dict, Set, Any, Tuple
from bs4 import BeautifulSoup

_PROVIDER_PATTERN = re.compile(
    r'\b(stripe|paypal|satispay|nexi|klarna|adyen|braintree|apple pay|google pay|amazon pay|shopify payments|'
    r'scalapay|sisalpay|banca sella|gestpay|xpay|unicredit|cherrypay|cofidis|findomestic|'
    r'worldline|concardis|axepta|mybank|bancomat pay|sumup|credem|bper|pagopa|'
    r'ideal|sofort|trustly|mollie|checkout\.com|2checkout|verifone|payoneer|payu|'
    r'razorpay|square|worldpay|windcave|coinbase|bitpay|'
    r'visa|mastercard|american express|pos virtuale)\b',
    re.IGNORECASE,
)
_PLATFORM_PATTERN = re.compile(
    r'\b(shopify|woocommerce|magento|prestashop|shopware|bigcommerce|'
    r'salesforce commerce cloud|demandware|wix|squarespace|'
    r'drupal commerce|opencart|oscommerce|lightspeed|vtex|custom e-commerce)\b',
    re.IGNORECASE,
)
_PAYMENT_TERMS_PATTERN = re.compile(
    r'\b(gateway di pagamento|sistemi di pagamento|integrazione pagamenti|pagamenti online|pos virtuale)\b',
    re.IGNORECASE,
)
_LOGO_IMG_PATTERN = re.compile(
    r'<img\s+[^>]*(?:alt|src)=["\']([^"\']*)["\'][^>]*>',
    re.IGNORECASE,
)
_JS_PAYMENT_PATTERN = re.compile(
    r'(?:Stripe\.setPublishableKey|braintree\.setup|paypal\.Button\.render|Stripe\(|'
    r'Braintree\.create|Mollie\(|Checkout\.com)',
    re.IGNORECASE,
)

_PROVIDER_TO_NAME = {
    "stripe": "Stripe",
    "paypal": "PayPal",
    "satispay": "Satispay",
    "nexi": "Nexi / XPay",
    "klarna": "Klarna (Buy Now Pay Later)",
    "adyen": "Adyen",
    "braintree": "Braintree",
    "apple pay": "Apple Pay",
    "google pay": "Google Pay",
    "amazon pay": "Amazon Pay",
    "shopify payments": "Shopify Payments",
    "scalapay": "Scalapay (Buy Now Pay Later)",
    "sisalpay": "SisalPay",
    "banca sella": "Banca Sella / GestPay",
    "gestpay": "Banca Sella / GestPay",
    "xpay": "XPay",
    "unicredit": "UniCredit",
    "cherrypay": "CherryPay",
    "cofidis": "Cofidis",
    "findomestic": "Findomestic",
    "worldline": "Worldline",
    "concardis": "Concardis / Worldline",
    "axepta": "Axepta (BNL)",
    "mybank": "MyBank",
    "bancomat pay": "Bancomat Pay",
    "sumup": "SumUp",
    "credem": "Credem",
    "bper": "BPER",
    "pagopa": "PagoPA",
    "ideal": "iDEAL",
    "sofort": "Sofort (Klarna)",
    "trustly": "Trustly",
    "mollie": "Mollie",
    "checkout.com": "Checkout.com",
    "2checkout": "2Checkout / Verifone",
    "verifone": "2Checkout / Verifone",
    "payoneer": "Payoneer",
    "payu": "PayU",
    "razorpay": "Razorpay",
    "square": "Square",
    "worldpay": "Worldpay",
    "windcave": "Windcave",
    "coinbase": "Coinbase Commerce",
    "bitpay": "BitPay",
    "visa": "Visa",
    "mastercard": "Mastercard",
    "american express": "American Express",
    "pos virtuale": "POS Virtuale",
}

_PLATFORM_TO_NAME = {
    "shopify": "Shopify",
    "woocommerce": "WooCommerce (WordPress)",
    "magento": "Adobe Commerce (Magento)",
    "prestashop": "PrestaShop",
    "shopware": "Shopware",
    "bigcommerce": "BigCommerce",
    "salesforce commerce cloud": "Salesforce Commerce Cloud",
    "demandware": "Salesforce Commerce Cloud",
    "wix": "Wix",
    "squarespace": "Squarespace",
    "drupal commerce": "Drupal Commerce",
    "opencart": "OpenCart",
    "oscommerce": "osCommerce",
    "lightspeed": "Lightspeed",
    "vtex": "VTEX",
    "custom e-commerce": "Sviluppo E-commerce Custom",
}


def _decode_cfemail(hex_str: str) -> str:
    """Decode Cloudflare email obfuscation (data-cfemail)."""
    try:
        raw = bytes.fromhex(hex_str)
        if len(raw) < 2:
            return ""
        key = raw[0]
        return "".join(chr(b ^ key) for b in raw[1:])
    except Exception:
        return ""


def _deobfuscate_email(email: str) -> str:
    """De-obfuscate common email obfuscation patterns."""
    result = email
    result = re.sub(r"\s*\[\s*at\s*\]\s*", "@", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*\(\s*at\s*\)\s*", "@", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*\[\s*dot\s*\]\s*", ".", result, flags=re.IGNORECASE)
    result = re.sub(r"\s*\(\s*dot\s*\)\s*", ".", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+chiocciola\s+", "@", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+punto\s+", ".", result, flags=re.IGNORECASE)
    result = re.sub(r"\s+at\s+", "@", result, flags=re.IGNORECASE)
    return result

class InformationExtractor:
    def __init__(self, scraped_pages: List[Dict[str, Any]]):
        self.pages = scraped_pages
        self.all_text = "\n".join([page.get("text", "") for page in self.pages])
        self.all_html = "\n".join([page.get("html", "") for page in self.pages])

    def extract_vat(self) -> str:
        """
        Extracts Italian VAT numbers (Partita IVA).
        It looks for 11 consecutive digits, often preceded by P.IVA, Partita IVA, etc.
        """
        # Regex explanation: Match 11 digits, possibly preceded by IT or P.IVA/P.I. patterns
        patterns = [
            r"(?:partita\s*iva|p\.?\s*iva|p\.?\s*i\.?)\s*(?:c\.?f\.?)?\s*:?\s*(?:it)?\s*(\d{11})\b",  # Context-based
            r"\b(it)?(\d{11})\b"  # Raw 11 digits
        ]
        
        # 1. Try context-based extraction first
        for page in self.pages:
            html = page.get("html", "")
            # Convert html to lowercase for easier matching
            html_lower = html.lower()
            
            for pattern in patterns:
                matches = re.findall(pattern, html_lower)
                for match in matches:
                    vat = match[-1] if isinstance(match, tuple) else match
                    if vat.isdigit() and len(vat) == 11:
                        return vat
                        
        # 2. Try raw text fallback
        text_lower = self.all_text.lower()
        for pattern in patterns:
            matches = re.findall(pattern, text_lower)
            for match in matches:
                vat = match[-1] if isinstance(match, tuple) else match
                if vat.isdigit() and len(vat) == 11:
                    return vat
                    
        # 3. Last resort: scan all 11 digit numbers in the text
        all_11_digits = re.findall(r"\b\d{11}\b", self.all_text)
        for num in all_11_digits:
            # Quick check: Italian VAT numbers have specific check-digit math (Luhn-like algorithm)
            # but we can validate it via VIES anyway. Let's return the first one found in homepage footer
            # if we are scanning page by page.
            return num
            
        return ""

    def extract_emails(self) -> List[str]:
        """Extracts unique public email addresses from text and HTML."""
        email_regex = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

        raw_sources = [self.all_text]
        if self.all_html:
            raw_sources.append(self.all_html)

        found = set()

        for source in raw_sources:
            for m in re.finditer(email_regex, source):
                found.add(m.group(0).lower())

        for m in re.finditer(r"mailto:([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", self.all_html):
            found.add(m.group(1).lower())

        for m in re.finditer(r'data-[a-z]*mail["\s:=]+["\']?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})', self.all_html, re.IGNORECASE):
            found.add(m.group(1).lower())

        for m in re.finditer(r'data-cfemail["\s:=]+["\']([0-9a-fA-F]+)', self.all_html):
            decoded = _decode_cfemail(m.group(1))
            if decoded and "@" in decoded:
                found.add(decoded.lower())

        for m in re.finditer(r'__cf_email__[^>]*data-cfemail="([0-9a-fA-F]+)"', self.all_html):
            decoded = _decode_cfemail(m.group(1))
            if decoded and "@" in decoded:
                found.add(decoded.lower())

        for source in raw_sources:
            deobf = _deobfuscate_email(source)
            for m in re.finditer(email_regex, deobf):
                found.add(m.group(0).lower())

        ignore_patterns = {"sentry.io", "w3.org", "example.com", "yourdomain.com",
                           "domain.com", "bootstrap.com", "schema.org"}
        role_prefixes = ("noreply@", "no-reply@", "postmaster@", "root@", "abuse@", "webmaster@")
        _IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "ico", "bmp", "tiff", "avif"}

        valid = set()
        for email in found:
            domain = email.split("@")[-1] if "@" in email else ""
            if domain in ignore_patterns:
                continue
            if any(email.startswith(p) for p in role_prefixes):
                continue
            tld = domain.rsplit(".", 1)[-1] if "." in domain else ""
            if tld in _IMAGE_EXTS:
                continue
            valid.add(email)

        return sorted(list(valid))

    def extract_telephones(self) -> List[str]:
        """Extracts unique public Italian telephone numbers."""
        # Italian phone formats: landlines start with 0, mobiles with 3.
        # Can have +39 or 0039 prefix.
        phone_regex = r"(?:\+39|0039)[\s.-]?(?:0\d{1,4}|3\d{2})[\s.-]?\d{3,4}[:\s.-]?\d{3,4}\b"
        raw_matches = re.findall(phone_regex, self.all_text)
        
        # Fallback regex for numbers without country code but structured as phone numbers
        fallback_regex = r"\b(?:0\d{1,4}|3\d{2})[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b"
        fallback_matches = re.findall(fallback_regex, self.all_text)
        
        all_matches = raw_matches + fallback_matches
        
        # Get VAT to exclude it
        vat = self.extract_vat()
        unique_phones = {}  # Map of local_digits -> display_format
        
        for p in all_matches:
            # Clean formatting characters to verify length and digits
            cleaned = p.strip().replace("\n", "").replace("\r", "")
            digits = re.sub(r"\D", "", cleaned)
            
            # Skip if it is the VAT number
            if vat and digits == vat:
                continue
                
            if 8 <= len(digits) <= 13:
                # Normalize and find local digits (without +39 or 0039 prefix)
                # Note: if it has +39/0039, digits starts with 39 or 0039
                if digits.startswith("39") and len(digits) > 9:
                    local_digits = digits[2:]
                    has_prefix = True
                elif digits.startswith("0039") and len(digits) > 11:
                    local_digits = digits[4:]
                    has_prefix = True
                else:
                    local_digits = digits
                    has_prefix = False
                
                # Check if this local number is already added
                if local_digits in unique_phones:
                    # Prefer the representation that has a country prefix
                    if has_prefix:
                        unique_phones[local_digits] = cleaned
                else:
                    unique_phones[local_digits] = cleaned
                    
        return sorted(list(unique_phones.values()))


    def extract_address(self) -> str:
        """Attempts to extract the physical address from the text."""
        # Italian addresses usually contain keywords like "Via", "Viale", "Piazza", "Corso", "P.zza", "C.so"
        # followed by street name, civic number, and cap (5 digits ZIP code), City, Province abbreviation.
        address_patterns = [
            r"(?:via|viale|piazza|corso|p\.zza|c\.so|vicolo|largo|contrada)\s+[^,\n]+,\s*\d+[^,\n]*(?:,\s*\d{5})?\s+[^,\n]+(?:\s*\([A-Z]{2}\))?",
            r"(?:via|viale|piazza|corso)\s+[^,\n]+\s+\d{5}\s+[^,\n]+"
        ]
        
        for pattern in address_patterns:
            matches = re.findall(pattern, self.all_text, re.IGNORECASE)
            if matches:
                # Return the longest/most complete match
                best_match = max(matches, key=len)
                return re.sub(r"\s+", " ", best_match).strip()
                
        return ""

    def extract_client_websites(self) -> List[str]:
        """Extracts external websites likely created or worked on by the agency."""
        client_sites = set()
        
        for page in self.pages:
            # Outbound links on portfolio pages are the best indicator
            if page.get("type") == "portfolio" or page.get("url") == page.get("base_url"):
                for url in page.get("external_links", []):
                    parsed = urlparse(url)
                    if parsed.netloc:
                        domain = parsed.netloc.lower()
                        # Clean www
                        clean_domain = domain[4:] if domain.startswith("www.") else domain
                        client_sites.add(clean_domain)
                        
        # Fallback: check all external links in the scraped data if portfolio list is empty
        if not client_sites:
            for page in self.pages:
                for url in page.get("external_links", []):
                    parsed = urlparse(url)
                    if parsed.netloc:
                        domain = parsed.netloc.lower()
                        clean_domain = domain[4:] if domain.startswith("www.") else domain
                        client_sites.add(clean_domain)
                        
        return sorted(list(client_sites))

    def extract_services(self) -> List[str]:
        """Extracts the list of services offered by the agency."""
        services = set()
        
        # Common terms in Italian web agencies
        service_keywords = [
            "sviluppo web", "realizzazione siti", "e-commerce", "ecommerce",
            "creazione siti", "mobile app", "applicazioni mobile", "seo",
            "posizionamento", "social media", "digital marketing", "system integration",
            "ux/ui", "design", "cloud", "hosting", "consulenza", "software su misura",
            "crm", "erp", "web design", "sviluppo software"
        ]
        
        # 1. Scrape structured lists on services page
        for page in self.pages:
            if page.get("type") == "services":
                # Look for list items (e.g. <li>) from BeautifulSoup
                html = page.get("html", "")
                soup = BeautifulSoup(html, "html.parser")
                
                # Check for bullet items
                for li in soup.find_all("li"):
                    text = li.get_text().strip()
                    # Keep lists that are concise (usually service bullet points are < 10 words)
                    if 2 < len(text.split()) < 12:
                        # Clean newlines and spacing
                        clean_item = re.sub(r"\s+", " ", text)
                        services.add(clean_item)
                        
                # Look for subheadings (h2, h3, h4)
                for header in soup.find_all(["h2", "h3", "h4"]):
                    text = header.get_text().strip()
                    if 2 < len(text.split()) < 8:
                        clean_header = re.sub(r"\s+", " ", text)
                        # Avoid boilerplate headers
                        if not any(k in clean_header.lower() for k in ["contatt", "newsletter", "chi siamo", "blog", "portfolio"]):
                            services.add(clean_header)

        # 2. Key phrase extraction from text content using keywords
        for keyword in service_keywords:
            # Search for occurrences in page text
            pattern = re.compile(rf"\b{keyword}\b", re.IGNORECASE)
            if pattern.search(self.all_text):
                # Try to capitalize matches nicely
                services.add(keyword.title())
                
        # If still empty, scan homepage headers
        if not services:
            for page in self.pages:
                if page.get("type") == "generic" or page.get("url") == page.get("base_url"):
                    soup = BeautifulSoup(page.get("html", ""), "html.parser")
                    for header in soup.find_all(["h2", "h3"]):
                        text = header.get_text().strip()
                        if 2 < len(text.split()) < 8:
                            services.add(text)
                            
        # Sort and clean
        cleaned_services = []
        for s in services:
            # Filter out boilerplate sentences
            if len(s) < 80 and not s.startswith(("Privacy", "Cookie", "©", "Tutti i diritti")):
                cleaned_services.append(s)
                
        return sorted(list(set(cleaned_services)))

    _TRUSTED_BY_PATTERNS = re.compile(
        r'(trusted\s+by|our\s+clients|works?\s+with|collaborated\s+with|'
        r'i\s+nostri\s+clienti|realizzato\s+per|aziende\s+che\s+ci\s+scelgono|'
        r'chi\s+ha\s+scelto|i\s+brand\s+che|clienti|progetti\s+realizzati)',
        re.IGNORECASE,
    )
    _BRAND_IGNORE = {
        "logo", "icon", "image", "banner", "img", "photo", "thumb", "avatar",
        "background", "bg", "hero", "cover", "brand", "about", "contact",
        "cookie", "privacy", "menu", "nav", "footer", "home", "search",
        "close", "arrow", "button", "social", "share", "print",
    }

    def extract_client_logos(self) -> List[str]:
        """Extract brand-like domains from img alt/src near 'trusted by' text, and nearby text patterns."""
        domains: List[str] = []

        for page in self.pages:
            html = page.get("html", "")
            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            base_url = page.get("url", "")

            # 1. Find text nodes matching trusted-by patterns
            for node in soup.find_all(string=self._TRUSTED_BY_PATTERNS):
                parent = node.parent
                if parent is None:
                    continue
                # Search in the parent element and siblings for img tags and domain-like strings
                container = parent
                for _ in range(3):
                    if container.parent:
                        container = container.parent
                # Look for img alt text
                for img in container.find_all("img", alt=True):
                    alt = img["alt"].strip()
                    if not alt or len(alt) > 50 or len(alt) < 2:
                        continue
                    alt_lower = alt.lower()
                    if any(skip in alt_lower for skip in self._BRAND_IGNORE):
                        continue
                    # If alt looks like a brand name (capitalized, short), use the src domain
                    src = img.get("src", "")
                    if src:
                        parsed = urlparse(src)
                        if parsed.netloc:
                            domain = parsed.netloc.lower()
                            domain = domain[4:] if domain.startswith("www.") else domain
                            if "." in domain and domain not in self._BRAND_IGNORE:
                                domains.append(domain)
                # Also scan for domain-like strings in nearby text
                text = container.get_text(" ", strip=True)[:2000]
                for m in re.finditer(r'\b([a-z0-9][a-z0-9.-]*\.(?:it|com|io|net|org|co|me))\b', text):
                    d = m.group(1).lower()
                    d = d[4:] if d.startswith("www.") else d
                    if "." in d and d.split(".")[0] not in self._BRAND_IGNORE:
                        domains.append(d)

        # Deduplicate preserving order
        seen = set()
        unique: List[str] = []
        for d in domains:
            if d not in seen:
                seen.add(d)
                unique.append(d)
        return unique

    def extract_client_websites_v2(self) -> List[str]:
        """Combines external link extraction with logo/trusted-by detection."""
        from_links = set(self.extract_client_websites())
        from_logos = set(self.extract_client_logos())
        combined = list(from_links | from_logos)
        return sorted(combined)

    def extract_payment_integrations(self) -> Dict[str, Any]:
        """
        Confirms if the agency offers payment provider integrations,
        and lists the specific providers or services mentioned.
        """
        text_lower = self.all_text.lower()
        html_lower = self.all_html.lower()

        detected_providers = {_PROVIDER_TO_NAME[m.group(0).lower()] for m in _PROVIDER_PATTERN.finditer(text_lower)}
        detected_platforms = {_PLATFORM_TO_NAME[m.group(0).lower()] for m in _PLATFORM_PATTERN.finditer(text_lower)}

        for m in _LOGO_IMG_PATTERN.finditer(self.all_html):
            alt_src = m.group(1).lower()
            for prov_key in _PROVIDER_TO_NAME:
                if prov_key in alt_src:
                    detected_providers.add(_PROVIDER_TO_NAME[prov_key])

        if _JS_PAYMENT_PATTERN.search(html_lower):
            for prov_key in ("stripe", "braintree", "paypal", "mollie", "checkout.com"):
                if prov_key in html_lower:
                    detected_providers.add(_PROVIDER_TO_NAME[prov_key])

        has_general_payment_mentions = bool(_PAYMENT_TERMS_PATTERN.search(text_lower))

        provides_integration = len(detected_providers) > 0 or len(detected_platforms) > 0 or has_general_payment_mentions

        services_affected = list(detected_platforms)
        if provides_integration and not services_affected:
            services_affected = ["E-commerce / Web Development"]

        return {
            "provides_payment_integration": provides_integration,
            "payment_providers": sorted(list(detected_providers)),
            "associated_services": sorted(services_affected),
        }
