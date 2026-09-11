"""OilWatch's command-line surface: the argument parser, the dispatch, and main().

The handlers themselves live in :mod:`oilwatch.cli_handlers`; this module only
knows how to parse a command line and route it. The parser and the handler table
are checked against each other in tests/test_cli.py.
"""

from __future__ import annotations

import argparse

from oilwatch.cli_handlers import HANDLERS
from oilwatch.identity import load_contact
from oilwatch.logging_setup import configure_logging
from oilwatch.service import OilWatchApp


def build_parser() -> argparse.ArgumentParser:
    # Identity defaults come from config/contact.json (gitignored) or the
    # environment — never a literal in source.
    contact = load_contact()
    parser = argparse.ArgumentParser(prog="oilwatch")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init")
    subparsers.add_parser("discover")

    suppliers = subparsers.add_parser("suppliers")
    suppliers.add_argument("--include-inactive", action="store_true")

    quote_one = subparsers.add_parser("quote")
    quote_one.add_argument("supplier_id", type=int)
    quote_one.add_argument("--postcode")
    quote_one.add_argument("--browser", action="store_true", help="Use browser automation")

    quote_all = subparsers.add_parser("quote-all")
    quote_all.add_argument("--postcode")
    quote_all.add_argument("--browser", action="store_true", help="Use browser automation")

    subparsers.add_parser("cheapest")
    subparsers.add_parser("status")
    subparsers.add_parser("chart")
    subparsers.add_parser("time-series")

    import_xls = subparsers.add_parser("import-spreadsheet")
    import_xls.add_argument("--path", default="", help="Path to the .xls spreadsheet")

    subparsers.add_parser("update-brent")

    # Record a purchase already made by phone or on a supplier's own site.
    purchase = subparsers.add_parser(
        "record-purchase", help="Record a purchase you have made (no automation)"
    )
    purchase.add_argument("supplier", help='Supplier name or id, e.g. "Scottish Fuels"')
    purchase.add_argument("--price-per-liter", type=float, help="Price paid, GBP/L inc VAT")
    purchase.add_argument("--total", type=float, help="Total paid, if that is what you know")
    purchase.add_argument("--litres", type=int, default=None, help="Quantity; defaults to the usual order")
    purchase.add_argument("--code", default=None, help="Discount code used, if any")
    purchase.add_argument("--reference", default=None, help="Supplier order reference")
    purchase.add_argument("--notes", default="")
    purchase.add_argument("--date", dest="ordered_at", default=None, help="ISO date, if not today")

    purchases = subparsers.add_parser("purchases", help="List recorded purchases")
    purchases.add_argument("--limit", type=int, default=None)

    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--postcode")

    # Telephone quote script command
    phone_script = subparsers.add_parser("phone-script")
    phone_script.add_argument("--quantity-liters", type=int, default=1000)
    phone_script.add_argument("--postcode", default="")
    phone_script.add_argument("--address", default="")
    phone_script.add_argument("--name", default="")
    phone_script.add_argument("--output", default="")

    # API discovery command
    api_discover = subparsers.add_parser("api-discover")
    api_discover.add_argument("--url", default="", help="URL to discover APIs on")
    api_discover.add_argument("--supplier-id", type=int, help="Supplier ID to discover APIs for")
    api_discover.add_argument("--output", default="", help="Output filename")

    # Auto-register command
    auto_register = subparsers.add_parser("register")
    auto_register.add_argument("--name", default=contact.name, help="Full name")
    auto_register.add_argument("--email", default=contact.email, help="Email address")
    auto_register.add_argument("--phone", default=contact.phone, help="Phone number")
    auto_register.add_argument("--address", default=None, help="Address (defaults to the configured home)")
    auto_register.add_argument("--postcode", default=contact.postcode, help="Postcode")
    auto_register.add_argument("--visible", action="store_true", help="Show browser (non-headless)")
    auto_register.add_argument("--output", default="", help="Save results to file")

    # Interactive sign-in (undetected browser + persistent session)
    login = subparsers.add_parser("login")
    login.add_argument("supplier", help="Supplier key to sign in to (e.g. scottish_fuels)")
    login.add_argument("--url", default="", help="Override the login URL")

    # Submit enquiry forms to manual suppliers
    submit = subparsers.add_parser("submit-requests")
    submit.add_argument("--name", default=contact.name, help="Full name")
    submit.add_argument("--email", default=contact.email, help="Email address")
    submit.add_argument("--phone", default=contact.phone, help="Phone number")
    submit.add_argument("--postcode", default=contact.postcode, help="Delivery postcode")
    submit.add_argument("--address", default=None, help="Delivery address (defaults to the configured home)")
    submit.add_argument("--quantity-liters", type=int, default=1000, help="Quantity in litres")
    submit.add_argument(
        "--suppliers",
        default="",
        help="Comma-separated supplier keys (defaults to the configured list)",
    )

    # Monitor email for supplier replies (Microsoft Graph / OAuth2)
    subparsers.add_parser("monitor-email")

    # One-time OAuth2 device-code sign-in for the email monitor
    subparsers.add_parser("login-email")

    return parser


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    app = OilWatchApp()
    HANDLERS[args.command](app, args)


if __name__ == "__main__":
    main()
