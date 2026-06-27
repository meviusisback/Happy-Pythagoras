import re
from urllib.parse import urlparse
from typing import List, Dict, Set, Any

class InformationExtractor:
    def __init__(self, scraped_pages: List[Dict[str, Any]]):
        self.pages = scraped_pages
        # Combine all texts for global search
        self.all_text = "\n".join([page.get("text", "") for page in self.pages])

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
        """Extracts unique public email addresses."""
        email_regex = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
        emails = re.findall(email_regex, self.all_text)
        
        # Filter out common dummy or automated email addresses
        ignore_emails = {
            "sentry.io", "w3.org", "example.com", "yourdomain.com", "email@example.com",
            "name@domain.com", "domain.com", "bootstrap.com"
        }
        
        valid_emails = set()
        for email in emails:
            email_lower = email.lower()
            domain = email_lower.split("@")[-1] if "@" in email_lower else ""
            if domain not in ignore_emails and email_lower not in ignore_emails:
                valid_emails.add(email_lower)
                
        return sorted(list(valid_emails))

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
                from bs4 import BeautifulSoup
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
                    from bs4 import BeautifulSoup
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

    def extract_payment_integrations(self) -> Dict[str, Any]:
        """
        Confirms if the agency offers payment provider integrations,
        and lists the specific providers or services mentioned.
        """
        providers_map = {
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
            "shopify payments": "Shopify Payments"
        }
        
        ecom_platforms = {
            "shopify": "Shopify",
            "woocommerce": "WooCommerce (WordPress)",
            "magento": "Adobe Commerce (Magento)",
            "prestashop": "PrestaShop",
            "custom e-commerce": "Sviluppo E-commerce Custom"
        }
        
        detected_providers = set()
        detected_platforms = set()
        
        text_lower = self.all_text.lower()
        
        # Check providers
        for key, name in providers_map.items():
            if key in text_lower:
                detected_providers.add(name)
                
        # Check eCommerce frameworks
        for key, name in ecom_platforms.items():
            if key in text_lower:
                detected_platforms.add(name)
                
        # Look for general payment terms
        payment_terms = ["gateway di pagamento", "sistemi di pagamento", "integrazione pagamenti", "pagamenti online", "pos virtuale"]
        has_general_payment_mentions = any(t in text_lower for t in payment_terms)
        
        # Confirm integration capability:
        # True if we found specific providers, eCommerce platforms, or explicit payment terms
        provides_integration = len(detected_providers) > 0 or len(detected_platforms) > 0 or has_general_payment_mentions
        
        # Build description of services
        services_affected = list(detected_platforms)
        if provides_integration and not services_affected:
            services_affected = ["E-commerce / Web Development"]
            
        return {
            "provides_payment_integration": provides_integration,
            "payment_providers": sorted(list(detected_providers)),
            "associated_services": sorted(services_affected)
        }
