"""The telephone quote script tool (pure text/JSON generation)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from oilwatch.connectors.suppliers.telephone import TelephoneQuoteScript

SUPPLIERS = [
    {
        "id": 1,
        "name": "Turriff Fuels",
        "phone": "01888 562706",
        "email": "",
        "website": "https://turriff-fuels.co.uk",
        "notes": "Phone only - no website found.",
    },
    {
        "id": 2,
        "name": "Gleaner Oils",
        "phone": "01224 877575",
        "email": "info@gleaner.co.uk",
        "website": "https://www.gleaner.co.uk",
        "notes": "",
    },
]


class TelephoneQuoteScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = TelephoneQuoteScript()
        self.script.configure(
            quantity_liters=900,
            postcode="AB21 0YA",
            address="Hatton of Fintray",
            contact_name="Wayne",
        )

    def test_script_carries_supplier_and_details(self) -> None:
        text = self.script.generate_script(SUPPLIERS[0])
        self.assertIn("TURRIFF FUELS", text)
        self.assertIn("01888 562706", text)
        self.assertIn("900 litres", text)
        self.assertIn("AB21 0YA", text)

    def test_script_copes_with_a_supplier_missing_details(self) -> None:
        text = self.script.generate_script({"name": "No Details Ltd"})
        self.assertIn("Not available", text)

    def test_call_sheet_lists_every_supplier(self) -> None:
        sheet = self.script.generate_call_sheet(SUPPLIERS)
        self.assertIn("Turriff Fuels", sheet)
        self.assertIn("Gleaner Oils", sheet)

    def test_quick_reference_lists_names_and_phones(self) -> None:
        reference = self.script.get_quick_reference(SUPPLIERS)
        self.assertIn("Turriff", reference)
        self.assertIn("01888 562706", reference)

    def test_export_writes_pending_tracking_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self.script.export_to_json(SUPPLIERS, Path(tmp) / "call-sheets.json")
            data = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(data["customer"]["quantity_liters"], 900)
        self.assertEqual([s["name"] for s in data["suppliers"]], ["Turriff Fuels", "Gleaner Oils"])
        self.assertTrue(all(s["quote_status"] == "pending" for s in data["suppliers"]))


if __name__ == "__main__":
    unittest.main()
