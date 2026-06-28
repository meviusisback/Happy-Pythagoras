import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import xml.etree.ElementTree as ET
from agency_finder.vies import check_vat, acheck_vat
from agency_finder.scraper import WebScraper
from agency_finder.extractor import InformationExtractor, _decode_cfemail, _deobfuscate_email
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


class TestCfemailDecoding(unittest.TestCase):
    def test_basic_decode(self):
        # "test@example.com": key=0x74('t'), each subsequent byte XOR'd with key
        # Encodes as: 0x74 0x00 0x11 0x07 0x00 0x34 0x11 0x0c 0x15 0x19 0x04 0x18 0x11 0x5a 0x17 0x1b 0x19
        encoded = "740011070034110c15190418115a171b19"
        decoded = _decode_cfemail(encoded)
        self.assertEqual(decoded, "test@example.com")

    def test_invalid_hex_returns_empty(self):
        self.assertEqual(_decode_cfemail("zzzz"), "")

    def test_empty_returns_empty(self):
        self.assertEqual(_decode_cfemail(""), "")


class TestDeobfuscateEmail(unittest.TestCase):
    def test_bracket_at(self):
        self.assertEqual(_deobfuscate_email("name[at]domain.com"), "name@domain.com")

    def test_paren_at(self):
        self.assertEqual(_deobfuscate_email("name(at)domain.com"), "name@domain.com")

    def test_dot_bracket(self):
        self.assertEqual(_deobfuscate_email("name[at]domain[dot]com"), "name@domain.com")

    def test_italian_chiocciola(self):
        self.assertEqual(_deobfuscate_email("name chiocciola domain punto it"), "name@domain.it")

    def test_already_clean(self):
        self.assertEqual(_deobfuscate_email("info@agency.it"), "info@agency.it")


class TestExtractorEmailHtml(unittest.TestCase):
    def test_mailto_href(self):
        pages = [{
            "url": "https://agency.it/contatti",
            "type": "contact",
            "text": "Contact us",
            "html": '<a href="mailto:info@agency.it">Email us</a>',
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        emails = ext.extract_emails()
        self.assertIn("info@agency.it", emails)

    def test_data_mail_attr(self):
        pages = [{
            "url": "https://agency.it",
            "type": "generic",
            "text": "No email in text",
            "html": '<span data-mail="contact@agency.it">Contact</span>',
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        emails = ext.extract_emails()
        self.assertIn("contact@agency.it", emails)

    def test_cfemail_decode(self):
        # Encode "hi@agency.it": key='h'=0x68, each subsequent char XOR'd with 0x68
        pages = [{
            "url": "https://agency.it",
            "type": "generic",
            "text": "No email here",
            "html": '<a href="/cdn-cgi/l/email-protection" class="__cf_email__" data-cfemail="68000128090f0d060b1146011c">[email&#160;protected]</a>',
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        emails = ext.extract_emails()
        self.assertIn("hi@agency.it", emails)

    def test_obfuscated_in_text(self):
        pages = [{
            "url": "https://agency.it",
            "type": "generic",
            "text": "Scrivici a name [at] agency [dot] it per info.",
            "html": "",
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        emails = ext.extract_emails()
        self.assertIn("name@agency.it", emails)

    def test_excludes_noreply(self):
        pages = [{
            "url": "https://agency.it",
            "type": "generic",
            "text": "noreply@agency.it and info@agency.it",
            "html": "",
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        emails = ext.extract_emails()
        self.assertNotIn("noreply@agency.it", emails)
        self.assertIn("info@agency.it", emails)


class TestPaymentIntegrationsExpanded(unittest.TestCase):
    def setUp(self):
        self.pages = [{
            "url": "https://agency.it",
            "type": "services",
            "text": "Offriamo integrazioni con Scalapay, Banca Sella GestPay e Worldline.",
            "html": "",
            "external_links": [],
        }]

    def test_scalapay_detected(self):
        ext = InformationExtractor(self.pages)
        result = ext.extract_payment_integrations()
        self.assertTrue(result["provides_payment_integration"])
        self.assertIn("Scalapay (Buy Now Pay Later)", result["payment_providers"])

    def test_banca_sella_detected(self):
        ext = InformationExtractor(self.pages)
        result = ext.extract_payment_integrations()
        self.assertIn("Banca Sella / GestPay", result["payment_providers"])

    def test_worldline_detected(self):
        ext = InformationExtractor(self.pages)
        result = ext.extract_payment_integrations()
        self.assertIn("Worldline", result["payment_providers"])

    def test_logo_img_detection(self):
        pages = [{
            "url": "https://agency.it",
            "type": "services",
            "text": "Payment methods we support:",
            "html": '<img src="/images/payments/stripe-logo.png" alt="Stripe"><img src="/images/payments/pal.png" alt="PayPal">',
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        result = ext.extract_payment_integrations()
        self.assertTrue(result["provides_payment_integration"])
        self.assertIn("Stripe", result["payment_providers"])
        self.assertIn("PayPal", result["payment_providers"])

    def test_new_platforms(self):
        pages = [{
            "url": "https://agency.it",
            "type": "services",
            "text": "Sviluppiamo su Shopware e VTEX.",
            "html": "",
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        result = ext.extract_payment_integrations()
        self.assertIn("Shopware", result["associated_services"])
        self.assertIn("VTEX", result["associated_services"])


class TestScraperPortfolioKeywords(unittest.TestCase):
    def test_case_study_recognized(self):
        scraper = WebScraper("https://example.com")
        from bs4 import BeautifulSoup
        html = '<html><body><a href="/case-studies/client-x">Case Study</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        links = scraper.get_internal_links(soup, "https://example.com")
        self.assertEqual(len(links), 1)

    def test_customers_recognized(self):
        scraper = WebScraper("https://example.com")
        from bs4 import BeautifulSoup
        html = '<html><body><a href="/customers">Our Customers</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        links = scraper.get_internal_links(soup, "https://example.com")
        self.assertEqual(len(links), 1)

    def test_realizzazioni_recognized(self):
        scraper = WebScraper("https://example.com")
        from bs4 import BeautifulSoup
        html = '<html><body><a href="/realizzazioni">Progetti</a></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        links = scraper.get_internal_links(soup, "https://example.com")
        self.assertEqual(len(links), 1)


class TestExternalPortfolioLookup(unittest.TestCase):

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    @patch("agency_finder.core.httpx.AsyncClient")
    def test_clutch_profile_extracts_client_links(self, MockClient, mock_search):
        # Mock DDG search returning a Clutch profile
        mock_search.return_value = [
            {"title": "Cantiere Creativo su Clutch", "link": "https://clutch.co/profile/cantiere-creativo", "snippet": "..."}
        ]
        # Mock the Clutch profile page HTML
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><body><a href="https://client-x.com">Client X</a><a href="https://clutch.co/other">Other</a></body></html>'
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        from agency_finder.core import aexternal_portfolio_lookup
        import asyncio
        results = asyncio.run(aexternal_portfolio_lookup("Cantiere Creativo"))
        domains = [c["domain"] for c in results]
        self.assertIn("client-x.com", domains)
        self.assertIn("clutch.co", [c["source"] for c in results])

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_generic_portfolio_search(self, mock_search):
        mock_search.return_value = [
            {"title": "Progetto realizzato per Cantiere Creativo", "link": "https://some-agency-showcase.it/portfolio", "snippet": "..."}
        ]
        from agency_finder.core import _afetch_generic_portfolio
        import asyncio
        results = asyncio.run(_afetch_generic_portfolio("Cantiere Creativo"))
        domains = [c["domain"] for c in results]
        self.assertIn("some-agency-showcase.it", domains)
        self.assertEqual(results[0]["source"], "generic_search")


if __name__ == "__main__":
    unittest.main()
