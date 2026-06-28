import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import xml.etree.ElementTree as ET
from agency_finder.vies import check_vat, acheck_vat
from agency_finder.scraper import WebScraper
from agency_finder.extractor import InformationExtractor
from agency_finder.core import find_linkedin_employees, scrape_linkedin_company_page


class TestViesClient(unittest.TestCase):
    @patch("agency_finder.vies.httpx.AsyncClient")
    def test_check_vat_valid(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
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
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        res = check_vat("01657380509")
        self.assertTrue(res["valid"])
        self.assertEqual(res["company_name"], "CANTIERE CREATIVO S.R.L.")
        self.assertEqual(res["address"], "VIA DE' GINORI 19 50123 FIRENZE FI")

    @patch("agency_finder.vies.httpx.AsyncClient")
    def test_check_vat_invalid(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
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
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

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
        self.assertTrue(any("E-Commerce" in s or "ecommerce" in s.lower() for s in services))


class TestLinkedInEmployees(unittest.TestCase):

    def _make_result(self, url, title, snippet):
        return {"title": title, "link": url, "snippet": snippet}

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_fanout_queries(self, mock_search):
        mock_search.return_value = []
        find_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
            "canticreativo.it",
        )
        queries = [call.args[0] for call in mock_search.call_args_list]
        self.assertEqual(len(queries), 6)
        self.assertIn("cantiere-creativo", queries[0])
        self.assertIn("cantiere-creativo", queries[1])
        self.assertIn("cantiere-creativo", queries[2])
        self.assertIn("Cantiere Creativo", queries[3])
        self.assertIn("Cantiere Creativo", queries[4])
        self.assertIn("Cantiere Creativo", queries[5])

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_dedup_by_url(self, mock_search):
        profile_url = "https://linkedin.com/in/giorgio-bianchi"
        r1 = self._make_result(profile_url, "Giorgio Bianchi - CEO | LinkedIn", "CEO at Cantiere Creativo")
        r2 = self._make_result(profile_url, "Giorgio Bianchi - CEO | LinkedIn", "CEO at Cantiere Creativo")
        mock_search.side_effect = [
            [r1], [r2], [], [], [], [],
        ]
        results = find_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
        )
        urls = [c["url"] for c in results]
        self.assertEqual(len(urls), 1)
        self.assertEqual(urls[0], profile_url)

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_relevance_gate(self, mock_search):
        irrelevant = self._make_result(
            "https://linkedin.com/in/mario-rossi",
            "Mario Rossi - Marketing | LinkedIn",
            "Working at Totally Unrelated Corp",
        )
        mock_search.return_value = [irrelevant]
        results = find_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
        )
        self.assertEqual(len(results), 0)

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_relevance_by_domain_stem(self, mock_search):
        relevant = self._make_result(
            "https://linkedin.com/in/laura-verdi",
            "Laura Verdi - CTO | LinkedIn",
            "Building things at canticreativo.it since 2020",
        )
        mock_search.return_value = [relevant]
        results = find_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
            "canticreativo.it",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Laura Verdi")
        self.assertEqual(results[0]["role"], "CTO")

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_cap_at_max(self, mock_search):
        candidates = [
            self._make_result(
                f"https://linkedin.com/in/person-{i}",
                f"Person {i} - Engineer | LinkedIn",
                f"Engineer at Cantiere Creativo",
            )
            for i in range(20)
        ]
        mock_search.return_value = candidates
        results = find_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
            max_results=15,
        )
        self.assertEqual(len(results), 15)

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_exception_per_query_continues(self, mock_search):
        good = self._make_result(
            "https://linkedin.com/in/giorgio-bianchi",
            "Giorgio Bianchi - CEO | LinkedIn",
            "CEO at Cantiere Creativo",
        )
        mock_search.side_effect = [
            Exception("network error"), [good], [], [], [], [],
        ]
        results = find_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Giorgio Bianchi")


class TestScrapeLinkedInCompanyPage(unittest.TestCase):

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_returns_empty_on_network_error(self, MockClient):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        results = scrape_linkedin_company_page("https://linkedin.com/company/acme")
        self.assertEqual(results, [])

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_returns_empty_on_non_200(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        results = scrape_linkedin_company_page("https://linkedin.com/company/acme")
        self.assertEqual(results, [])

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_returns_empty_on_captcha(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><div class="captcha">Verify you are human</div></html>'
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        results = scrape_linkedin_company_page("https://linkedin.com/company/acme")
        self.assertEqual(results, [])

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_parses_profile_links(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <html>
            <a href="/in/giorgio-bianchi">Giorgio Bianchi</a>
            <a href="/in/laura-verdi">Laura Verdi</a>
        </html>
        """
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        results = scrape_linkedin_company_page("https://linkedin.com/company/acme")
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["name"], "Giorgio Bianchi")
        self.assertIn("/in/giorgio-bianchi", results[0]["url"])

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_deduplicates_profiles(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """
        <html>
            <a href="/in/giorgio-bianchi">Giorgio Bianchi</a>
            <a href="https://www.linkedin.com/in/giorgio-bianchi">Giorgio</a>
        </html>
        """
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        results = scrape_linkedin_company_page("https://linkedin.com/company/acme")
        self.assertEqual(len(results), 1)

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_caps_at_max(self, MockClient):
        links = "".join(
            f'<a href="/in/person-{i}">Person {i}</a>' for i in range(20)
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = f"<html>{links}</html>"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        results = scrape_linkedin_company_page("https://linkedin.com/company/acme", max_results=10)
        self.assertEqual(len(results), 10)


if __name__ == "__main__":
    unittest.main()
