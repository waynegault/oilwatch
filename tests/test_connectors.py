from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

import oilwatch
from oilwatch.connectors.http_form import HTTPFormConnector
from oilwatch.connectors.manual import ManualConnector
from oilwatch.connectors.price_page import PricePageConnector
from oilwatch.models import QUOTE_REASONS


def fake_response(text: str, url: str = "https://example.com/prices", status_code: int = 200) -> Mock:
    response = Mock()
    response.text = text
    response.url = url
    response.status_code = status_code
    response.raise_for_status = Mock()
    return response


class ManualConnectorTests(unittest.TestCase):
    def test_quote_requires_manual_action(self) -> None:
        supplier = {"id": 1, "name": "A", "phone": "01224 123456", "email": None, "website": "https://a.example.com"}
        result = ManualConnector().quote(supplier, 1000, {})
        self.assertEqual(result.status, "manual_action_required")
        # The number on the record is contact data, not a route the note offers:
        # the app asks by form or by email and never rings a supplier, so naming
        # the site is the answer and the number is left off.
        self.assertNotIn("01224 123456", result.notes)
        self.assertIn("https://a.example.com", result.notes)

    def test_a_supplier_with_a_quote_page_is_not_reported_as_having_none(self) -> None:
        """The reason has to say which way the supplier is out of reach.

        A quote page that answers a person is not the same as no quote page, and
        the two were conflated: five suppliers with a working quote form
        (Compass Fuels, Crown Oil, Gleaner Oils, Nationwide Fuels, Oilfast Insch)
        were all reported as ``no_quote_page``, which tells a reader there is
        nowhere to go - the opposite of the truth.
        """
        supplier = {
            "id": 2,
            "name": "B",
            "phone": "01224 123456",
            "website": "https://b.example.com",
            "connector_config": {"order_page": "https://b.example.com/get-a-quote/"},
        }
        result = ManualConnector().quote(supplier, 1000, {})
        self.assertEqual(result.reason, "quote_by_request")
        self.assertIn("https://b.example.com/get-a-quote/", result.notes)

    def test_a_supplier_with_no_quote_page_still_says_so(self) -> None:
        supplier = {"id": 3, "name": "C", "phone": "01224 123456", "website": "https://c.example.com"}
        result = ManualConnector().quote(supplier, 1000, {})
        self.assertEqual(result.reason, "no_quote_page")
        # No order page to name, so the note must not imply one.
        self.assertNotIn("Quote page:", result.notes)


class QuoteResultContractTests(unittest.TestCase):
    """Every non-ok result a connector builds says why, from the documented set.

    Read from the code rather than from the rows, because the rows are the
    symptom: on 2026-09-18 the latest attempts for ten suppliers carried
    ``reason: null`` because the sites that produce them had never been given a
    value, and a consumer cannot act on a gap nobody explained. A new connector
    that returns a manual or error result with no reason — or with a reason
    invented outside ``QUOTE_REASONS`` — fails here rather than in a report.
    """

    def test_every_non_ok_result_carries_a_documented_reason(self) -> None:
        package = Path(oilwatch.__file__).resolve().parent
        offenders: list[str] = []

        for path in sorted(package.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name != "QuoteResult":
                    continue
                keywords = {kw.arg: kw.value for kw in node.keywords}
                status = keywords.get("status")
                # Only a literal status is judgeable; a computed one is skipped
                # rather than guessed at.
                if not (isinstance(status, ast.Constant) and isinstance(status.value, str)):
                    continue
                if status.value == "ok":
                    continue
                where = f"{path.name}:{node.lineno}"
                reason = keywords.get("reason")
                if reason is None:
                    offenders.append(f"{where} status={status.value} with no reason")
                elif isinstance(reason, ast.Constant) and reason.value not in QUOTE_REASONS:
                    offenders.append(f"{where} reason={reason.value!r} is not in QUOTE_REASONS")

        self.assertEqual(offenders, [], "these results cannot say why they are not ok")


class PricePageConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {
            "id": 1,
            "name": "A",
            "website": "https://a.example.com",
            "connector_config": {
                "quote_url": "https://a.example.com/prices",
                "price_regex": r"(\d+\.\d{2})\s*p(?:ence)?\s*/\s*l",
            },
        }

    def test_quote_normalises_pence(self) -> None:
        # Patch where httpx is actually used: price_page builds its client
        # through oilwatch.http, so patching a name on the connector mocks
        # nothing.
        with patch("oilwatch.http.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("Kerosene 155.80p/L delivered today")
            result = PricePageConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.price_per_liter, 1.558)
        self.assertEqual(result.total_price, 1558.0)

    def test_quote_applies_vat_when_configured(self) -> None:
        self.supplier["connector_config"]["vat_rate"] = 0.05
        with patch("oilwatch.http.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("Kerosene 155.80p/L delivered today")
            result = PricePageConnector().quote(self.supplier, 1000, {})
        self.assertEqual(result.price_per_liter, 1.6359)
        self.assertEqual(result.total_price, 1635.9)

    def test_missing_regex_raises(self) -> None:
        self.supplier["connector_config"].pop("price_regex")
        connector = PricePageConnector()
        with self.assertRaises(ValueError):
            connector.quote(self.supplier, 1000, {})

    def test_a_page_with_no_price_is_a_row_not_a_traceback(self) -> None:
        """The connectors returned error rows; these two raised out of `quote <id>`.

        Latent while no register row names them, but a page that is redesigned or
        a site that will not answer must be reported the way every supplier
        connector reports the same two cases.
        """
        with patch("oilwatch.http.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("<html>no prices today</html>")
            result = PricePageConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "manual_action_required")
        self.assertEqual(result.reason, "no_price_found")
        self.assertIsNone(result.price_per_liter)

    def test_a_dead_site_is_an_error_row(self) -> None:
        with patch("oilwatch.http.httpx.Client") as Client:
            Client.return_value.get.side_effect = httpx.ConnectError("no route")
            result = PricePageConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "site_error")
        self.assertIn("HTTP error fetching", result.notes)

    def test_a_price_that_will_not_parse_is_an_error_row(self) -> None:
        """The pattern matched and the text is not a number: a parse failure."""
        self.supplier["connector_config"]["price_regex"] = r"price:\s*(\S+)"
        with patch("oilwatch.http.httpx.Client") as Client:
            Client.return_value.get.return_value = fake_response("Kerosene price: POA")
            result = PricePageConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "site_error")
        self.assertIn("POA", result.notes)


class HTTPFormConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supplier = {
            "id": 1,
            "name": "A",
            "website": "https://a.example.com",
            "connector_config": {
                "quote_url": "https://a.example.com/api/quote",
                "quote_method": "POST",
                "quote_fields": {"postcode": "{postcode}", "litres": "{quantity_liters}"},
                "price_regex": r'"price_per_liter"\s*:\s*(\d+\.\d+)',
            },
        }

    def _supplier(self, **config) -> dict:
        return {**self.supplier, "connector_config": {**self.supplier["connector_config"], **config}}

    def test_quote_via_form(self) -> None:
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            result = HTTPFormConnector().quote(self.supplier, 1000, {"postcode": "AB00 0AA"})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.price_per_liter, 1.42)
        self.assertEqual(result.total_price, 1420.0)

    def test_a_response_without_a_price_is_a_row_not_a_traceback(self) -> None:
        """A redesigned response must be reported as a gap, never read as a price.

        It used to raise out of `oilwatch quote <id>`; the row that replaces it
        says which of the two things happened — here the site answered and had no
        price, which is `no_price_found` rather than a fault in the retrieval.
        """
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response("<html>No prices today</html>")
            result = HTTPFormConnector().quote(self.supplier, 1000, {"postcode": "AB00 0AA"})

        self.assertEqual(result.status, "manual_action_required")
        self.assertEqual(result.reason, "no_price_found")
        self.assertIsNone(result.price_per_liter)

    def test_a_match_that_is_not_a_price_is_an_error_row(self) -> None:
        supplier = self._supplier(price_regex=r'"price_per_liter"\s*:\s*([^,}]+)')
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": "POA"}')
            result = HTTPFormConnector().quote(supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "site_error")
        self.assertIn("POA", result.notes)

    def test_a_post_that_raises_is_an_error_row(self) -> None:
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.side_effect = httpx.ReadTimeout("timed out")
            result = HTTPFormConnector().quote(self.supplier, 1000, {})

        self.assertEqual(result.status, "error")
        self.assertEqual(result.reason, "site_error")
        self.assertIn("HTTP error posting", result.notes)

    def test_a_row_without_configuration_still_raises(self) -> None:
        """A register entry that names no URL or pattern is not a site problem.

        Every reason the contract defines describes what a site did, so there is
        no honest row for it: the raise names the missing setting, and `quote-all`
        turns it into an error row of its own.
        """
        for config in ({"price_regex": r"(\d+\.\d+)"}, {"quote_url": "https://a.example.com"}):
            with self.subTest(config=sorted(config)):
                supplier = {**self.supplier, "connector_config": config}
                with self.assertRaises(ValueError) as raised:
                    HTTPFormConnector().quote(supplier, 1000, {})

                self.assertIn("configuration", str(raised.exception))

    def test_quote_applies_a_configured_ex_vat_rate(self) -> None:
        supplier = self._supplier(vat_rate=0.05)
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            result = HTTPFormConnector().quote(supplier, 1000, {"postcode": "AB00 0AA"})

        price_per_liter = result.price_per_liter
        assert price_per_liter is not None
        self.assertAlmostEqual(price_per_liter, 1.491, places=4)

    def test_fields_render_the_templates_and_leave_the_rest_alone(self) -> None:
        """quote_fields holds templates and literal values; only strings are formatted."""
        supplier = self._supplier(
            quote_fields={"postcode": "{postcode}", "litres": "{quantity_liters}", "fixed": 1000}
        )
        with patch("oilwatch.connectors.http_form.httpx.Client") as Client:
            Client.return_value.request.return_value = fake_response('{"price_per_liter": 1.42}')
            HTTPFormConnector().quote(supplier, 1000, {"postcode": "AB00 0AA"})

        data = Client.return_value.request.call_args.kwargs["data"]
        self.assertEqual(data, {"postcode": "AB00 0AA", "litres": "1000", "fixed": 1000})

if __name__ == "__main__":
    unittest.main()
