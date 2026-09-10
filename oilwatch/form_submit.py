"""Submit price-enquiry forms to the manual suppliers.

Suppliers without a public price (Gleaner Oils, Oilfast, and, where located,
Highland Fuels / Regency Oils) quote by email after an enquiry form is
submitted. This module drives those forms with the undetected browser so the
replies can later be captured by ``oilwatch.graph_email``.

Field mappings are data-driven; each entry describes the form and which fields
map to name/email/phone/postcode/quantity.
"""

from __future__ import annotations

import time
from typing import Any

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select

# Each supplier's form. ``fields`` keys are the semantic names used by
# ``submit_request``; values are the HTML field names / selectors.
SUPPLIER_FORMS: dict[str, dict[str, Any]] = {
    "gleaner_oils": {
        "name": "Gleaner Oils",
        "url": "https://www.gleaner.co.uk/home-heating/winter-heating-oil/",
        "quote_type": {"name": "wpforms[fields][15]", "value": "Receive a Quote"},
        "fields": {
            "name": "wpforms[fields][1]",
            "email": "wpforms[fields][2]",
            "phone": "wpforms[fields][6]",
            "address": "wpforms[fields][3]",
            "postcode": "wpforms[fields][5]",
            "fuel_type": "wpforms[fields][7]",  # select: Kerosene
            "amount": "wpforms[fields][8]",
        },
    },
    "oilfast": {
        "name": "Oilfast Insch",
        "url": "https://oilfast.co.uk/depot/insch/",
        "fields": {
            "name": "input_1",
            "phone": "input_9",
            "email": "input_16",
            "postcode": "input_4",
            "address": "input_3",
            "message": "input_17",
        },
    },
    "highland_fuels": {
        "name": "Highland Fuels",
        "url": "https://www.highlandfuels.co.uk/contact",
        "fields": {
            "name": "fields[yourName]",
            "email": "fields[emailAddress]",
            "phone": "fields[contactNumber][number]",
            "postcode": "fields[postcode]",
            "message": "fields[message]",
        },
    },
    "compass_fuels": {
        "name": "Compass Fuels",
        "url": "https://compassfuels.co.uk/aberdeen-fuel/",
        "fuel_type": "Kerosene Heating Oil",
        "fields": {
            "name": "name",
            "email": "email",
            "phone": "telephone",
            "postcode": "postcode",
            "fuel_type": "fuel_type",
            "amount": "litres",
        },
    },
    "nationwide_fuels": {
        "name": "Nationwide Fuels",
        "url": "https://www.nationwidefuels.co.uk/fuel-products/kerosene/",
        "fuel_type": "Kerosene",
        "fields": {
            "name": "quote[fullname]",
            "email": "quote[email]",
            "phone": "quote[telephone]",
            "postcode": "quote[postcode]",
            "amount": "quote[litres]",
        },
    },
    "crown_oil": {
        "name": "Crown Oil",
        "url": "https://www.crownoil.co.uk/products/kerosene/",
        "fuel_type": "Kerosene",
        "fields": {
            "name": "quote[fullname]",
            "email": "quote[email]",
            "phone": "quote[telephone]",
            "postcode": "quote[postcode]",
            "amount": "quote[litres]",
        },
    },
}


def _set_value(driver, name: str, value: str) -> None:
    el = driver.find_element(By.CSS_SELECTOR, f"[name='{name}']")
    el.clear()
    el.send_keys(value)


def _resolve_field(driver, name: str):
    """Return the element to type into for a form field name.

    wpforms' *smart phone field* keeps the value it submits in a hidden input
    (``wpforms[fields][6]``, ``type=hidden``, zero size) and puts the control a
    person actually types into beside it under a ``wpf-temp-`` name. Typing into
    the hidden one raises "invalid element state", which is what made the Gleaner
    submission fail while the other four suppliers worked.

    Deliberately does **not** test ``is_displayed()``: on these pages every field
    reports not-displayed, including the ones that fill perfectly well, so a
    visibility check would reject working fields.
    """
    element = driver.find_element(By.CSS_SELECTOR, f"[name='{name}']")
    if element.get_attribute("type") == "hidden":
        for candidate in driver.find_elements(By.CSS_SELECTOR, f"[name='wpf-temp-{name}']"):
            if candidate.get_attribute("type") in ("tel", "text", "email", "number"):
                return candidate
    return element


def _select_value(driver, name: str, value: str) -> None:
    Select(driver.find_element(By.CSS_SELECTOR, f"[name='{name}']")).select_by_visible_text(value)


def _select_option(element, value: str) -> None:
    """Select a dropdown option robustly.

    Tries an exact visible-text match, then any option *containing* the value
    (e.g. "1000 litres"), then falls back to the first real option.
    """
    sel = Select(element)
    try:
        sel.select_by_visible_text(value)
        return
    except Exception:  # noqa: BLE001
        pass
    for option in sel.options:
        if value in option.text:
            sel.select_by_visible_text(option.text)
            return
    # last resort: select the first non-placeholder option
    for option in sel.options:
        if option.text.strip() and not option.text.strip().startswith(("Select", "Please", "--")):
            sel.select_by_visible_text(option.text)
            return


def submit_request(
    driver,
    supplier_key: str,
    *,
    name: str,
    email: str,
    phone: str,
    postcode: str,
    address: str,
    quantity_liters: int,
) -> dict[str, Any]:
    """Fill and submit one supplier's enquiry form. Returns a status dict."""
    if supplier_key not in SUPPLIER_FORMS:
        return {"supplier": supplier_key, "status": "unknown", "message": f"No form configured for {supplier_key}"}

    form = SUPPLIER_FORMS[supplier_key]
    driver.get(form["url"])
    time.sleep(6)

    # quote type radio (Gleaner only)
    if "quote_type" in form:
        q = form["quote_type"]
        radio = driver.find_element(By.CSS_SELECTOR, f"[name='{q['name']}'][value='{q['value']}']")
        driver.execute_script("arguments[0].click();", radio)
        time.sleep(0.5)

    fields = form["fields"]
    values = {
        "name": name,
        "email": email,
        "phone": phone,
        "postcode": postcode,
        "address": address,
        "fuel_type": form.get("fuel_type", "Kerosene"),
        "amount": str(quantity_liters),
        "message": f"Please quote for {quantity_liters} litres of kerosene (heating oil) delivered to {postcode}.",
    }
    for semantic, field_name in fields.items():
        if semantic not in values:
            continue
        value = values[semantic]
        if not value:
            continue  # skip empty optional fields (e.g. phone)
        try:
            el = _resolve_field(driver, field_name)
            tag = el.tag_name
            if tag == "select":
                _select_option(el, value)
            else:
                el.clear()
                el.send_keys(value)
            time.sleep(0.3)
        except Exception as exc:  # noqa: BLE001
            return {"supplier": supplier_key, "status": "error", "message": f"field {semantic} ({field_name}): {exc}"}

    # submit
    try:
        submit = driver.find_element(By.CSS_SELECTOR, "button[type='submit'], input[type='submit']")
        driver.execute_script("arguments[0].click();", submit)
        time.sleep(8)
    except Exception as exc:  # noqa: BLE001
        return {"supplier": supplier_key, "status": "error", "message": f"submit: {exc}"}

    return {"supplier": supplier_key, "status": "submitted", "message": "Form submitted; awaiting email reply."}


def submit_all(
    driver,
    suppliers: list[str],
    *,
    name: str,
    email: str,
    phone: str,
    postcode: str,
    address: str,
    quantity_liters: int,
) -> list[dict[str, Any]]:
    results = []
    for key in suppliers:
        results.append(
            submit_request(
                driver,
                key,
                name=name,
                email=email,
                phone=phone,
                postcode=postcode,
                address=address,
                quantity_liters=quantity_liters,
            )
        )
    return results
