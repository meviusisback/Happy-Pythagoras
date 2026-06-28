import unittest
from unittest.mock import patch, MagicMock, AsyncMock
import xml.etree.ElementTree as ET
from agency_finder.vies import check_vat, acheck_vat
from agency_finder.scraper import WebScraper
from agency_finder.extractor import InformationExtractor, _decode_cfemail, _deobfuscate_email
from agency_finder.core import (
    find_linkedin_employees, scrape_linkedin_company_page,
    _role_tier, ROLE_CLAUSE, IGNORE_DOMAINS,
    _afetch_awwwards_portfolio, _afetch_designrush_profile,
    _afetch_themanifest_profile,
)
from agency_finder.utils import strip_diacritics


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

    @patch("agency_finder.core._averify_client_link", new_callable=AsyncMock)
    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    @patch("agency_finder.core.httpx.AsyncClient")
    def test_clutch_profile_extracts_client_links(self, MockClient, mock_search, mock_verify):
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
        # Mock verification to always pass
        mock_verify.return_value = True

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


class TestNewsModule(unittest.TestCase):

    def test_is_relevant_news_match_name(self):
        from agency_finder.news import _is_relevant_news
        item = {"title": "Cantiere Creativo Raises €2M in Funding", "url": "https://techcrunch.it/article", "snippet": "The Italian web agency..."}
        self.assertTrue(_is_relevant_news(item, "Cantiere Creativo", "canticreativo.it"))

    def test_is_relevant_news_match_domain_stem(self):
        from agency_finder.news import _is_relevant_news
        item = {"title": "canticreativo.it wins award", "url": "https://techcrunch.it/article", "snippet": "..."}
        self.assertTrue(_is_relevant_news(item, "Some Unrelated Name", "canticreativo.it"))

    def test_is_relevant_news_reject_irrelevant(self):
        from agency_finder.news import _is_relevant_news
        item = {"title": "Unrelated Company Acquired", "url": "https://techcrunch.it/article", "snippet": "A completely different company..."}
        self.assertFalse(_is_relevant_news(item, "Cantiere Creativo", "canticreativo.it"))

    def test_parse_date_rfc2822(self):
        from agency_finder.news import _parse_date
        self.assertEqual(_parse_date("Wed, 15 Jan 2025 10:30:00 +0100"), "2025-01-15")

    def test_parse_date_iso(self):
        from agency_finder.news import _parse_date
        self.assertEqual(_parse_date("2025-03-20T14:00:00Z"), "2025-03-20")

    def test_parse_date_empty(self):
        from agency_finder.news import _parse_date
        self.assertEqual(_parse_date(""), "")

    @patch("agency_finder.news.httpx.AsyncClient")
    def test_google_news_rss_parses_xml(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
        <channel>
          <title>Google News</title>
          <item>
            <title>Cantiere Creativo presenta nuovo progetto e-commerce</title>
            <link>https://techcrunch.it/cantiere-creativo-progetto</link>
            <pubDate>Wed, 15 Jan 2025 10:30:00 +0100</pubDate>
            <source url="https://techcrunch.it">TechCrunch Italy</source>
            <description>Cantiere Creativo ha lanciato un nuovo portale e-commerce per il settore moda.</description>
          </item>
          <item>
            <title>Milano vince campionato di calcio</title>
            <link>https://gazzetta.it/milano-calcio</link>
            <pubDate>Tue, 14 Jan 2025 09:00:00 +0100</pubDate>
            <source url="https://gazzetta.it">La Gazzetta</source>
            <description>Il Milan batte l'Inter 2-1 in derby.</description>
          </item>
        </channel>
        </rss>"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        from agency_finder.news import afetch_google_news_rss
        import asyncio
        results = asyncio.run(afetch_google_news_rss("Cantiere Creativo", "canticreativo.it"))
        self.assertEqual(len(results), 1)
        self.assertIn("Cantiere Creativo", results[0]["title"])
        self.assertEqual(results[0]["date"], "2025-01-15")
        self.assertEqual(results[0]["source"], "TechCrunch Italy")

    @patch("agency_finder.news.asearch_query", new_callable=AsyncMock)
    def test_ddg_news_filters_irrelevant(self, mock_search):
        mock_search.side_effect = [
            [{"title": "Cantiere Creativo Raises Funding", "link": "https://techcrunch.it/cantiere", "snippet": "2025-01-15 Cantiere Creativo raises €2M..."}],
            [{"title": "Unrelated Corp Acquired", "link": "https://random.it/article", "snippet": "Completely different company..."}],
        ]
        from agency_finder.news import afetch_ddg_news
        import asyncio
        results = asyncio.run(afetch_ddg_news("Cantiere Creativo", "canticreativo.it"))
        self.assertEqual(len(results), 1)
        self.assertIn("Cantiere Creativo", results[0]["title"])
        self.assertEqual(results[0]["date"], "2025-01-15")

    @patch("agency_finder.news.httpx.AsyncClient")
    def test_linkedin_posts_returns_empty_on_captcha(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><div class="captcha">Verify you are human</div></html>'
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        from agency_finder.news import afetch_linkedin_posts
        import asyncio
        results = asyncio.run(afetch_linkedin_posts("https://linkedin.com/company/acme", "Acme Corp"))
        self.assertEqual(results, [])

    def test_news_cache_hit(self):
        from agency_finder.news import NEWS_CACHE, NEWS_CACHE_TTL
        import time
        cache_k = "test_cache_key"
        NEWS_CACHE[cache_k] = (time.time(), [{"title": "cached"}])
        self.assertIn(cache_k, NEWS_CACHE)
        del NEWS_CACHE[cache_k]


class TestStripDiacritics(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(strip_diacritics("Cantiere Creativo"), "Cantiere Creativo")

    def test_accented(self):
        self.assertEqual(strip_diacritics("Milano"), "Milano")

    def test_italian_accents(self):
        self.assertEqual(strip_diacritics("Ragú"), "Ragu")
        self.assertEqual(strip_diacritics("café"), "cafe")
        self.assertEqual(strip_diacritics("foco"), "foco")


class TestRoleTier(unittest.TestCase):

    def test_ceo_is_tier1(self):
        self.assertEqual(_role_tier("CEO"), 1)

    def test_founder_is_tier1(self):
        self.assertEqual(_role_tier("Founder"), 1)

    def test_sales_director_is_tier1(self):
        self.assertEqual(_role_tier("Sales Director"), 1)

    def test_marketing_manager_is_tier1(self):
        self.assertEqual(_role_tier("Marketing Manager"), 1)

    def test_account_manager_is_tier1(self):
        self.assertEqual(_role_tier("Account Manager"), 1)

    def test_partnerships_is_tier1(self):
        self.assertEqual(_role_tier("Head of Partnerships"), 1)

    def test_business_development_is_tier1(self):
        self.assertEqual(_role_tier("Business Development Manager"), 1)

    def test_responsabile_is_tier1(self):
        self.assertEqual(_role_tier("Responsabile Commerciale"), 1)

    def test_developer_is_tier3(self):
        self.assertEqual(_role_tier("Developer"), 3)

    def test_designer_is_tier3(self):
        self.assertEqual(_role_tier("Graphic Designer"), 3)

    def test_engineer_is_tier3(self):
        self.assertEqual(_role_tier("Software Engineer"), 3)

    def test_intern_is_tier3(self):
        self.assertEqual(_role_tier("Intern"), 3)

    def test_project_manager_is_tier2(self):
        self.assertEqual(_role_tier("Project Manager"), 2)

    def test_empty_is_tier2(self):
        self.assertEqual(_role_tier(""), 2)

    def test_unknown_role_is_tier2(self):
        self.assertEqual(_role_tier("Coordinator"), 2)


class TestLinkedInNoDrop(unittest.TestCase):

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_no_contacts_dropped_based_on_role(self, mock_search):
        contacts = [
            {"name": "Mario Rossi", "role": "CEO", "url": "https://linkedin.com/in/mario-rossi", "snippet": "CEO at Cantiere Creativo"},
            {"name": "Giulia Bianchi", "role": "Junior Intern", "url": "https://linkedin.com/in/giulia-bianchi", "snippet": "Intern at Cantiere Creativo"},
            {"name": "Luca Verdi", "role": "Sales Director", "url": "https://linkedin.com/in/luca-verdi", "snippet": "Sales Director at Cantiere Creativo"},
        ]
        mock_search.return_value = [
            {"title": c["name"] + " - " + c["role"] + " | LinkedIn", "link": c["url"], "snippet": c["snippet"]}
            for c in contacts
        ]
        from agency_finder.core import afind_linkedin_employees
        import asyncio
        results = asyncio.run(afind_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
        ))
        self.assertEqual(len(results), 3)
        roles = [c["role"] for c in results]
        self.assertIn("CEO", roles)
        self.assertIn("Junior Intern", roles)
        self.assertIn("Sales Director", roles)

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_tier1_before_tier3(self, mock_search):
        contacts = [
            {"name": "Giulia Bianchi", "role": "Designer", "url": "https://linkedin.com/in/giulia-bianchi", "snippet": "Designer at Cantiere Creativo"},
            {"name": "Mario Rossi", "role": "CEO", "url": "https://linkedin.com/in/mario-rossi", "snippet": "CEO at Cantiere Creativo"},
            {"name": "Luca Verdi", "role": "Developer", "url": "https://linkedin.com/in/luca-verdi", "snippet": "Developer at Cantiere Creativo"},
        ]
        mock_search.return_value = [
            {"title": c["name"] + " - " + c["role"] + " | LinkedIn", "link": c["url"], "snippet": c["snippet"]}
            for c in contacts
        ]
        from agency_finder.core import afind_linkedin_employees
        import asyncio
        results = asyncio.run(afind_linkedin_employees(
            "Cantiere Creativo",
            "https://linkedin.com/company/cantiere-creativo",
        ))
        roles_by_tier = {}
        for c in results:
            tier = _role_tier(c["role"])
            roles_by_tier.setdefault(tier, []).append(c["role"])
        self.assertIn(1, roles_by_tier, "Should have a tier-1 (commercial) contact")
        self.assertIn(3, roles_by_tier, "Should have a tier-3 (IC) contact")
        self.assertEqual(_role_tier("CEO"), 1)
        self.assertEqual(_role_tier("Designer"), 3)


class TestWebsiteDetection(unittest.TestCase):

    def test_bing_in_ignore_domains(self):
        self.assertIn("bing.com", IGNORE_DOMAINS)

    def test_duckduckgo_in_ignore_domains(self):
        self.assertIn("duckduckgo.com", IGNORE_DOMAINS)

    def test_brave_in_ignore_domains(self):
        self.assertIn("search.brave.com", IGNORE_DOMAINS)

    def test_yahoo_in_ignore_domains(self):
        self.assertIn("yahoo.com", IGNORE_DOMAINS)

    def test_qwant_in_ignore_domains(self):
        self.assertIn("qwant.com", IGNORE_DOMAINS)


class TestPortfolioVerification(unittest.TestCase):

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_verify_client_link_positive(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><body>We worked with Cantiere Creativo on this project.</body></html>'
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        from agency_finder.core import _averify_client_link
        import asyncio
        result = asyncio.run(_averify_client_link(
            {"domain": "example.com", "url": "https://example.com", "source": "clutch.co"},
            "Cantiere Creativo",
        ))
        self.assertTrue(result)

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_verify_client_link_negative(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><body>This is a completely unrelated page about cooking.</body></html>'
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        from agency_finder.core import _averify_client_link, _verified_cache
        _verified_cache.clear()
        import asyncio
        result = asyncio.run(_averify_client_link(
            {"domain": "example.com", "url": "https://example.com", "source": "clutch.co"},
            "Cantiere Creativo",
        ))
        self.assertFalse(result)
        _verified_cache.clear()

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_verify_client_link_handles_diacritics(self, MockClient):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '<html><body>Progetto realizzato per Cantiere Creativo S.r.l.</body></html>'
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client

        from agency_finder.core import _averify_client_link, _verified_cache
        _verified_cache.clear()
        import asyncio
        result = asyncio.run(_averify_client_link(
            {"domain": "example.com", "url": "https://example.com", "source": "clutch.co"},
            "Cantiere Creativo",
        ))
        self.assertTrue(result)
        _verified_cache.clear()


class TestClientLogoDetection(unittest.TestCase):

    def test_extract_client_logos_finds_trusted_by_brands(self):
        pages = [{
            "url": "https://agency.it/clients",
            "type": "portfolio",
            "text": "Our trusted partners and clients",
            "html": """
            <section>
                <h2>Trusted by</h2>
                <div>
                    <img src="https://cdn.clienta.com/logo.png" alt="ClientA">
                    <img src="https://cdn.clientb.com/assets/logo.svg" alt="ClientB">
                </div>
            </section>
            """,
            "external_links": [],
        }]
        ext = InformationExtractor(pages)
        logos = ext.extract_client_logos()
        self.assertTrue(len(logos) > 0)
        has_clienta = any("clienta" in d for d in logos)
        has_clientb = any("clientb" in d for d in logos)
        self.assertTrue(has_clienta or has_clientb)

    def test_extract_client_websites_v2_includes_logos(self):
        pages = [{
            "url": "https://agency.it",
            "type": "portfolio",
            "text": "Our clients",
            "html": """
            <div>
                <h2>Trusted by</h2>
                <a href="https://client-x.com">Client X</a>
                <img src="https://cdn.clienty.com/logo.png" alt="ClientY">
            </div>
            """,
            "external_links": ["https://client-x.com"],
        }]
        ext = InformationExtractor(pages)
        v2 = ext.extract_client_websites_v2()
        self.assertTrue(any("client-x" in d for d in v2))
        self.assertTrue(any("clienty" in d for d in v2))


class TestNewOffsiteSources(unittest.TestCase):

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_awwwards_source(self, mock_search):
        mock_search.return_value = [
            {"title": "Agency on Awwwards", "link": "https://awwwards.com/sites/agency-portfolio", "snippet": "https://client.com project"}
        ]
        import asyncio
        results = asyncio.run(_afetch_awwwards_portfolio("Cantiere Creativo"))
        sources = [c["source"] for c in results]
        self.assertIn("awwwards.com", sources)

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_designrush_source(self, mock_search):
        mock_search.return_value = [
            {"title": "Agency on DesignRush", "link": "https://designrush.com/agency/profile/agency", "snippet": "..."}
        ]
        from agency_finder.core import _afetch_designrush_profile
        import asyncio
        results = asyncio.run(_afetch_designrush_profile("Cantiere Creativo"))
        self.assertIsInstance(results, list)

    @patch("agency_finder.core.asearch_query", new_callable=AsyncMock)
    def test_themanifest_source(self, mock_search):
        mock_search.return_value = [
            {"title": "Agency on The Manifest", "link": "https://themanifest.com/it/company/agency", "snippet": "..."}
        ]
        import asyncio
        results = asyncio.run(_afetch_themanifest_profile("Cantiere Creativo"))
        self.assertIsInstance(results, list)


class TestRoleClauseCommercial(unittest.TestCase):

    def test_contains_business_development(self):
        self.assertIn("business development", ROLE_CLAUSE.lower())

    def test_contains_account_manager(self):
        self.assertIn("account manager", ROLE_CLAUSE.lower())

    def test_contains_partnerships(self):
        self.assertIn("partnerships", ROLE_CLAUSE.lower())

    def test_contains_sales_director(self):
        self.assertIn("sales director", ROLE_CLAUSE.lower())

    def test_contains_marketing_director(self):
        self.assertIn("marketing director", ROLE_CLAUSE.lower())

    def test_contains_growth_manager(self):
        self.assertIn("growth manager", ROLE_CLAUSE.lower())

    def test_contains_responsabile_commerciale(self):
        self.assertIn("responsabile commerciale", ROLE_CLAUSE.lower())

    def test_contains_country_manager(self):
        self.assertIn("country manager", ROLE_CLAUSE.lower())


if __name__ == "__main__":
    unittest.main()
