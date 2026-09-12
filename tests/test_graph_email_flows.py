"""GraphEmailMonitor's token plumbing and its inbox sweep.

A fake app/db stands in for the database, so run() is exercised over scripted
messages: which ones get recorded, which are skipped, and which are deleted.
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import MagicMock, patch

from oilwatch.graph_email import (
    GraphEmailMonitor,
    cache_path,
    load_client_id,
    sender_domain_from_email,
)

SETTINGS = types.SimpleNamespace(quote_quantity_liters=1000, currency="GBP")
SUPPLIER = {"id": 3, "name": "Oilfast", "website": "https://oilfast.co.uk"}
PRICED_BODY = {"contentType": "text", "content": "Price today: 105.00p per litre (excl VAT)"}
INBOX = "inbox-folder"


def _message(message_id="m1", sender="sales@oilfast.co.uk", body=None, parent=INBOX):
    return {
        "id": message_id,
        "from": {"emailAddress": {"address": sender}},
        "subject": "Your quote",
        "body": body or PRICED_BODY,
        "bodyPreview": "",
        "receivedDateTime": "2026-09-01T10:00:00Z",
        "parentFolderId": parent,
    }


class FakeDb:
    def __init__(self, *, processed=(), already_recorded=False):
        self._processed = set(processed)
        self._already_recorded = already_recorded
        self.schema_inits = 0
        self.quotes: list[dict] = []
        self.discounts: list[dict] = []
        self.marked: list[str] = []

    def init_schema(self) -> None:
        self.schema_inits += 1

    def message_processed(self, message_id: str) -> bool:
        return message_id in self._processed

    def list_suppliers(self, include_inactive: bool = False) -> list[dict]:
        return [SUPPLIER]

    def quote_already_recorded(self, record: dict) -> bool:
        return self._already_recorded

    def record_quote(self, record: dict) -> None:
        self.quotes.append(record)

    def record_discount(self, record: dict) -> None:
        self.discounts.append(record)

    def mark_message_processed(self, message_id: str) -> None:
        self.marked.append(message_id)


def _app(db: FakeDb):
    return types.SimpleNamespace(db=db, settings=SETTINGS)


def _monitor(**overrides) -> GraphEmailMonitor:
    monitor = GraphEmailMonitor.__new__(GraphEmailMonitor)
    monitor.client_id = "client"
    monitor.app = MagicMock()
    for name, value in overrides.items():
        setattr(monitor, name, value)
    return monitor


class ClientIdTests(unittest.TestCase):
    def test_the_environment_wins(self) -> None:
        with patch.dict("os.environ", {"MICROSOFT_CLIENT_ID": "env-id"}):
            self.assertEqual(load_client_id(), "env-id")

    def test_settings_are_the_fallback(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("oilwatch.config.load_settings", return_value=types.SimpleNamespace(microsoft_client_id="cfg")),
        ):
            self.assertEqual(load_client_id(), "cfg")

    def test_missing_everywhere_is_an_error(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("oilwatch.config.load_settings", side_effect=OSError("no settings")),
        ):
            with self.assertRaises(RuntimeError):
                load_client_id()


class RefreshTokenCacheTests(unittest.TestCase):
    def test_a_saved_token_round_trips(self) -> None:
        with patch("oilwatch.graph_email.cache_path") as path:
            path.return_value = cache_path().parent / "graph_token_cache_test.json"
            path.return_value.unlink(missing_ok=True)
            self.assertIsNone(_monitor()._load_refresh_token())

            _monitor()._save_refresh_token({"refresh_token": "rt"})
            self.assertEqual(_monitor()._load_refresh_token(), "rt")

            path.return_value.unlink(missing_ok=True)

    def test_a_result_without_a_refresh_token_saves_nothing(self) -> None:
        monitor = _monitor()
        with patch("oilwatch.graph_email.cache_path") as path:
            monitor._save_refresh_token({"access_token": "a"})
            path.assert_not_called()

    def test_a_corrupt_cache_reads_as_none(self) -> None:
        with patch("oilwatch.graph_email.cache_path") as path:
            path.return_value = cache_path().parent / "corrupt_test.json"
            path.return_value.write_text("not json", encoding="utf-8")
            self.assertIsNone(_monitor()._load_refresh_token())
            path.return_value.unlink(missing_ok=True)


class TokenTests(unittest.TestCase):
    def test_the_silent_cache_is_used_first(self) -> None:
        monitor = _monitor()
        monitor.app.get_accounts.return_value = [{"username": "owner"}]
        monitor.app.acquire_token_silent.return_value = {"access_token": "silent"}

        self.assertEqual(monitor.get_token(), {"access_token": "silent"})

    def test_a_persisted_refresh_token_is_used_when_silent_fails(self) -> None:
        monitor = _monitor()
        monitor.app.get_accounts.return_value = []
        monitor.app.acquire_token_by_refresh_token.return_value = {"access_token": "refreshed"}

        with patch.object(GraphEmailMonitor, "_load_refresh_token", return_value="rt"):
            self.assertEqual(monitor.get_token(), {"access_token": "refreshed"})

    def test_no_token_available_is_none(self) -> None:
        monitor = _monitor()
        monitor.app.get_accounts.return_value = []
        with patch.object(GraphEmailMonitor, "_load_refresh_token", return_value=None):
            self.assertIsNone(monitor.get_token())

    def test_headers_ask_for_immutable_ids(self) -> None:
        headers = _monitor()._headers({"access_token": "a"})
        self.assertEqual(headers["Authorization"], "Bearer a")
        self.assertIn("ImmutableId", headers["Prefer"])

    def test_interactive_login_prints_the_flow_message(self) -> None:
        monitor = _monitor()
        monitor.app.initiate_device_flow.return_value = {"message": "Go to https://microsoft.com/devicelogin"}
        monitor.app.acquire_token_by_device_flow.return_value = {"refresh_token": "rt"}

        with (
            patch.object(GraphEmailMonitor, "_save_refresh_token") as save,
            patch("builtins.print"),
        ):
            self.assertEqual(monitor.interactive_login(), {"refresh_token": "rt"})

        save.assert_called_once_with({"refresh_token": "rt"})


class SweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor = _monitor()
        self.monitor.get_token = MagicMock(return_value={"access_token": "a"})
        self.monitor.inbox_id = MagicMock(return_value=INBOX)
        self.monitor.delete = MagicMock()

    def _run(self, db: FakeDb, messages, *, parse_discounts=None):
        self.monitor.fetch_candidates = MagicMock(return_value=messages)
        patcher = patch(
            "oilwatch.discounts.parse_discounts",
            return_value=parse_discounts or [],
        )
        with patcher:
            return self.monitor.run(_app(db))

    def test_an_unauthenticated_run_refuses(self) -> None:
        self.monitor.get_token = MagicMock(return_value=None)
        with self.assertRaises(RuntimeError):
            self.monitor.run(_app(FakeDb()))

    def test_a_priced_reply_is_recorded_and_deleted(self) -> None:
        db = FakeDb()
        recorded = self._run(db, [_message()])

        self.assertEqual(len(recorded), 1)
        self.assertEqual(db.quotes[0]["status"], "ok")
        self.assertEqual(db.quotes[0]["source"], "email")
        self.assertAlmostEqual(db.quotes[0]["price_per_liter"], 1.1025, places=4)
        self.assertEqual(db.marked, ["m1"])
        self.monitor.delete.assert_called_once_with({"access_token": "a"}, "m1")

    def test_mail_already_in_the_bin_is_not_deleted_again(self) -> None:
        db = FakeDb()
        self._run(db, [_message(parent="deleted-items")])
        self.assertEqual(db.marked, ["m1"])
        self.monitor.delete.assert_not_called()

    def test_a_processed_message_is_skipped(self) -> None:
        db = FakeDb(processed=["m1"])
        self.assertEqual(self._run(db, [_message()]), [])
        self.assertEqual(db.marked, [])

    def test_an_unknown_sender_is_skipped(self) -> None:
        db = FakeDb()
        self.assertEqual(self._run(db, [_message(sender="spam@example.com")]), [])
        self.assertEqual(db.marked, [])

    def test_a_sender_with_no_matching_supplier_is_skipped(self) -> None:
        db = FakeDb()
        db.list_suppliers = lambda include_inactive=False: []
        self.assertEqual(self._run(db, [_message()]), [])
        self.assertEqual(db.marked, [])

    def test_a_discount_only_reply_is_kept_for_its_code(self) -> None:
        db = FakeDb()
        offer = types.SimpleNamespace(
            code="AUTUMN25", amount_gbp=25.0, min_litres=500, max_litres=None, expires_at=None, terms=""
        )
        self._run(db, [_message(body={"contentType": "text", "content": "Use AUTUMN25"})], parse_discounts=[offer])

        self.assertEqual(db.discounts[0]["code"], "AUTUMN25")
        self.assertEqual(db.quotes, [])
        self.assertEqual(db.marked, ["m1"])

    def test_a_reply_with_nothing_to_learn_is_cleared_but_not_marked(self) -> None:
        """Nothing to record, so it is swept out of the inbox — but left unmarked.

        Marking it would retire it for good; unmarked, a later parser improvement
        can still reach it in Deleted Items, and a real deal can never be dropped
        because parsing always runs before the clearing.
        """
        db = FakeDb()
        self._run(db, [_message(body={"contentType": "text", "content": "Thanks for your enquiry"})])
        self.assertEqual(db.marked, [])
        self.monitor.delete.assert_called_once_with({"access_token": "a"}, "m1")

    def test_a_duplicate_observation_is_not_recorded_twice(self) -> None:
        db = FakeDb(already_recorded=True)
        recorded = self._run(db, [_message()])
        self.assertEqual(recorded, [])
        self.assertEqual(db.marked, ["m1"])

    def test_the_schema_is_initialised_before_the_sweep(self) -> None:
        db = FakeDb()
        self._run(db, [])
        self.assertEqual(db.schema_inits, 1)

    def test_the_email_date_is_used_not_today(self) -> None:
        db = FakeDb()
        self._run(db, [_message()])
        self.assertTrue(db.quotes[0]["observed_at"].startswith("2026-09-01"))


class ClientTests(unittest.TestCase):
    def test_fetch_unseen_returns_the_messages(self) -> None:
        monitor = _monitor()
        response = MagicMock()
        response.json.return_value = {"value": [{"id": "1"}]}
        with patch("oilwatch.graph_email.httpx.get", return_value=response) as get:
            self.assertEqual(monitor.fetch_unseen({"access_token": "a"}), [{"id": "1"}])
        self.assertIn("isRead eq false", get.call_args.kwargs["params"]["$filter"])

    def test_fetch_candidates_survives_an_unavailable_folder(self) -> None:
        monitor = _monitor()
        good = MagicMock()
        good.json.return_value = {"value": [{"id": "1"}]}
        with patch("oilwatch.graph_email.httpx.get", side_effect=[RuntimeError("no access"), good]):
            candidates = monitor.fetch_candidates({"access_token": "a"})
        self.assertEqual([m["id"] for m in candidates], ["1"])

    def test_inbox_id_is_none_when_the_folder_cannot_be_resolved(self) -> None:
        with patch("oilwatch.graph_email.httpx.get", side_effect=RuntimeError("boom")):
            self.assertIsNone(_monitor().inbox_id({"access_token": "a"}))

    def test_delete_calls_the_delete_endpoint(self) -> None:
        response = MagicMock()
        with patch("oilwatch.graph_email.httpx.delete", return_value=response) as delete:
            _monitor().delete({"access_token": "a"}, "m1")
        self.assertIn("m1", delete.call_args.args[0])
        response.raise_for_status.assert_called_once()

    def test_sender_domain_is_lowercased(self) -> None:
        self.assertEqual(sender_domain_from_email("Sales@OilFast.CO.UK"), "oilfast.co.uk")
        self.assertEqual(sender_domain_from_email(""), "")


if __name__ == "__main__":
    unittest.main()
