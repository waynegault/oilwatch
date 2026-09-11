from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch.form_submit import SUPPLIER_FORMS, _resolve_field, submit_request


class _Element:
    """Just enough of a WebElement for resolving a field and typing into it."""

    def __init__(self, name: str, type_: str, tag: str = "input") -> None:
        self._attributes = {"name": name, "type": type_}
        self.tag_name = tag
        self.cleared = 0
        self.sent: list[str] = []

    def get_attribute(self, key: str):
        return self._attributes.get(key)

    def clear(self) -> None:
        self.cleared += 1

    def send_keys(self, value: str) -> None:
        self.sent.append(value)


class _Driver:
    """Minimal stand-in: element lookup plus the two calls made on the driver."""

    def __init__(self, elements: list[_Element]) -> None:
        self._elements = elements
        self.urls: list[str] = []
        self.scripts: list[str] = []

    @staticmethod
    def _name_from(selector: str) -> str:
        return selector.split("'")[1]

    def get(self, url: str) -> None:
        self.urls.append(url)

    def execute_script(self, script: str, *args) -> None:
        self.scripts.append(script)

    def find_element(self, _by, selector: str):
        wanted = self._name_from(selector)
        for element in self._elements:
            if element.get_attribute("name") == wanted:
                return element
        raise LookupError(wanted)

    def find_elements(self, _by, selector: str) -> list[_Element]:
        wanted = self._name_from(selector)
        return [e for e in self._elements if e.get_attribute("name") == wanted]


class ResolveFieldTests(unittest.TestCase):
    """wpforms' smart phone field hides the submitted input behind a temp twin.

    The Gleaner submission failed with "invalid element state" because it typed
    into ``wpforms[fields][6]`` — a zero-size hidden input — instead of the tel
    control beside it. The other four suppliers were unaffected.
    """

    def test_a_hidden_phone_field_defers_to_its_temp_twin(self) -> None:
        hidden = _Element("wpforms[fields][6]", "hidden")
        temporary = _Element("wpf-temp-wpforms[fields][6]", "tel")
        driver = _Driver([hidden, temporary])

        self.assertIs(_resolve_field(driver, "wpforms[fields][6]"), temporary)

    def test_an_ordinary_field_is_used_as_is(self) -> None:
        text = _Element("wpforms[fields][1]", "text")
        driver = _Driver([text])

        self.assertIs(_resolve_field(driver, "wpforms[fields][1]"), text)

    def test_a_hidden_field_without_a_twin_is_returned_unchanged(self) -> None:
        hidden = _Element("wpforms[id]", "hidden")
        driver = _Driver([hidden])

        self.assertIs(_resolve_field(driver, "wpforms[id]"), hidden)

    def test_a_hidden_temp_twin_is_not_mistaken_for_the_control(self) -> None:
        hidden = _Element("wpforms[fields][6]", "hidden")
        also_hidden = _Element("wpf-temp-wpforms[fields][6]", "hidden")
        driver = _Driver([hidden, also_hidden])

        self.assertIs(_resolve_field(driver, "wpforms[fields][6]"), hidden)


class SubmitRequestTests(unittest.TestCase):
    """Filling and sending one supplier's form.

    The resolve-field tests above drive the helper directly; these reach the two
    paths inside submit_request that nothing else did: the Gleaner-only
    quote-type radio, and a plain text field going in through clear() and
    send_keys() rather than a dropdown.
    """

    def test_the_quote_type_radio_is_clicked_and_the_fields_are_filled(self) -> None:
        form = SUPPLIER_FORMS["gleaner_oils"]
        fields = form["fields"]
        elements = {
            "name": _Element(fields["name"], "text"),
            "email": _Element(fields["email"], "text"),
            "address": _Element(fields["address"], "text"),
            "postcode": _Element(fields["postcode"], "text"),
            "fuel_type": _Element(fields["fuel_type"], "select-one", tag="select"),
            "amount": _Element(fields["amount"], "text"),
        }
        radio = _Element(form["quote_type"]["name"], "radio")
        submit = _Element("submit", "submit")
        driver = _Driver([radio, *elements.values(), submit])

        with (
            patch("oilwatch.form_submit.time.sleep"),
            # Dropdowns are _select_option's business and covered elsewhere; what
            # matters here is the radio, the text fields and the submit.
            patch("oilwatch.form_submit._select_option"),
        ):
            result = submit_request(
                driver,
                "gleaner_oils",
                name="Wayne",
                email="owner@example.test",
                phone="",  # optional: skipped rather than filled empty
                postcode="AB21 0YA",
                address="Hatton of Fintray",
                quantity_liters=1000,
            )

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(driver.urls, [form["url"]])
        self.assertEqual(elements["name"].sent, ["Wayne"])
        self.assertEqual(elements["email"].sent, ["owner@example.test"])
        self.assertEqual(elements["postcode"].sent, ["AB21 0YA"])
        self.assertEqual(elements["amount"].sent, ["1000"])
        self.assertEqual(elements["name"].cleared, 1)
        self.assertEqual(elements["fuel_type"].sent, [])  # a select, handled as one
        # the quote-type radio, then the submit button, each clicked via the driver
        self.assertEqual(len(driver.scripts), 2)


if __name__ == "__main__":
    unittest.main()
