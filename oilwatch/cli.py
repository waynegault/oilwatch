from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Callable

from oilwatch.api_discovery import discover_supplier_api
from oilwatch.auto_register import register_all
from oilwatch.connectors.suppliers import get_telephone_script
from oilwatch.identity import load_contact
from oilwatch.logging_setup import configure_logging
from oilwatch.scheduler import OilWatchScheduler
from oilwatch.service import OilWatchApp


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


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


# One handler per subcommand. Heavy imports (browser automation, Graph) stay
# inside the few handlers that need them, so `oilwatch suppliers` does not drag
# Playwright or msal into memory.
#
# HANDLERS and the parser are checked against each other in tests/test_cli.py: a
# command with no handler, or a handler with no command, fails the suite rather
# than a KeyError at the point of use.


def _cmd_init(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.init())


def _cmd_discover(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.discover_suppliers())


def _cmd_suppliers(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.suppliers(include_inactive=args.include_inactive))


def _cmd_quote(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(
        app.quote_supplier(args.supplier_id, postcode=args.postcode, prefer_browser=args.browser)
    )


def _cmd_quote_all(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.quote_all(postcode=args.postcode, prefer_browser=args.browser))


def _cmd_cheapest(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.cheapest())


def _cmd_status(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.status())


def _cmd_chart(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print({"chart_path": app.chart()})


def _cmd_time_series(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print({"chart_path": app.time_series_chart()})


def _cmd_import_spreadsheet(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.import_spreadsheet(args.path or None))


def _cmd_update_brent(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.update_brent())


def _cmd_record_purchase(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(
        app.record_purchase(
            args.supplier,
            quantity_liters=args.litres,
            price_per_liter=args.price_per_liter,
            total_price=args.total,
            code=args.code,
            reference=args.reference,
            notes=args.notes,
            ordered_at=args.ordered_at,
        )
    )


def _cmd_purchases(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.purchases(limit=args.limit))


def _cmd_schedule(app: OilWatchApp, args: argparse.Namespace) -> None:
    OilWatchScheduler(app, postcode=args.postcode).run_forever()


def _cmd_phone_script(app: OilWatchApp, args: argparse.Namespace) -> None:
    script = get_telephone_script()
    script.configure(
        quantity_liters=args.quantity_liters,
        postcode=args.postcode,
        address=args.address,
        contact_name=args.name,
    )
    suppliers = app.suppliers(include_inactive=False)

    print(script.get_quick_reference(suppliers))
    print()
    for supplier in suppliers:
        print(script.generate_script(supplier))

    if args.output:
        output_path = Path(args.output)
        script.export_to_json(suppliers, output_path)
        print(f"\nCall sheet exported to: {output_path}")


def _cmd_api_discover(app: OilWatchApp, args: argparse.Namespace) -> None:
    url = args.url
    if args.supplier_id:
        supplier = app.db.get_supplier(args.supplier_id)
        if supplier:
            url = supplier.get("website", "")

    if not url:
        print("Error: Please provide --url or --supplier-id")
        return

    output_file = args.output or f"api_discovery_{url.replace('https://', '').replace('/', '_')}.json"
    print(f"Discovering APIs on: {url}")
    _print(asyncio.run(discover_supplier_api(url, output_file)))


def _cmd_register(app: OilWatchApp, args: argparse.Namespace) -> None:
    address = args.address or app.settings.home.label
    print("Registering accounts on supplier websites...")
    print(f"  Name: {args.name}")
    print(f"  Email: {args.email}")
    print(f"  Address: {address}, {args.postcode}")
    print()

    results = asyncio.run(
        register_all(
            name=args.name,
            email=args.email,
            phone=args.phone,
            address=address,
            postcode=args.postcode,
            headless=not args.visible,
        )
    )

    print()
    print("=" * 60)
    print("REGISTRATION SUMMARY")
    print("=" * 60)
    for result in results:
        print(f"{result['supplier']}: {result['status']}")
        print(f"   Email: {result['email']}")
        print(f"   Message: {result['message']}")
        print()

    # The generated passwords are deliberately not echoed: they are in the
    # encrypted credential store, and in the saved run when --output is given.
    print("Credentials were stored for automated sign-in. Use --output to keep")
    print("this run, generated passwords included, as a file.")

    if args.output:
        from oilwatch.auto_register import AccountRegistrar

        output_path = AccountRegistrar().save_results(args.output, results)
        print(f"Results saved to: {output_path}")


def _cmd_login(app: OilWatchApp, args: argparse.Namespace) -> None:
    from oilwatch.browser_auth import BrowserAuth

    url = args.url or app.settings.login_urls.get(args.supplier)
    if not url:
        print(f"Error: no login URL for '{args.supplier}'. Pass --url to override.")
        return
    auth = BrowserAuth(args.supplier)
    auth.interactive_login(url)
    auth.close()


def _cmd_submit_requests(app: OilWatchApp, args: argparse.Namespace) -> None:
    from oilwatch.browser_auth import BrowserAuth
    from oilwatch.form_submit import submit_all

    supplier_keys = [
        s.strip()
        for s in (args.suppliers or ",".join(app.settings.submit_request_suppliers)).split(",")
        if s.strip()
    ]
    if not supplier_keys:
        print("Error: no suppliers given and none configured. Pass --suppliers.")
        return
    auth = BrowserAuth("form_submit")
    driver = auth.launch(headless=True)
    try:
        results = submit_all(
            driver,
            supplier_keys,
            name=args.name,
            email=args.email,
            phone=args.phone,
            postcode=args.postcode,
            address=args.address or app.settings.home.label,
            quantity_liters=args.quantity_liters,
        )
    finally:
        auth.close()
    _print(results)


def _cmd_monitor_email(app: OilWatchApp, args: argparse.Namespace) -> None:
    # Go through the service rather than straight to GraphEmailMonitor: the
    # service initialises the schema first (this path used to skip that, so a
    # newly added table was simply missing on an existing database) and turns
    # a transient failure into a message instead of a traceback.
    _print(app.monitor_email())


def _cmd_login_email(app: OilWatchApp, args: argparse.Namespace) -> None:
    from oilwatch.graph_email import GraphEmailMonitor

    result = GraphEmailMonitor().interactive_login()
    if "access_token" in result:
        print("Email authentication successful - token cached.")
    else:
        print(f"Authentication failed: {result.get('error_description', result.get('error', result))}")


HANDLERS: dict[str, Callable[[OilWatchApp, argparse.Namespace], None]] = {
    "init": _cmd_init,
    "discover": _cmd_discover,
    "suppliers": _cmd_suppliers,
    "quote": _cmd_quote,
    "quote-all": _cmd_quote_all,
    "cheapest": _cmd_cheapest,
    "status": _cmd_status,
    "chart": _cmd_chart,
    "time-series": _cmd_time_series,
    "import-spreadsheet": _cmd_import_spreadsheet,
    "update-brent": _cmd_update_brent,
    "record-purchase": _cmd_record_purchase,
    "purchases": _cmd_purchases,
    "schedule": _cmd_schedule,
    "phone-script": _cmd_phone_script,
    "api-discover": _cmd_api_discover,
    "register": _cmd_register,
    "login": _cmd_login,
    "submit-requests": _cmd_submit_requests,
    "monitor-email": _cmd_monitor_email,
    "login-email": _cmd_login_email,
}


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    app = OilWatchApp()
    HANDLERS[args.command](app, args)


if __name__ == "__main__":
    main()
