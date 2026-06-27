import xml.etree.ElementTree as ET
import requests
import re
from .config import Config

def check_vat(vat_number: str) -> dict:
    """
    Validates an Italian VAT (Partita IVA) via the EU VIES service and retrieves
    official registered company name and address.
    """
    # Clean the input, keeping only digits
    cleaned_vat = re.sub(r"\D", "", vat_number)
    
    # Italian VAT is exactly 11 digits
    if len(cleaned_vat) != 11:
        return {
            "valid": False,
            "vat": cleaned_vat,
            "error": "Invalid format. Italian VAT number must contain exactly 11 digits."
        }

    soap_url = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"
    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": ""
    }
    
    # SOAP Envelope for checking VAT
    payload = f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
   <soapenv:Header/>
   <soapenv:Body>
      <urn:checkVat>
         <urn:countryCode>IT</urn:countryCode>
         <urn:vatNumber>{cleaned_vat}</urn:vatNumber>
      </urn:checkVat>
   </soapenv:Body>
</soapenv:Envelope>"""

    try:
        response = requests.post(soap_url, data=payload, headers=headers, timeout=Config.TIMEOUT)
        if response.status_code != 200:
            return {
                "valid": False,
                "vat": cleaned_vat,
                "error": f"VIES service unavailable (HTTP {response.status_code})."
            }
        
        # Parse XML response
        root = ET.fromstring(response.text)
        
        # Helper to find elements ignoring namespace namespaces
        def find_tag(name):
            for elem in root.iter():
                if elem.tag.endswith(name):
                    return elem.text
            return None

        # Check if there is a SOAP fault
        fault = find_tag("faultstring")
        if fault:
            return {
                "valid": False,
                "vat": cleaned_vat,
                "error": f"VIES fault: {fault}"
            }

        valid_str = find_tag("valid")
        is_valid = str(valid_str).lower() == "true"

        if not is_valid:
            return {
                "valid": False,
                "vat": cleaned_vat,
                "error": "VAT number is invalid or inactive."
            }

        company_name = find_tag("name")
        address = find_tag("address")

        # Clean string formats
        if company_name:
            # Convert ---, etc to empty or strip formatting
            company_name = re.sub(r"\s+", " ", company_name).strip()
        if address:
            address = re.sub(r"\s+", " ", address).strip()

        return {
            "valid": True,
            "vat": cleaned_vat,
            "company_name": company_name or "Unknown Registry Name",
            "address": address or "Address not provided in VIES"
        }
        
    except requests.RequestException as e:
        return {
            "valid": False,
            "vat": cleaned_vat,
            "error": f"VIES connection error: {str(e)}"
        }
    except ET.ParseError:
        return {
            "valid": False,
            "vat": cleaned_vat,
            "error": "Failed to parse VIES XML response."
        }
