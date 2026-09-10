from __future__ import annotations

import unittest

from oilwatch.form_submit import _resolve_field


class _Element:
    """Just enough of a WebElement for _resolve_field."""

    def __init__(self, name: str, type_: str) -> None:
        self._attributes = {"name": name, "type": type_}
        self.tag_name = "input"

    def get_attribute(self, key: str):
        return self._attributes.get(key)


class _Driver:
    """Minimal stand-in: only find_element/find_elements are exercised."""

    def __init__(self, elements: list[_Element]) -> None:
        self._elements = elements

    @staticmethod
    def _name_from(selector: str) -> str:
        return selector.split("'")[1]

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


if __name__ == "__main__":
    unittest.main()
