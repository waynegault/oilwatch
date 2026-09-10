"""The enquiry-form submission: field filling, dropdowns and the status dict.

The driver and Select are faked and the form data is synthetic, so the flow runs
without a browser.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch import form_submit
from oilwatch.form_submit import _select_option, submit_all, submit_request

FORM = {
    "url": "https://example.test/quote",
    "fields": {"name": "your-name", "email": "your-email", "postcode": "your-postcode"},
}


class FakeOption:
    def __init__(self, text: str) -> None:
        self.text = text


class FakeSelect:
    """Accepts a visible-text match only when the text is one of its options."""

    last: "FakeSelect | None" = None

    def __init__(self, element) -> None:
        self._labels = [option.text for option in element.options]
        self.selected: list[str] = []
        FakeSelect.last = self

    @property
    def options(self) -> list[FakeOption]:
        return [FakeOption(text) for text in self._labels]

    def select_by_visible_text(self, text: str) -> None:
        if text not in self._labels:
            raise LookupError(f"no option {text!r}")
        self.selected.append(text)


class FakeField:
    def __init__(self, *, tag_name: str = "input", type_: str = "text", options=None, send_keys_raises: bool = False) -> None:
        self.tag_name = tag_name
        self._type = type_
        self.options = options or []
        self.sent: list[str] = []
        self.cleared = 0
        self._send_keys_raises = send_keys_raises

    def get_attribute(self, name: str):
        return self._type if name == "type" else None

    def clear(self) -> None:
        self.cleared += 1

    def send_keys(self, value) -> None:
        if self._send_keys_raises:
            raise RuntimeError("invalid element state")
        self.sent.append(value)


class FakeDriver:
    def __init__(self, *, fields=None, submit_raises: bool = False, submit_missing: bool = False) -> None:
        self._fields = fields or {}
        self._submit_raises = submit_raises
        self._submit_missing = submit_missing
        self.urls: list[str] = []
        self.scripts: list[str] = []
        self.submit: FakeField | None = None

    def get(self, url: str) -> None:
        self.urls.append(url)

    def execute_script(self, script: str, *args) -> None:
        self.scripts.append(script)

    def find_element(self, by, selector: str):
        if "submit" in selector:
            if self._submit_missing:
                raise LookupError("no submit button")
            if self._submit_raises:
                raise LookupError("submit not clickable")
            self.submit = self.submit or FakeField()
            return self.submit
        name = selector.split("'")[1]
        if name not in self._fields:
            raise LookupError(name)
        return self._fields[name]

    def find_elements(self, by, selector: str) -> list:
        return []


class SelectOptionTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSelect.last = None

    def test_exact_visible_text_wins(self) -> None:
        element = FakeField(tag_name="select", options=[FakeOption("1000 litres")])
        with patch("oilwatch.form_submit.Select", FakeSelect):
            _select_option(element, "1000 litres")
        self.assertEqual(FakeSelect.last.selected, ["1000 litres"])

    def test_a_partial_match_is_used_when_the_exact_text_is_absent(self) -> None:
        element = FakeField(tag_name="select", options=[FakeOption("1000 litres")])
        with patch("oilwatch.form_submit.Select", FakeSelect):
            _select_option(element, "1000")
        self.assertEqual(FakeSelect.last.selected, ["1000 litres"])

    def test_it_falls_back_to_the_first_real_option(self) -> None:
        element = FakeField(tag_name="select", options=[FakeOption("Please select"), FakeOption("500 litres")])
        with patch("oilwatch.form_submit.Select", FakeSelect):
            _select_option(element, "no such option")
        self.assertEqual(FakeSelect.last.selected, ["500 litres"])

    def test_nothing_is_selected_when_every_option_is_a_placeholder(self) -> None:
        element = FakeField(tag_name="select", options=[FakeOption("Please select"), FakeOption("-- none --")])
        with patch("oilwatch.form_submit.Select", FakeSelect):
            _select_option(element, "no such option")
        self.assertEqual(FakeSelect.last.selected, [])


def _fields(**overrides):
    fields = {
        "your-name": FakeField(),
        "your-email": FakeField(),
        "your-postcode": FakeField(),
    }
    fields.update(overrides)
    return fields


class SubmitRequestTests(unittest.TestCase):
    def _submit(self, driver, supplier="test_supplier", **kwargs):
        kwargs.setdefault("phone", "")
        with (
            patch.dict(form_submit.SUPPLIER_FORMS, {"test_supplier": FORM}, clear=True),
            patch("oilwatch.form_submit.time.sleep"),
        ):
            return submit_request(
                driver,
                supplier,
                name=kwargs.pop("name", "Wayne"),
                email=kwargs.pop("email", "owner@example.test"),
                postcode=kwargs.pop("postcode", "AB21 0YA"),
                address=kwargs.pop("address", ""),
                quantity_liters=kwargs.pop("quantity_liters", 1000),
                **kwargs,
            )

    def test_an_unknown_supplier_is_reported(self) -> None:
        result = self._submit(FakeDriver(), supplier="nobody")
        self.assertEqual(result["status"], "unknown")

    def test_a_successful_submission_fills_every_field_and_submits(self) -> None:
        fields = _fields()
        driver = FakeDriver(fields=fields)

        result = self._submit(driver)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(driver.urls, [FORM["url"]])
        self.assertEqual(fields["your-name"].sent, ["Wayne"])
        self.assertEqual(fields["your-email"].sent, ["owner@example.test"])
        self.assertEqual(fields["your-postcode"].sent, ["AB21 0YA"])
        self.assertEqual(fields["your-name"].cleared, 1)
        self.assertTrue(driver.scripts)  # the submit click

    def test_a_field_that_will_not_accept_a_value_is_reported(self) -> None:
        driver = FakeDriver(fields=_fields(**{"your-email": FakeField(send_keys_raises=True)}))
        result = self._submit(driver)
        self.assertEqual(result["status"], "error")
        self.assertIn("field email", result["message"])

    def test_a_missing_submit_button_is_reported(self) -> None:
        result = self._submit(FakeDriver(fields=_fields(), submit_missing=True))
        self.assertEqual(result["status"], "error")
        self.assertIn("submit:", result["message"])

    def test_submit_all_returns_one_result_per_supplier(self) -> None:
        driver = FakeDriver(fields=_fields())
        with (
            patch.dict(form_submit.SUPPLIER_FORMS, {"test_supplier": FORM, "other_supplier": FORM}, clear=True),
            patch("oilwatch.form_submit.time.sleep"),
        ):
            results = submit_all(
                driver,
                ["test_supplier", "nobody"],
                name="Wayne",
                email="owner@example.test",
                phone="",
                postcode="AB21 0YA",
                address="",
                quantity_liters=1000,
            )

        self.assertEqual([r["status"] for r in results], ["submitted", "unknown"])


if __name__ == "__main__":
    unittest.main()
