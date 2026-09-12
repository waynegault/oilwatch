from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from oilwatch.discounts import DiscountOffer, best_discount_for, parse_discounts

# The real ValueOils email, lightly trimmed.
VALUEOILS_EMAIL = """\
Hi,
It's been a while since your last order - and we'd love to have you back at ValueOils.com.
To make your return easier, here's a special limited-time discount just for you if you
order online within the next 48 hours:
£10 OFF 500-999 litres - Code: UWCNI154305
£12 OFF 1,000-1,999 litres - Code: KJHA154306
£15 OFF 2,000+ litres - Code: THB154307
Use your code at checkout and enjoy fast, secure online ordering - anytime, anywhere.
Act now - your exclusive discount expires in 48 hours.
Best wishes,
The ValueOils.com Team
Please note, prices are live and can vary at any time.
This voucher cannot be used in conjunction with
"""

RECEIVED = datetime(2026, 9, 10, 9, 0, 0)


class ParseValueOilsEmailTests(unittest.TestCase):
    def test_finds_all_three_offers(self) -> None:
        offers = parse_discounts(VALUEOILS_EMAIL, received_at=RECEIVED)
        self.assertEqual([offer.amount_gbp for offer in offers], [10.0, 12.0, 15.0])
        self.assertEqual([offer.code for offer in offers], ["UWCNI154305", "KJHA154306", "THB154307"])

    def test_reads_the_litre_bands(self) -> None:
        offers = parse_discounts(VALUEOILS_EMAIL, received_at=RECEIVED)
        self.assertEqual((offers[0].min_litres, offers[0].max_litres), (500, 999))
        self.assertEqual((offers[1].min_litres, offers[1].max_litres), (1000, 1999))
        self.assertEqual((offers[2].min_litres, offers[2].max_litres), (2000, None))

    def test_open_ended_band_has_no_upper_limit(self) -> None:
        offers = parse_discounts(VALUEOILS_EMAIL, received_at=RECEIVED)
        self.assertIsNone(offers[2].max_litres)
        self.assertTrue(offers[2].applies_to(10_000))

    def test_resolves_the_48_hour_expiry_against_the_received_date(self) -> None:
        offers = parse_discounts(VALUEOILS_EMAIL, received_at=RECEIVED)
        for offer in offers:
            self.assertEqual(offer.expires_at, RECEIVED + timedelta(hours=48))

    def test_captures_the_exclusion_clause(self) -> None:
        offers = parse_discounts(VALUEOILS_EMAIL, received_at=RECEIVED)
        self.assertIn("cannot be used", offers[0].terms)


class BestOfferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.offers = parse_discounts(VALUEOILS_EMAIL, received_at=RECEIVED)

    def test_picks_the_band_matching_the_order(self) -> None:
        chosen = best_discount_for(self.offers, 1000, now=RECEIVED)
        self.assertIsNotNone(chosen)
        assert chosen is not None  # narrowing for the type checker
        self.assertEqual(chosen.amount_gbp, 12.0)
        self.assertEqual(chosen.code, "KJHA154306")

    def test_picks_the_top_band_for_a_large_order(self) -> None:
        chosen = best_discount_for(self.offers, 2500, now=RECEIVED)
        assert chosen is not None
        self.assertEqual(chosen.amount_gbp, 15.0)

    def test_no_offer_below_the_lowest_band(self) -> None:
        self.assertIsNone(best_discount_for(self.offers, 400, now=RECEIVED))

    def test_expired_offers_are_ignored(self) -> None:
        self.assertIsNone(best_discount_for(self.offers, 1000, now=RECEIVED + timedelta(hours=49)))

    def test_just_inside_the_window_still_counts(self) -> None:
        chosen = best_discount_for(self.offers, 1000, now=RECEIVED + timedelta(hours=47))
        self.assertIsNotNone(chosen)

    def test_discount_reduces_the_total(self) -> None:
        chosen = best_discount_for(self.offers, 1000, now=RECEIVED)
        assert chosen is not None
        self.assertEqual(chosen.discounted_total(1114.40), 1102.40)

    def test_total_never_goes_negative(self) -> None:
        self.assertEqual(DiscountOffer(amount_gbp=50.0).discounted_total(20.0), 0.0)


class ExpiryWordingTests(unittest.TestCase):
    """Suppliers state the deadline in several ways.

    A deadline the parser misses is worse than none: ``is_expired`` returns False
    for an offer with no expiry, so an unread "ends 30/09/2026" would leave a
    dead code looking live and getting applied to quotes.
    """

    def _expiry(self, text: str) -> datetime | None:
        offers = parse_discounts(text, received_at=RECEIVED)
        self.assertEqual(len(offers), 1)
        return offers[0].expires_at

    def test_within_the_next_48_hours(self) -> None:
        self.assertEqual(
            self._expiry("£10 OFF 1,000+ litres - order within the next 48 hours"),
            RECEIVED + timedelta(hours=48),
        )

    def test_expires_tomorrow(self) -> None:
        self.assertEqual(
            self._expiry("£10 OFF 1,000+ litres - expires tomorrow"),
            RECEIVED + timedelta(days=1),
        )

    def test_absolute_date_with_slashes(self) -> None:
        self.assertEqual(self._expiry("£10 OFF 1,000+ litres - ends 30/09/2026"), datetime(2026, 9, 30))

    def test_absolute_date_with_a_month_name(self) -> None:
        self.assertEqual(
            self._expiry("£10 OFF 1,000+ litres - valid until 30 September 2026"),
            datetime(2026, 9, 30),
        )

    def test_a_delivery_date_is_not_an_expiry(self) -> None:
        """'delivery by 28-Sep-2026' must not retire a live offer early."""
        offers = parse_discounts(
            "£10 OFF 1,000+ litres - delivery by 28-Sep-2026", received_at=RECEIVED
        )
        self.assertEqual(len(offers), 1)
        self.assertIsNone(offers[0].expires_at)


class RobustnessTests(unittest.TestCase):
    def test_empty_text_is_not_an_error(self) -> None:
        self.assertEqual(parse_discounts(""), [])

    def test_a_plain_quote_email_yields_nothing(self) -> None:
        text = "Thanks for your enquiry. Your price is 101.03p per litre (Excl. VAT)."
        self.assertEqual(parse_discounts(text, received_at=RECEIVED), [])

    def test_percentage_discount_is_not_mistaken_for_pounds(self) -> None:
        """'10% off' must not become a £10 offer."""
        self.assertEqual(parse_discounts("10% off your next order", received_at=RECEIVED), [])

    def test_a_code_is_not_invented_without_the_word_code(self) -> None:
        offers = parse_discounts("£10 OFF 500-999 litres, ask for details", received_at=RECEIVED)
        self.assertEqual(len(offers), 1)
        self.assertIsNone(offers[0].code)

    def test_an_ordinary_lower_case_word_is_not_a_code(self) -> None:
        """A code may be lower case now, but a plain word still is not one."""
        offers = parse_discounts("£5 off 500+ litres, use code abcdef", received_at=RECEIVED)
        self.assertEqual(len(offers), 1)
        self.assertIsNone(offers[0].code)

    def test_a_lower_case_code_is_captured(self) -> None:
        """Scottish Fuels issued "autumn25"; a missed code is a lost discount.

        The amount was always captured, so the offer was known but unusable —
        the code is the half that has to survive to the checkout.
        """
        offers = parse_discounts(
            "£25 off your next order - use code autumn25", received_at=RECEIVED
        )
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].amount_gbp, 25.0)
        self.assertEqual(offers[0].code, "autumn25")

    def test_a_mixed_case_code_is_captured_as_written(self) -> None:
        offers = parse_discounts("£25 off, use code Autumn25", received_at=RECEIVED)
        self.assertEqual(offers[0].code, "Autumn25")

    def test_an_upper_case_code_is_unchanged(self) -> None:
        offers = parse_discounts("£25 off, use code AUTUMN25", received_at=RECEIVED)
        self.assertEqual(offers[0].code, "AUTUMN25")

    def test_band_accepts_both_hyphen_and_en_dash(self) -> None:
        hyphen = parse_discounts("£12 OFF 1,000-1,999 litres - Code: ABC12345", received_at=RECEIVED)
        endash = parse_discounts("£12 OFF 1,000–1,999 litres - Code: ABC12345", received_at=RECEIVED)
        for offers in (hyphen, endash):
            self.assertEqual((offers[0].min_litres, offers[0].max_litres), (1000, 1999))

    def test_offer_without_a_stated_expiry_does_not_expire(self) -> None:
        offers = parse_discounts("£10 OFF 500+ litres - Code: ABC12345", received_at=RECEIVED)
        self.assertIsNone(offers[0].expires_at)
        self.assertFalse(offers[0].is_expired(now=RECEIVED + timedelta(days=365)))


    def test_offers_collapsed_onto_one_line_are_all_found(self) -> None:
        """The real email collapses to one line once its HTML is stripped.

        Taking only the first amount per line captured the £10 code and missed
        the £12 one — the only offer that applied to a 1,000L order.
        """
        collapsed = (
            "Hi, It's been a while since your last order. "
            "£10 OFF 500-999 litres - Code: UWCNI154305 "
            "£12 OFF 1,000-1,999 litres - Code: KJHA154306 "
            "£15 OFF 2,000+ litres - Code: THB154307 Act now, expires in 48 hours."
        )
        offers = parse_discounts(collapsed, received_at=RECEIVED)

        self.assertEqual([offer.amount_gbp for offer in offers], [10.0, 12.0, 15.0])
        self.assertEqual(
            [offer.code for offer in offers], ["UWCNI154305", "KJHA154306", "THB154307"]
        )

    def test_a_collapsed_offer_does_not_borrow_the_next_band_or_code(self) -> None:
        """Each offer's search window stops at the following amount."""
        collapsed = "£10 OFF 500-999 litres £12 OFF 1,000-1,999 litres - Code: KJHA154306"
        offers = parse_discounts(collapsed, received_at=RECEIVED)

        self.assertEqual((offers[0].min_litres, offers[0].max_litres), (500, 999))
        self.assertIsNone(offers[0].code, "the following offer's code must not be borrowed")
        self.assertEqual((offers[1].min_litres, offers[1].max_litres), (1000, 1999))
        self.assertEqual(offers[1].code, "KJHA154306")

    def test_the_best_code_for_a_thousand_litres_is_the_twelve_pound_one(self) -> None:
        """The point of the fix: 1,000L is served by the £12 offer, not the £10."""
        collapsed = (
            "£10 OFF 500-999 litres - Code: UWCNI154305 "
            "£12 OFF 1,000-1,999 litres - Code: KJHA154306 "
            "£15 OFF 2,000+ litres - Code: THB154307 expires in 48 hours"
        )
        chosen = best_discount_for(parse_discounts(collapsed, received_at=RECEIVED), 1000, now=RECEIVED)

        self.assertIsNotNone(chosen)
        assert chosen is not None
        self.assertEqual(chosen.amount_gbp, 12.0)
        self.assertEqual(chosen.code, "KJHA154306")


if __name__ == "__main__":
    unittest.main()
