"""What each OilWatch CLI command actually does.

``cli.py`` owns the argument parser and the dispatch; the handlers live here, so
the command surface stays readable and the heavy imports (browser automation, the
Graph client) sit beside the handlers that use them rather than at module level.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Callable

from oilwatch.api_discovery import discover_supplier_api
from oilwatch.auto_register import register_all
from oilwatch.connectors.suppliers import get_telephone_script
from oilwatch.scheduler import OilWatchScheduler
from oilwatch.service import OilWatchApp


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


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


# One handler per subcommand, keyed by the parser's command name. The table and
# the parser are checked against each other in tests/test_cli.py: a command with
# no handler, or a handler with no command, fails the suite rather than a KeyError
# at the point of use.
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
