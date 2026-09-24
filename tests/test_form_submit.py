from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch.form_submit import SUPPLIER_FORMS, _resolve_field, requests_from, submit_request
from oilwatch.waiting import wait_until


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
    """Minimal stand-in: element lookup plus the two calls made on the driver.

    It models the one page transition that matters: the enquiry form's fields are
    on the page before the submit and gone after it, which is how a real page
    shows the submission was accepted. Without that, an accepted form and one the
    site quietly refused look identical from here.
    """

    def __init__(self, elements: list[_Element]) -> None:
        self._elements = elements
        self._submitted = False
        self.urls: list[str] = []
        self.scripts: list[str] = []

    @staticmethod
    def _name_from(selector: str) -> str:
        return selector.split("'")[1]

    def get(self, url: str) -> None:
        self.urls.append(url)

    def execute_script(self, script: str, *args) -> None:
        self.scripts.append(script)
        if any(element.get_attribute("type") == "submit" for element in args):
            self._submitted = True

    def find_element(self, _by, selector: str):
        wanted = self._name_from(selector)
        for element in self._elements:
            if element.get_attribute("name") == wanted:
                return element
        raise LookupError(wanted)

    def find_elements(self, _by, selector: str) -> list[_Element]:
        if self._submitted:
            return []
        wanted = self._name_from(selector)
        return [e for e in self._elements if e.get_attribute("name") == wanted]


class WaitUntilTests(unittest.TestCase):
    """The bounded poll that replaced submit_request's flat sleeps."""

    def _wait(self, condition, **kwargs):
        with patch("oilwatch.waiting.time.sleep"):
            return wait_until(condition, **kwargs)

    def test_it_returns_as_soon_as_the_condition_holds(self) -> None:
        calls = []

        def condition() -> bool:
            calls.append(1)
            return len(calls) >= 3

        self.assertTrue(self._wait(condition))
        self.assertEqual(len(calls), 3)  # it stopped polling once true

    def test_it_gives_up_and_reports_when_the_condition_never_holds(self) -> None:
        self.assertFalse(self._wait(lambda: False, timeout_s=1.0))

    def test_a_condition_that_raises_is_not_fatal(self) -> None:
        """A not-yet-present element raises; that is the normal case for a poll."""

        def condition() -> bool:
            raise LookupError("not in the DOM yet")

        self.assertFalse(self._wait(condition, timeout_s=0.5))


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


class RequestsFromTests(unittest.TestCase):
    """Which suppliers to ask, read from the tracked register.

    The list used to live in gitignored settings.json, so the set of suppliers
    the app chases was neither reviewable in the repository nor shared with it.
    Only a form entry is work here: the app asks by form or by email and never
    rings, so a record with no form is left to the email path rather than
    reported as a number to call.
    """

    def test_a_form_entry_becomes_a_key_to_submit(self) -> None:
        keys = requests_from(
            [{"name": "Gleaner Oils", "quote_request": {"form": "gleaner_oils"}}]
        )
        self.assertEqual(keys, ["gleaner_oils"])

    def test_a_supplier_with_no_form_is_not_work_for_this_path(self) -> None:
        """``no_form`` means it is asked by email, so there is nothing to drive.

        The phone beside it is contact data, not a route: reporting a number to
        ring would name a call the app never makes.
        """
        keys = requests_from(
            [
                {
                    "name": "Turriff Fuels",
                    "phone": "01888 562706",
                    "email": "rory@turriff-fuels.co.uk",
                    "quote_request": {"no_form": True},
                }
            ]
        )
        self.assertEqual(keys, [], "there is no form to drive")

    def test_a_supplier_with_no_request_entry_is_left_alone(self) -> None:
        """Registration in the register is not a request to be chased."""
        keys = requests_from([{"name": "Rix", "phone": "01224 455477"}])
        self.assertEqual(keys, [])

    def test_a_no_form_entry_with_no_address_is_not_asked_at_all(self) -> None:
        """Neither a form nor an address leaves nothing to ask with.

        The run must not turn that gap into a phone call to report: there is no
        phone route any more. The supplier stays out of the ask and the ledger.
        """
        keys = requests_from(
            [{"name": "Nowhere Fuels", "phone": "01224 000000", "quote_request": {"no_form": True}}]
        )
        self.assertEqual(keys, [])


if __name__ == "__main__":
    unittest.main()
