import asyncio
import json
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
    _result_is_relevant, _avat_bonus, _acheck_vat_on_page,
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


class TestSearchOuterTimeout(unittest.TestCase):

    @patch("agency_finder.search._asearch_duckduckgo", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_ddg_lite", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_ddg_html", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_mojeek", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_bing_html", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_wikipedia", new_callable=AsyncMock)
    @patch("agency_finder.search._aguess_direct_domains", new_callable=AsyncMock)
    def test_asearch_completes_within_timeout(self, mock_guess, mock_wiki, mock_bing, mock_mojeek, mock_html, mock_lite, mock_ddg):
        import asyncio
        async def _slow(*a, **kw):
            await asyncio.sleep(200)
            return []
        mock_ddg.side_effect = _slow
        mock_lite.side_effect = _slow
        mock_html.side_effect = _slow
        mock_mojeek.side_effect = _slow
        mock_bing.side_effect = _slow
        mock_wiki.side_effect = _slow
        mock_guess.side_effect = _slow
        from agency_finder.search import asearch_query
        import time
        t0 = time.time()
        result = asyncio.run(asyncio.wait_for(asearch_query("test", 3), timeout=20))
        elapsed = time.time() - t0
        self.assertIsInstance(result, list)
        self.assertLess(elapsed, 20)

    @patch("agency_finder.search._asearch_bing_html", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_wikipedia", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_mojeek", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_ddg_html", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_ddg_lite", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_duckduckgo", new_callable=AsyncMock)
    @patch("agency_finder.search._aguess_direct_domains", new_callable=AsyncMock)
    def test_returns_cached_result_immediately(self, mock_guess, mock_ddg, mock_lite, mock_html, mock_mojeek, mock_wiki, mock_bing):
        import asyncio
        from agency_finder.search import _cache_set
        _cache_set("cached_query", 5, [{"title": "cached", "link": "http://x", "snippet": ""}])
        mock_ddg.side_effect = lambda *a, **kw: (_ for _ in ()).throw(Exception("should not be called"))
        mock_bing.side_effect = lambda *a, **kw: (_ for _ in ()).throw(Exception("should not be called"))
        from agency_finder.search import asearch_query
        result = asyncio.run(asearch_query("cached_query", 5))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "cached")


class TestRelevanceFilter(unittest.TestCase):

    def test_2word_name_passes_with_one_word_in_title(self):
        result = {"title": "Cantiere Creativo", "link": "https://example.com", "snippet": ""}
        self.assertTrue(_result_is_relevant(result, "Cantiere Creativo"))

    def test_2word_name_passes_with_one_word_in_snippet(self):
        result = {"title": "Web Agency", "link": "https://example.com", "snippet": "Specializzati in cantiere"}
        self.assertTrue(_result_is_relevant(result, "Cantiere Creativo"))

    def test_2word_name_passes_when_link_contains_word(self):
        result = {"title": "Something else", "link": "https://cantierecreativo.net/servizi", "snippet": "no match"}
        self.assertTrue(_result_is_relevant(result, "Cantiere Creativo"))

    def test_2word_name_rejects_no_match_at_all(self):
        result = {"title": "Pizzeria Romana", "link": "https://pizzeria.it", "snippet": "pizza napoletana"}
        self.assertFalse(_result_is_relevant(result, "Cantiere Creativo"))

    def test_empty_name_always_passes(self):
        result = {"title": "Anything", "link": "http://x", "snippet": ""}
        self.assertTrue(_result_is_relevant(result, ""))

    def test_3word_name_requires_2_matches(self):
        result = {"title": "Web Agency Milano", "link": "http://x", "snippet": "web agency milano srl"}
        self.assertTrue(_result_is_relevant(result, "Web Agency Milano"))

    def test_3word_name_rejects_only_1_match(self):
        result = {"title": "Solo Milano", "link": "http://x", "snippet": "a Milano"}
        self.assertFalse(_result_is_relevant(result, "Web Agency Milano"))


class TestVatDisambiguation(unittest.TestCase):

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_vat_bonus_positive_when_page_shows_vat(self, MockClient):
        import asyncio
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "P.IVA 01234567890 - Cantiere Creativo S.r.l."
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        bonuses = asyncio.run(_avat_bonus(
            ["https://cantierecreativo.net", "https://cantierecreativo.info"],
            "01234567890",
        ))
        self.assertEqual(bonuses["https://cantierecreativo.net"], 50)
        self.assertEqual(bonuses["https://cantierecreativo.info"], 50)

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_vat_bonus_negative_when_page_missing_vat(self, MockClient):
        import asyncio
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"Content-Type": "text/html"}
        mock_resp.text = "Welcome to our site"
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        bonuses = asyncio.run(_avat_bonus(
            ["https://other-site.com"],
            "01234567890",
        ))
        self.assertEqual(bonuses["https://other-site.com"], -10)

    def test_vat_bonus_empty_when_no_vat(self):
        import asyncio
        bonuses = asyncio.run(_avat_bonus(["https://x.com"], ""))
        self.assertEqual(bonuses, {})

    @patch("agency_finder.core.httpx.AsyncClient")
    def test_vat_bonus_handles_network_error(self, MockClient):
        import asyncio
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value = mock_client
        bonuses = asyncio.run(_avat_bonus(["https://x.com"], "01234567890"))
        self.assertEqual(bonuses["https://x.com"], -10)


class TestSearchHardening(unittest.TestCase):

    def test_browser_headers_returns_required_keys(self):
        from agency_finder.utils import _browser_headers
        headers = _browser_headers("google")
        required = ["User-Agent", "Accept", "Accept-Language", "Accept-Encoding",
                     "Sec-Ch-Ua", "Sec-Ch-Ua-Mobile", "Sec-Ch-Ua-Platform",
                     "Sec-Fetch-Dest", "Sec-Fetch-Mode", "Sec-Fetch-Site",
                     "Sec-Fetch-User", "Upgrade-Insecure-Requests", "Referer"]
        for key in required:
            self.assertIn(key, headers)

    def test_browser_headers_referer_per_backend(self):
        from agency_finder.utils import _browser_headers
        self.assertIn("google", _browser_headers("google")["Referer"])
        self.assertIn("duckduckgo", _browser_headers("ddg")["Referer"])
        self.assertIn("bing", _browser_headers("bing")["Referer"])

    def test_browser_headers_sec_ch_ua_derived(self):
        from agency_finder.utils import _browser_headers
        headers = _browser_headers("google")
        self.assertIn("v=", headers["Sec-Ch-Ua"])
        self.assertIn("Not_A Brand", headers["Sec-Ch-Ua"])

    def test_browser_headers_platform_derived(self):
        from agency_finder.utils import _browser_headers, _parse_platform
        self.assertEqual(_parse_platform("Windows NT 10.0; Win64"), '"Windows"')
        self.assertEqual(_parse_platform("Macintosh; Intel Mac OS X 10_15_7"), '"macOS"')
        self.assertEqual(_parse_platform("X11; Linux x86_64"), '"Linux"')
        self.assertEqual(_parse_platform("iPhone; CPU iPhone OS 17_6"), '"iOS"')
        self.assertEqual(_parse_platform("Android 14; Pixel 8"), '"Android"')

    def test_browser_headers_fake_ua_fallback(self):
        from agency_finder.utils import _browser_headers, _HAS_FAKE_UA
        from agency_finder.utils import _FALLBACK_USER_AGENTS
        if not _HAS_FAKE_UA:
            headers = _browser_headers("google")
            self.assertIn(headers["User-Agent"], _FALLBACK_USER_AGENTS)

    def test_browser_headers_non_empty(self):
        from agency_finder.utils import _browser_headers
        headers = _browser_headers("google")
        for key, value in headers.items():
            self.assertTrue(value, f"Header {key} is empty")

    @patch("agency_finder.search._aretry")
    def test_bing_search_uses_brdr_param(self, mock_aretry):
        import asyncio
        from agency_finder.search import _asearch_bing_html
        mock_aretry.return_value = MagicMock(status_code=200, text="<html><body></body></html>")
        asyncio.run(_asearch_bing_html("test query", 5))
        call_args = mock_aretry.call_args
        kwargs = call_args[1]
        self.assertEqual(kwargs["params"].get("brdr"), 1)
        self.assertEqual(kwargs["params"].get("setmkt"), "it-IT")

    @patch("agency_finder.search._aretry")
    def test_bing_retry_with_mobile_ua(self, mock_aretry):
        import asyncio
        from agency_finder.search import _asearch_bing_html
        mock_aretry.side_effect = [
            MagicMock(status_code=200, text="<html>captcha</html>"),
            MagicMock(status_code=200, text="<html><li class='b_algo'><h2><a href='http://x'>Title</a></h2></li></html>"),
        ]
        results = asyncio.run(_asearch_bing_html("test query", 5))
        self.assertEqual(len(results), 1)


class TestSearchRobustness(unittest.TestCase):

    @patch("agency_finder.search._asearch_duckduckgo", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_ddg_lite", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_ddg_html", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_mojeek", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_bing_html", new_callable=AsyncMock)
    @patch("agency_finder.search._asearch_wikipedia", new_callable=AsyncMock)
    @patch("agency_finder.search._aguess_direct_domains", new_callable=AsyncMock)
    def test_asearch_query_launches_all_backends(self, mock_guess, mock_wiki, mock_bing, mock_mojeek, mock_html, mock_lite, mock_ddg):
        from agency_finder.search import asearch_query
        mock_ddg.return_value = [{"title": "x", "link": "y", "snippet": ""}]
        mock_lite.return_value = []
        mock_html.return_value = []
        mock_mojeek.return_value = []
        mock_bing.return_value = []
        mock_wiki.return_value = []
        mock_guess.return_value = []
        result = asyncio.run(asearch_query("test query", 3))
        self.assertGreater(len(result), 0)
        mock_ddg.assert_called_once()
        mock_lite.assert_called_once()
        mock_html.assert_called_once()
        mock_mojeek.assert_called_once()
        mock_bing.assert_called_once()
        mock_wiki.assert_called_once()
        mock_guess.assert_called_once()

    @patch("agency_finder.search.DDGS")
    def test_ddg_library_tries_backends_sequentially(self, mock_ddgs_class):
        from agency_finder.search import _asearch_duckduckgo

        call_log = []

        class FakeDDGS:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def text(self, query, **kw):
                backend = kw.get("backend", "auto")
                call_log.append(backend)
                if backend == "auto":
                    return []
                return [{"title": "ok", "href": "http://x", "body": "y"}]

        mock_ddgs_class.side_effect = FakeDDGS
        result = asyncio.run(_asearch_duckduckgo("test", 5))
        self.assertEqual(len(result), 1)
        self.assertIn("auto", call_log)
        self.assertIn("html", call_log)

    @patch("agency_finder.search.DDGS")
    def test_ddg_library_returns_empty_after_all_backends(self, mock_ddgs_class):
        from agency_finder.search import _asearch_duckduckgo

        class FakeDDGS:
            def __init__(self, *a, **kw):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def text(self, query, **kw):
                return []

        mock_ddgs_class.side_effect = FakeDDGS
        result = asyncio.run(_asearch_duckduckgo("test", 5))
        self.assertEqual(result, [])

    @patch("agency_finder.search.DDGS")
    def test_ddg_library_falls_back_when_timeout_unsupported(self, mock_ddgs_class):
        from agency_finder.search import _asearch_duckduckgo

        class OldDDGS:
            def __init__(self, *a, **kw):
                if "timeout" in kw:
                    raise TypeError("unexpected keyword argument 'timeout'")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def text(self, query, **kw):
                return [{"title": "fallback ok", "href": "http://x", "body": "y"}]

        mock_ddgs_class.side_effect = OldDDGS
        result = asyncio.run(_asearch_duckduckgo("test", 5))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "fallback ok")

    @patch("agency_finder.search._aretry")
    def test_ddg_lite_retries_on_empty(self, mock_aretry):
        from agency_finder.search import _asearch_ddg_lite
        empty_resp = MagicMock(status_code=200, text="<html></html>")
        empty_resp.content = b"<html></html>"
        good_resp = MagicMock(status_code=200, text="<html><a class='result-link' href='http://x'>Title</a><td class='result-snippet'>snip</td></html>")
        good_resp.content = b"<html><a class='result-link' href='http://x'>Title</a><td class='result-snippet'>snip</td></html>"
        mock_aretry.side_effect = [empty_resp, good_resp]
        result = asyncio.run(_asearch_ddg_lite("test", 5))
        self.assertEqual(len(result), 1)
        self.assertEqual(mock_aretry.call_count, 2)

    @patch("agency_finder.search._aretry")
    def test_ddg_lite_does_not_retry_on_202(self, mock_aretry):
        from agency_finder.search import _asearch_ddg_lite
        challenge_resp = MagicMock(status_code=202, text="<html>challenge</html>")
        mock_aretry.return_value = challenge_resp
        result = asyncio.run(_asearch_ddg_lite("test", 5))
        self.assertEqual(result, [])
        self.assertEqual(mock_aretry.call_count, 1)

    @patch("agency_finder.search._aretry")
    def test_ddg_html_does_not_retry_on_202(self, mock_aretry):
        from agency_finder.search import _asearch_ddg_html
        challenge_resp = MagicMock(status_code=202, text="<html>challenge</html>")
        mock_aretry.return_value = challenge_resp
        result = asyncio.run(_asearch_ddg_html("test", 5))
        self.assertEqual(result, [])
        self.assertEqual(mock_aretry.call_count, 1)

    @patch("agency_finder.search._aretry")
    def test_direct_domain_guess_finds_website(self, mock_aretry):
        from agency_finder.search import _aguess_direct_domains
        mock_aretry.return_value = MagicMock(status_code=200, text="<html><title>Acme</title></html>")
        result = asyncio.run(_aguess_direct_domains("Acme"))
        self.assertEqual(len(result), 1)
        self.assertIn(".it", result[0]["link"])

    @patch("agency_finder.search._aretry")
    def test_direct_domain_guess_ignores_captcha(self, mock_aretry):
        from agency_finder.search import _aguess_direct_domains
        mock_aretry.return_value = MagicMock(status_code=200, text="<html>captcha verify</html>")
        result = asyncio.run(_aguess_direct_domains("Acme"))
        self.assertEqual(result, [])

    def test_slugify_name(self):
        from agency_finder.search import _slugify_name
        no_space, hyphen = _slugify_name("Cantiere Creativo")
        self.assertEqual(no_space, "cantierecreativo")
        self.assertEqual(hyphen, "cantiere-creativo")
        no_space2, hyphen2 = _slugify_name("Web&Co")
        self.assertEqual(no_space2, "webco")
        self.assertEqual(hyphen2, "web-co")

    def test_clean_agency_name(self):
        from agency_finder.search import _clean_agency_name
        self.assertEqual(_clean_agency_name("Cantiere Creativo web agency"), "Cantiere Creativo")
        self.assertEqual(_clean_agency_name("Hostinato partita iva"), "Hostinato")
        self.assertEqual(_clean_agency_name("Acme SRL Milano"), "Acme")
        self.assertEqual(_clean_agency_name("Web&Co"), "Web Co")

    @patch("agency_finder.search._aretry")
    def test_asearch_wikipedia_returns_results(self, mock_aretry):
        from agency_finder.search import _asearch_wikipedia
        api_response = {
            "query": {
                "search": [
                    {"title": "Cantiere Creativo", "snippet": "A <span>creative</span> agency in Milan."}
                ]
            }
        }
        mock_response = MagicMock(status_code=200, text=json.dumps(api_response))
        mock_response.json.return_value = api_response
        mock_aretry.return_value = mock_response
        result = asyncio.run(_asearch_wikipedia("Cantiere Creativo", 5))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Cantiere Creativo")
        self.assertIn("creative agency", result[0]["snippet"])
        self.assertIn("wikipedia.org", result[0]["link"])

    @patch("agency_finder.search._aretry")
    def test_asearch_wikipedia_handles_empty(self, mock_aretry):
        from agency_finder.search import _asearch_wikipedia
        api_response = {"query": {"search": []}}
        mock_response = MagicMock(status_code=200, text=json.dumps(api_response))
        mock_response.json.return_value = api_response
        mock_aretry.return_value = mock_response
        result = asyncio.run(_asearch_wikipedia("xyznotfound", 5))
        self.assertEqual(result, [])

    @patch("agency_finder.search._aretry")
    def test_asearch_mojeek_returns_results(self, mock_aretry):
        from agency_finder.search import _asearch_mojeek
        html = """
        <html><body>
        <ul class="results-standard">
          <li class="ob">
            <a class="title" href="https://example.com">Example Title</a>
            <p class="s">An example snippet.</p>
          </li>
        </ul>
        </body></html>
        """
        mock_aretry.return_value = MagicMock(status_code=200, text=html)
        result = asyncio.run(_asearch_mojeek("example", 5))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Example Title")
        self.assertEqual(result[0]["link"], "https://example.com")
        self.assertEqual(result[0]["snippet"], "An example snippet.")

    @patch("agency_finder.search._aretry")
    def test_asearch_mojeek_handles_empty(self, mock_aretry):
        from agency_finder.search import _asearch_mojeek
        mock_aretry.return_value = MagicMock(status_code=200, text="<html><body></body></html>")
        result = asyncio.run(_asearch_mojeek("xyznotfound", 5))
        self.assertEqual(result, [])

    @patch("agency_finder.search._aretry")
    def test_direct_domain_guess_uses_full_name(self, mock_aretry):
        from agency_finder.search import _aguess_direct_domains
        mock_aretry.return_value = MagicMock(status_code=404, text="not found")
        asyncio.run(_aguess_direct_domains("Cantiere Creativo web agency Milano"))
        called_urls = [call.args[0] for call in mock_aretry.call_args_list]
        self.assertTrue(any("cantierecreativo" in u for u in called_urls))
        self.assertTrue(any("cantiere-creativo" in u for u in called_urls))
        self.assertFalse(any("milano" in u.lower() for u in called_urls))


class TestPortfolioFinder(unittest.TestCase):

    def _sitemap_xml(self, urls):
        body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
        return f'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'

    def _portfolio_page(self, client_links):
        anchors = "".join(f'<a href="{u}">{u.split("//")[1].split("/")[0]}</a>' for u in client_links)
        return f"<html><body><h1>Portfolio</h1>{anchors}</body></html>"

    @patch("agency_finder.scraper.make_async_client")
    def test_find_portfolio_from_sitemap(self, mock_client_factory):
        from agency_finder.scraper import afind_portfolio_websites

        sitemap = self._sitemap_xml([
            "https://acme.it/",
            "https://acme.it/portfolio",
            "https://acme.it/contacts",
        ])
        portfolio_html = self._portfolio_page([
            "https://client1.it",
            "https://client2.com",
        ])
        client1_html = "<html><head><title>Client One Srl</title></head></html>"

        sitemap_resp = MagicMock(status_code=200, text=sitemap)
        sitemap_resp.headers = {"Content-Type": "application/xml"}
        portfolio_resp = MagicMock(status_code=200, text=portfolio_html)
        portfolio_resp.headers = {"Content-Type": "text/html"}
        client1_resp = MagicMock(status_code=200, text=client1_html)
        client1_resp.headers = {"Content-Type": "text/html"}
        client2_resp = MagicMock(status_code=404, text="not found")
        client2_resp.headers = {"Content-Type": "text/html"}

        resp_map = {
            "https://acme.it/sitemap.xml": sitemap_resp,
            "https://acme.it/portfolio": portfolio_resp,
            "https://client1.it": client1_resp,
            "https://client2.com": client2_resp,
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def fake_get(url, **kw):
            r = resp_map.get(url)
            if r is None:
                fallback = MagicMock(status_code=200, text="<html></html>")
                fallback.headers = {"Content-Type": "text/html"}
                return fallback
            return r
        mock_client.get = fake_get
        mock_client_factory.return_value = mock_client

        result = asyncio.run(afind_portfolio_websites("Acme", "https://acme.it", max_sites=10))
        self.assertEqual(len(result), 2)
        domains = {r["domain"] for r in result}
        self.assertIn("client1.it", domains)
        self.assertIn("client2.com", domains)

    @patch("agency_finder.scraper.make_async_client")
    def test_find_portfolio_skips_social_and_cdn(self, mock_client_factory):
        from agency_finder.scraper import afind_portfolio_websites

        sitemap = self._sitemap_xml(["https://acme.it/portfolio"])
        portfolio_html = "<html><body>" + "".join([
            '<a href="https://facebook.com/acme">FB</a>',
            '<a href="https://instagram.com/acme">IG</a>',
            '<a href="https://google.com/search">Search</a>',
            '<a href="https://cdn.jsdelivr.net/npm/jquery">jQuery</a>',
            '<a href="https://realclient.it">Real Client</a>',
        ]) + "</body></html>"

        sitemap_resp = MagicMock(status_code=200, text=sitemap)
        sitemap_resp.headers = {"Content-Type": "application/xml"}
        portfolio_resp = MagicMock(status_code=200, text=portfolio_html)
        portfolio_resp.headers = {"Content-Type": "text/html"}
        client_resp = MagicMock(status_code=200, text="<html><head><title>Real</title></head></html>")
        client_resp.headers = {"Content-Type": "text/html"}

        resp_map = {
            "https://acme.it/sitemap.xml": sitemap_resp,
            "https://acme.it/portfolio": portfolio_resp,
            "https://realclient.it": client_resp,
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        async def fake_get(url, **kw):
            return resp_map.get(url, client_resp)
        mock_client.get = fake_get
        mock_client_factory.return_value = mock_client

        result = asyncio.run(afind_portfolio_websites("Acme", "https://acme.it", max_sites=10))
        domains = {r["domain"] for r in result}
        self.assertEqual(domains, {"realclient.it"})
        self.assertNotIn("facebook.com", domains)
        self.assertNotIn("google.com", domains)
        self.assertNotIn("cdn.jsdelivr.net", domains)

    def test_find_portfolio_uses_tld_filter(self):
        from agency_finder.scraper import _client_tld_allowed
        self.assertTrue(_client_tld_allowed("client.it"))
        self.assertTrue(_client_tld_allowed("client.com"))
        self.assertTrue(_client_tld_allowed("client.shop"))
        self.assertTrue(_client_tld_allowed("www.client.com"))
        self.assertFalse(_client_tld_allowed("client.ru"))
        self.assertFalse(_client_tld_allowed("client.tk"))
        self.assertFalse(_client_tld_allowed("client.cn"))

    def test_find_portfolio_dedupes_by_domain(self):
        from agency_finder.scraper import _extract_external_domains
        html = """
        <html><body>
          <a href="https://www.client1.it/page1">page 1</a>
          <a href="https://client1.it/page2">page 2</a>
          <a href="https://client1.it/about">about</a>
        </body></html>
        """
        result = _extract_external_domains(html, "acme.it")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["url"], "https://client1.it/page1")

    @patch("agency_finder.scraper.make_async_client")
    def test_find_portfolio_no_sitemap_falls_back_to_homepage(self, mock_client_factory):
        from agency_finder.scraper import afind_portfolio_websites

        sitemap_resp = MagicMock(status_code=404, text="not found")
        sitemap_resp.headers = {"Content-Type": "text/xml"}
        homepage_html = "<html><body>" + "".join([
            '<a href="https://acme.it/about">About</a>',
            '<a href="https://fallback.it">Fallback Client</a>',
        ]) + "</body></html>"
        homepage_resp = MagicMock(status_code=200, text=homepage_html)
        homepage_resp.headers = {"Content-Type": "text/html"}
        fallback_resp = MagicMock(status_code=200, text="<html><head><title>Fallback</title></head></html>")
        fallback_resp.headers = {"Content-Type": "text/html"}

        resp_map = {
            "https://acme.it/sitemap.xml": sitemap_resp,
            "https://acme.it/sitemap_index.xml": sitemap_resp,
            "https://acme.it/sitemap-index.xml": sitemap_resp,
            "https://acme.it": homepage_resp,
            "https://acme.it/": homepage_resp,
            "https://fallback.it": fallback_resp,
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        async def fake_get(url, **kw):
            return resp_map.get(url, sitemap_resp)
        mock_client.get = fake_get
        mock_client_factory.return_value = mock_client

        result = asyncio.run(afind_portfolio_websites("Acme", "https://acme.it", max_sites=10))
        domains = {r["domain"] for r in result}
        self.assertIn("fallback.it", domains)


if __name__ == "__main__":
    unittest.main()
