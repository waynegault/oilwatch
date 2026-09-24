from __future__ import annotations

import unittest

from oilwatch.pricing import (
    DOMESTIC_VAT_RATE,
    apply_vat,
    inclusive_price_and_total,
    inclusive_total,
    normalise_price_per_litre,
    pence_to_pounds,
)


class NormalisePriceTests(unittest.TestCase):
    def test_pence_to_pounds(self) -> None:
        self.assertEqual(normalise_price_per_litre("155.80"), 1.558)
        self.assertEqual(normalise_price_per_litre("132"), 1.32)
        self.assertEqual(normalise_price_per_litre(155.80), 1.558)

    def test_pounds_unchanged(self) -> None:
        self.assertEqual(normalise_price_per_litre("1.558"), 1.558)
        self.assertEqual(normalise_price_per_litre("1.46"), 1.46)
        self.assertEqual(normalise_price_per_litre(1.46), 1.46)

    def test_the_boundary_is_pence(self) -> None:
        """A bare "100" is £1.00/L, not £100/L.

        The heuristic divides at 100 and used a strict ``>``, so exactly 100 —
        an ordinary kerosene price in pence — came back a hundred pounds a litre.
        Nothing has ever quoted £100/L: the realistic pence range starts at
        about 100, and the realistic pounds range is £0.5-£2.
        """
        self.assertEqual(normalise_price_per_litre("100"), 1.0)
        self.assertEqual(normalise_price_per_litre(100), 1.0)
        # And the far side of the boundary still reads as pounds.
        self.assertEqual(normalise_price_per_litre("99.15"), 99.15)

    def test_strips_currency_symbol(self) -> None:
        self.assertEqual(normalise_price_per_litre("£1.46"), 1.46)

    def test_unparseable_returns_none(self) -> None:
        self.assertIsNone(normalise_price_per_litre(None))
        self.assertIsNone(normalise_price_per_litre(""))
        self.assertIsNone(normalise_price_per_litre("n/a"))


class PenceToPoundsTests(unittest.TestCase):
    def test_explicit_pence_conversion(self) -> None:
        self.assertEqual(pence_to_pounds("103.90"), 1.039)
        self.assertEqual(pence_to_pounds("99.15"), 0.9915)
        self.assertEqual(pence_to_pounds(132), 1.32)


class VatTests(unittest.TestCase):
    def test_apply_domestic_vat(self) -> None:
        self.assertEqual(DOMESTIC_VAT_RATE, 0.05)
        self.assertEqual(apply_vat(1.558), 1.6359)
        self.assertEqual(apply_vat(1.46), 1.533)

    def test_inclusive_total(self) -> None:
        self.assertEqual(inclusive_total(1.6359, 1000), 1635.9)
        self.assertEqual(inclusive_total(1.533, 1000), 1533.0)

    def test_inclusive_price_and_total_couples_the_two(self) -> None:
        """The per-litre price is uplifted 5%, and the total follows from it —
        never an ex-VAT per-litre price beside an inclusive total."""
        price_per_liter, total = inclusive_price_and_total(1.039, 1000)
        self.assertEqual(price_per_liter, 1.0909)
        self.assertEqual(total, 1090.9)
        self.assertEqual(total, inclusive_total(price_per_liter, 1000))


if __name__ == "__main__":
    unittest.main()
