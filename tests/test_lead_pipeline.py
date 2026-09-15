import unittest
from unittest.mock import Mock, patch

from lead_pipeline import (
    AUDIT_FAILED,
    FREE_DOMAIN,
    HTTP_ONLY,
    MISSING_META,
    NO_WEBSITE,
    OPPORTUNITY_SOLID,
    CALLMARK_CONTACT_FIELDS,
    CITIES,
    Market,
    audit_website,
    city_grid_bbox,
    has_meta_description,
    is_chain_restaurant,
    normalize_phone,
    normalize_scraped_lead,
    process_results,
    proxy_uri_from_env,
    run_gosom_scraper,
    to_callmark_contact,
)


class LeadPipelineTests(unittest.TestCase):
    def test_phone_uses_last_ten_digits(self):
        self.assertEqual(normalize_phone("+1 (260) 555-0100"), "2605550100")
        self.assertEqual(normalize_phone(""), "")

    def test_market_rotation_has_requested_fifty_cities(self):
        self.assertEqual(len(CITIES), 50)
        self.assertEqual(CITIES[24], ("Ca\u00f1on City", "CO"))

    def test_city_grid_bbox_is_valid_for_fork(self):
        bbox = city_grid_bbox(Market("Fort Wayne", "IN"), 5000)
        values = [float(value) for value in bbox.split(",")]
        self.assertEqual(len(values), 4)
        self.assertLess(values[0], values[2])
        self.assertLess(values[1], values[3])

    def test_scraper_uses_grid_bbox_instead_of_invalid_grid_flag(self):
        completed = Mock(returncode=0, stdout="", stderr="")
        def fake_run(command, **_kwargs):
            mount = next(value for index, value in enumerate(command) if command[index - 1] == "-v")
            output_dir = mount.rsplit(":", 1)[0]
            with open(f"{output_dir}/results.json", "w", encoding="utf-8") as handle:
                handle.write("[]")
            return completed

        with patch("lead_pipeline.subprocess.run", side_effect=fake_run) as run_mock:
            with patch("lead_pipeline.load_scraper_results", return_value=[]):
                run_gosom_scraper(Market("Fort Wayne", "IN"), "http://proxy.test:8000")
        command = run_mock.call_args.args[0]
        self.assertIn("-grid-bbox", command)
        self.assertIn("-grid-cell", command)
        self.assertIn("/run/secrets/gmaps-proxies", command)
        self.assertNotIn("-grid", command)

    def test_chain_filter(self):
        self.assertTrue(is_chain_restaurant("McDonald's #22", "restaurant"))
        self.assertFalse(is_chain_restaurant("Joe's Neighborhood Grill", "restaurant"))

    def test_meta_description_parser(self):
        self.assertTrue(has_meta_description('<meta name="description" content="A real description">'))
        self.assertFalse(has_meta_description('<title>No meta</title>'))

    def test_audit_outcomes(self):
        self.assertEqual(audit_website("")["website_opportunity"], NO_WEBSITE)
        response = Mock(status_code=200, url="http://example.com", text='<meta name="description" content="ok">')
        session = Mock(); session.get.return_value = response
        self.assertEqual(audit_website("http://example.com", session=session)["website_opportunity"], HTTP_ONLY)
        response.url = "https://example.com"; response.text = "<html></html>"
        self.assertEqual(audit_website("https://example.com", session=session)["website_opportunity"], MISSING_META)
        response.url = "https://example.wixsite.com/home"; response.text = '<meta name="description" content="ok">'
        self.assertEqual(audit_website("https://example.wixsite.com/home", session=session)["website_opportunity"], FREE_DOMAIN)
        response.url = "https://www.wixsite.com/home"
        self.assertEqual(audit_website("https://www.wixsite.com/home", session=session)["website_opportunity"], FREE_DOMAIN)
        response.url = "https://example.com"; response.status_code = 503
        self.assertEqual(audit_website("https://example.com", session=session)["website_opportunity"], AUDIT_FAILED)
        session.get.side_effect = Exception("blocked")
        self.assertEqual(audit_website("https://instagram.com/local-business", session=session)["website_opportunity"], AUDIT_FAILED)

    def test_audit_uses_plain_http_html_fetch(self):
        response = Mock(status_code=200, url="https://example.com", text='<meta name="description" content="ok">')
        session = Mock(); session.get.return_value = response
        result = audit_website("https://example.com", session=session)
        session.get.assert_called_once()
        self.assertEqual(result["website_status"], 200)
        self.assertEqual(result["website_opportunity"], OPPORTUNITY_SOLID)

    def test_qualified_lead_and_run_dedupe(self):
        market = Market("Fort Wayne", "IN")
        session = Mock(); session.get.side_effect = Exception("network")
        row = {"name": "Local Electric", "phone": "(260) 555-0100", "website": "https://local.test"}
        lead = normalize_scraped_lead(row, market, "electricians", session=session)
        self.assertIsNotNone(lead)
        self.assertEqual(lead["website_status"], "CHECK_FAILED")
        self.assertEqual(len(process_results([row, row], market)), 1)

    def test_gosom_json_field_aliases_are_captured(self):
        session = Mock(); session.get.side_effect = Exception("network")
        lead = normalize_scraped_lead(
            {
                "title": "Local Plumbing",
                "phone": "+1 260 555 0101",
                "web_site": "http://local.example",
                "link": "https://www.google.com/maps/place/local",
                "review_rating": 4.5,
                "review_count": 12,
                "emails": ["hello@local.example"],
            },
            Market("Fort Wayne", "IN"),
            "plumbers",
            session=session,
        )
        self.assertEqual(lead["website"], "http://local.example")
        self.assertEqual(lead["source_url"], "https://www.google.com/maps/place/local")
        self.assertEqual(lead["rating"], 4.5)
        self.assertEqual(lead["review_count"], 12)
        self.assertEqual(lead["email"], "hello@local.example")

    def test_proxy_env(self):
        self.assertEqual(proxy_uri_from_env({"PROXY_URL": "http://rotating.example:8000"}), "http://rotating.example:8000")
        self.assertEqual(proxy_uri_from_env({"PROXY_HOST": "h", "PROXY_PORT": "1", "PROXY_USERNAME": "u", "PROXY_PASSWORD": "p"}), "http://u:p@h:1")

    def test_callmark_display_field_mapping(self):
        lead = {
            "company_name": "Local Electric",
            "category": "Electrician",
            "market": "Fort Wayne, IN",
            "city": "Fort Wayne",
            "state": "IN",
            "phone": "+1 (260) 555-0100",
            "email": "hello@local.test",
            "source": "gosom/google-maps-scraper",
            "website_opportunity": NO_WEBSITE,
        }
        contact = to_callmark_contact(lead)
        self.assertEqual(
            set(CALLMARK_CONTACT_FIELDS),
            {
                "id", "name", "contactName", "company", "role", "phone", "email", "city",
                "status", "due", "dueDate", "lastCall", "lastOutcome", "notes", "tag", "source",
            },
        )
        self.assertTrue(set(CALLMARK_CONTACT_FIELDS).issubset(contact))
        self.assertIsInstance(contact["id"], int)
        self.assertEqual(contact["name"], "Local Electric")
        self.assertEqual(contact["company"], "Local Electric")
        self.assertEqual(contact["role"], "Electrician")
        self.assertEqual(contact["phone"], "2605550100")
        self.assertEqual(contact["city"], "Fort Wayne, IN")
        self.assertEqual(contact["status"], "New")
        self.assertEqual(contact["due"], "Today")
        self.assertEqual(contact["website_opportunity"], NO_WEBSITE)


if __name__ == "__main__":
    unittest.main()
