from __future__ import annotations

import argparse
import json
from pathlib import Path

from oilwatch.api_discovery import APIDiscoveryTool, discover_supplier_api
from oilwatch.auto_register import register_all
from oilwatch.connectors.suppliers.telephone import TelephoneQuoteScript
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

    order = subparsers.add_parser("place-order")
    order.add_argument("supplier_id", type=int)
    order.add_argument("agreed_price_per_liter", type=float)
    order.add_argument("--postcode")
    order.add_argument("--quantity-liters", type=int)

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
    auto_register.add_argument("--address", default="Hatton of Fintray, Aberdeenshire", help="Address")
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
    submit.add_argument("--address", default="Hatton of Fintray, Aberdeenshire", help="Delivery address")
    submit.add_argument("--quantity-liters", type=int, default=1000, help="Quantity in litres")
    submit.add_argument("--suppliers", default="gleaner_oils,oilfast", help="Comma-separated supplier keys")

    # Monitor email for supplier replies (Microsoft Graph / OAuth2)
    subparsers.add_parser("monitor-email")

    # One-time OAuth2 device-code sign-in for the email monitor
    subparsers.add_parser("login-email")

    return parser


def main() -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    app = OilWatchApp()

    if args.command == "init":
        _print(app.init())
    elif args.command == "discover":
        _print(app.discover_suppliers())
    elif args.command == "suppliers":
        _print(app.suppliers(include_inactive=args.include_inactive))
    elif args.command == "quote":
        _print(app.quote_supplier(args.supplier_id, postcode=args.postcode, prefer_browser=args.browser))
    elif args.command == "quote-all":
        _print(app.quote_all(postcode=args.postcode, prefer_browser=args.browser))
    elif args.command == "cheapest":
        _print(app.cheapest())
    elif args.command == "status":
        _print(app.status())
    elif args.command == "chart":
        _print({"chart_path": app.chart()})
    elif args.command == "time-series":
        _print({"chart_path": app.time_series_chart()})
    elif args.command == "import-spreadsheet":
        _print(app.import_spreadsheet(args.path or None))
    elif args.command == "update-brent":
        _print(app.update_brent())
    elif args.command == "place-order":
        _print(
            app.place_order(
                args.supplier_id,
                agreed_price_per_liter=args.agreed_price_per_liter,
                postcode=args.postcode,
                quantity_liters=args.quantity_liters,
            )
        )
    elif args.command == "schedule":
        OilWatchScheduler(app, postcode=args.postcode).run_forever()
    elif args.command == "phone-script":
        script = TelephoneQuoteScript()
        script.configure(
            quantity_liters=args.quantity_liters,
            postcode=args.postcode,
            address=args.address,
            contact_name=args.name,
        )
        suppliers = app.suppliers(include_inactive=False)
        
        # Print quick reference
        print(script.get_quick_reference(suppliers))
        print()
        
        # Print individual scripts
        for supplier in suppliers:
            print(script.generate_script(supplier))
        
        # Export to JSON if output path specified
        if args.output:
            output_path = Path(args.output)
            script.export_to_json(suppliers, output_path)
            print(f"\nCall sheet exported to: {output_path}")
    elif args.command == "api-discover":
        import asyncio
        
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
        results = asyncio.run(discover_supplier_api(url, output_file))
        _print(results)
    elif args.command == "register":
        import asyncio
        
        print(f"Registering accounts on supplier websites...")
        print(f"  Name: {args.name}")
        print(f"  Email: {args.email}")
        print(f"  Address: {args.address}, {args.postcode}")
        print()
        
        results = asyncio.run(register_all(
            name=args.name,
            email=args.email,
            phone=args.phone,
            address=args.address,
            postcode=args.postcode,
            headless=not args.visible,
        ))
        
        print()
        print("=" * 60)
        print("REGISTRATION SUMMARY")
        print("=" * 60)
        for result in results:
            status_icon = "✅" if result["status"] == "registered" else "⚠️" if result["status"] == "already_registered" else "❌"
            print(f"{status_icon} {result['supplier']}: {result['status']}")
            print(f"   Email: {result['email']}")
            print(f"   Password: {result['password']}")
            print(f"   Message: {result['message']}")
            print()
        
        if args.output:
            from oilwatch.auto_register import AccountRegistrar
            registrar = AccountRegistrar()
            registrar._results = results
            output_path = registrar.save_results(args.output)
            print(f"Results saved to: {output_path}")
    elif args.command == "login":
        from oilwatch.browser_auth import BrowserAuth

        login_urls = {
            "scottish_fuels": "https://quote.scottishfuels.co.uk/quote/",
        }
        url = args.url or login_urls.get(args.supplier)
        if not url:
            print(f"Error: no login URL for '{args.supplier}'. Pass --url to override.")
            return
        auth = BrowserAuth(args.supplier)
        auth.interactive_login(url)
        auth.close()
    elif args.command == "submit-requests":
        from oilwatch.browser_auth import BrowserAuth
        from oilwatch.form_submit import submit_all

        supplier_keys = [s.strip() for s in args.suppliers.split(",") if s.strip()]
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
                address=args.address,
                quantity_liters=args.quantity_liters,
            )
        finally:
            auth.close()
        _print(results)
    elif args.command == "monitor-email":
        # Go through the service rather than straight to GraphEmailMonitor: the
        # service initialises the schema first (this path used to skip that, so a
        # newly added table was simply missing on an existing database) and turns
        # a transient failure into a message instead of a traceback.
        _print(app.monitor_email())
    elif args.command == "login-email":
        from oilwatch.graph_email import GraphEmailMonitor

        result = GraphEmailMonitor().interactive_login()
        if "access_token" in result:
            print("Email authentication successful - token cached.")
        else:
            print(f"Authentication failed: {result.get('error_description', result.get('error', result))}")


if __name__ == "__main__":
    main()

