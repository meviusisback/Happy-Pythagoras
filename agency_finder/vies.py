import xml.etree.ElementTree as ET
import re
import asyncio
import httpx
from .config import Config
from .utils import USER_AGENT

SOAP_URL = "https://ec.europa.eu/taxation_customs/vies/services/checkVatService"
SOAP_HEADERS = {
    "Content-Type": "text/xml; charset=utf-8",
    "SOAPAction": "",
    "User-Agent": USER_AGENT,
}


def _soap_payload(vat: str) -> str:
    return f"""<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:urn="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
   <soapenv:Header/>
   <soapenv:Body>
      <urn:checkVat>
         <urn:countryCode>IT</urn:countryCode>
         <urn:vatNumber>{vat}</urn:vatNumber>
      </urn:checkVat>
   </soapenv:Body>
</soapenv:Envelope>"""


def _parse_response(text: str, cleaned_vat: str) -> dict:
    root = ET.fromstring(text)

    def find_tag(name):
        for elem in root.iter():
            if elem.tag.endswith(name):
                return elem.text
        return None

    fault = find_tag("faultstring")
    if fault:
        return {"valid": False, "vat": cleaned_vat, "error": f"VIES fault: {fault}"}

    valid_str = find_tag("valid")
    is_valid = str(valid_str).lower() == "true"

    if not is_valid:
        return {"valid": False, "vat": cleaned_vat, "error": "VAT number is invalid or inactive."}

    company_name = find_tag("name")
    address = find_tag("address")
    if company_name:
        company_name = re.sub(r"\s+", " ", company_name).strip()
    if address:
        address = re.sub(r"\s+", " ", address).strip()

    return {
        "valid": True,
        "vat": cleaned_vat,
        "company_name": company_name or "Unknown Registry Name",
        "address": address or "Address not provided in VIES",
    }


async def acheck_vat(vat_number: str) -> dict:
    cleaned_vat = re.sub(r"\D", "", vat_number)
    if len(cleaned_vat) != 11:
        return {
            "valid": False,
            "vat": cleaned_vat,
            "error": "Invalid format. Italian VAT number must contain exactly 11 digits.",
        }

    try:
        async with httpx.AsyncClient(timeout=Config.TIMEOUT, follow_redirects=True) as client:
            response = await client.post(
                SOAP_URL, content=_soap_payload(cleaned_vat), headers=SOAP_HEADERS
            )
        if response.status_code != 200:
            return {
                "valid": False,
                "vat": cleaned_vat,
                "error": f"VIES service unavailable (HTTP {response.status_code}).",
            }
        return _parse_response(response.text, cleaned_vat)
    except httpx.RequestError as e:
        return {"valid": False, "vat": cleaned_vat, "error": f"VIES connection error: {e}"}
    except ET.ParseError:
        return {"valid": False, "vat": cleaned_vat, "error": "Failed to parse VIES XML response."}


def check_vat(vat_number: str) -> dict:
    return asyncio.run(acheck_vat(vat_number))
