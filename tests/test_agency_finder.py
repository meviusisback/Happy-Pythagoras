import unittest
from unittest.mock import patch, MagicMock
import xml.etree.ElementTree as ET
from agency_finder.vies import check_vat
from agency_finder.scraper import WebScraper
from agency_finder.extractor import InformationExtractor

class TestViesClient(unittest.TestCase):
    @patch("requests.post")
    def test_check_vat_valid(self, mock_post):
        # Mock VIES valid SOAP response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <checkVatResponse xmlns="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
              <countryCode>IT</countryCode>
              <vatNumber>01657380509</vatNumber>
              <requestDate>2026-06-27+02:00</requestDate>
              <valid>true</valid>
              <name>CANTIERE CREATIVO S.R.L.</name>
              <address>VIA DE' GINORI 19\n50123 FIRENZE FI</address>
            </checkVatResponse>
          </soap:Body>
        </soap:Envelope>
        """
        mock_post.return_value = mock_response

        res = check_vat("01657380509")
        self.assertTrue(res["valid"])
        self.assertEqual(res["company_name"], "CANTIERE CREATIVO S.R.L.")
        self.assertEqual(res["address"], "VIA DE' GINORI 19 50123 FIRENZE FI")

    @patch("requests.post")
    def test_check_vat_invalid(self, mock_post):
        # Mock VIES invalid response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = """
        <soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
          <soap:Body>
            <checkVatResponse xmlns="urn:ec.europa.eu:taxud:vies:services:checkVat:types">
              <countryCode>IT</countryCode>
              <vatNumber>00000000000</vatNumber>
              <valid>false</valid>
            </checkVatResponse>
          </soap:Body>
        </soap:Envelope>
        """
        mock_post.return_value = mock_response

        res = check_vat("00000000000")
        self.assertFalse(res["valid"])
        self.assertIn("invalid", res["error"].lower())


class TestScraper(unittest.TestCase):
    def test_is_same_domain(self):
        scraper = WebScraper("https://example.com")
        self.assertTrue(scraper.is_same_domain("https://example.com/about"))
        self.assertTrue(scraper.is_same_domain("https://sub.example.com/services"))
        self.assertFalse(scraper.is_same_domain("https://google.com"))

    def test_clean_url(self):
        scraper = WebScraper("https://example.com")
        self.assertEqual(
            scraper.clean_url("https://example.com/services?param=1#section"),
            "https://example.com/services"
        )


class TestExtractor(unittest.TestCase):
    def setUp(self):
        self.mock_pages = [
            {
                "url": "https://agency.it",
                "type": "generic",
                "text": "Benvenuti in Web Agency Srl. P.IVA 01234567890. Contattaci al +39 02 1234567 o scrivi a info@agency.it. Via Roma 10, Milano.",
                "html": "<footer>P.IVA 01234567890</footer>",
                "external_links": ["https://client1.it", "https://facebook.com/agency"]
            },
            {
                "url": "https://agency.it/servizi",
                "type": "services",
                "text": "I nostri servizi: Sviluppo Web e realizzazione siti E-commerce. Utilizziamo Stripe e PayPal per l'integrazione pagamenti.",
                "html": "<h2>Sviluppo Web</h2><li>Realizzazione siti E-commerce</li>",
                "external_links": []
            }
        ]
        self.extractor = InformationExtractor(self.mock_pages)

    def test_extract_vat(self):
        self.assertEqual(self.extractor.extract_vat(), "01234567890")

    def test_extract_emails(self):
        self.assertEqual(self.extractor.extract_emails(), ["info@agency.it"])

    def test_extract_telephones(self):
        self.assertEqual(self.extractor.extract_telephones(), ["+39 02 1234567"])

    def test_extract_payment_integrations(self):
        pay_info = self.extractor.extract_payment_integrations()
        self.assertTrue(pay_info["provides_payment_integration"])
        self.assertIn("Stripe", pay_info["payment_providers"])
        self.assertIn("PayPal", pay_info["payment_providers"])

    def test_extract_services(self):
        services = self.extractor.extract_services()
        # Should include capitalized services and items found in h2/li
        self.assertTrue(any("E-Commerce" in s or "ecommerce" in s.lower() for s in services))


if __name__ == "__main__":
    unittest.main()
