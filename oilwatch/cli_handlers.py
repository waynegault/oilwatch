"""What each OilWatch CLI command actually does.

``cli.py`` owns the argument parser and the dispatch; the handlers live here, so
the command surface stays readable and the heavy imports (browser automation, the
Graph client) sit beside the handlers that use them rather than at module level.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections.abc import Callable
from typing import Any

from oilwatch.api_discovery import discover_supplier_api
from oilwatch.auto_register import register_all
from oilwatch.identity import Contact
from oilwatch.scheduler import OilWatchScheduler
from oilwatch.service import OilWatchApp


class CliError(RuntimeError):
    """A command that could not do its job, reported to the shell as a failure.

    These paths used to print their reason and return, so every one of them —
    ``api-discover`` with no URL, a sign-in with no configured URL, an
    authentication the mailbox refused — exited 0. A person reads the message
    either way; the difference only shows to a script or an agent driving the
    CLI, which could not tell a failure from a success. ``main()`` catches this,
    prints the same text to stdout as before, and exits 1.
    """


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _require_a_contact(args: argparse.Namespace) -> Contact:
    """The delivery identity an enquiry is made in, refused when it is not usable.

    Checked here rather than left to each site's own validation: a form sent with
    a blank name or a postcode the supplier cannot deliver to is an enquiry
    nobody can answer, and the copy of it written to ``quote_requests`` would be
    reported as one still owed a reply. The fields come from the parser's
    defaults — ``contact.json`` or the ``OILWATCH_*`` variables — so an install
    that has configured neither gets a sentence rather than six blank forms.
    """
    contact = Contact(
        name=args.name, email=args.email, phone=args.phone, postcode=args.postcode
    )
    if not contact.is_complete:
        # `is_complete` is name, email and postcode: what every form below asks
        # for, and what a supplier needs to answer.
        missing = [
            field
            for field in ("name", "email", "postcode")
            if not getattr(contact, field)
        ]
        raise CliError(
            "Error: the enquiry needs a name, an email and a postcode; nothing is "
            f"configured or passed for: {', '.join(missing)}. Set them in "
            "config/contact.json, in the OILWATCH_* environment variables, or pass "
            "--name/--email/--postcode."
        )
    return contact


def _slug_for(url: str) -> str:
    """A filesystem-safe stem for a URL, for the default results filename.

    The scheme and the slashes used to be replaced by hand, which left any other
    reserved character in the name: ``--url http://x.co.uk`` produced
    ``api_discovery_http:__x.co.uk.json`` and a query string kept its ``?``.
    Windows refuses both, and it refused them at the *write*, after the browser
    run had already been paid for — so the owner got a traceback instead of the
    discovery. Everything outside the safe set becomes an underscore.
    """
    return re.sub(r"[^A-Za-z0-9._-]", "_", url.split("://", 1)[-1])


def _cmd_init(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.init())


def _cmd_discover(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.discover_suppliers())


def _cmd_suppliers(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.suppliers(include_inactive=args.include_inactive))


def _cmd_duplicates(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(app.duplicates(include_inactive=args.include_inactive))


def _cmd_quote(app: OilWatchApp, args: argparse.Namespace) -> None:
    _print(
        app.quote_supplier(args.supplier_id, postcode=args.postcode, prefer_browser=args.browser)
    )


def _cmd_quote_all(app: OilWatchApp, args: argparse.Namespace) -> None:
    job_id = getattr(args, "job_id", None)
    if job_id:
        # The detached worker's path: the job row carries the outcome for whoever
        # asked, so this only has to exit non-zero if the sweep raised.
        _print(app.run_refresh_job(job_id, postcode=args.postcode))
        return
    _print(app.quote_all(postcode=args.postcode, prefer_browser=args.browser, started_by="cli"))


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


def _cmd_api_discover(app: OilWatchApp, args: argparse.Namespace) -> None:
    url = args.url
    # Only look the supplier up when no URL was given: an explicit --url is a
    # deliberate choice, and letting the row overwrite it silently ignored the
    # flag the user had just typed.
    if not url and args.supplier_id:
        supplier = app.db.get_supplier(args.supplier_id)
        if supplier:
            url = supplier.get("website", "")

    if not url:
        raise CliError("Error: Please provide --url or --supplier-id")

    output_file = args.output or f"api_discovery_{_slug_for(url)}.json"
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
    if any(result.get("credentials_stored") is False for result in results):
        # Saying they were stored would be the one thing the run must not say
        # when one of them was not: that supplier's message above says which.
        print("Some credentials could NOT be stored - see the messages above.")
    else:
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
        raise CliError(f"Error: no login URL for '{args.supplier}'. Pass --url to override.")
    auth = BrowserAuth(args.supplier)
    auth.interactive_login(url)
    auth.close()


def _record_submitted_requests(
    app: OilWatchApp,
    results: list[dict[str, Any]],
    args: argparse.Namespace,
) -> None:
    """Record each submitted form as a request that is owed an answer.

    A form is answered by a person later, so without a row here the only way to
    tell whether a reply is still coming is to read the mailbox - and there a
    supplier thinking looks exactly like a supplier nobody ever asked. Resolved
    to a supplier row by website, then by name: those are the two keys the
    register and the database share, since `init` upserts one from the other, so
    an entry that resolves by neither means those two have come apart.
    """
    submitted = [result for result in results if result.get("status") == "submitted"]
    if not submitted:
        # Nothing was asked, so the register is not read either: a run that only
        # printed phone numbers should not depend on the file being there.
        return
    from oilwatch.config import load_supplier_registry

    register = load_supplier_registry(app.root)["suppliers"]
    form_of: dict[Any, dict[str, Any]] = {
        (record.get("quote_request") or {}).get("form"): record
        for record in register
        if (record.get("quote_request") or {}).get("form")
    }
    id_by_website: dict[Any, int] = {}
    id_by_name: dict[Any, int] = {}
    for supplier in app.db.list_suppliers():
        id_by_website[supplier.get("website")] = supplier["id"]
        id_by_name[supplier.get("name")] = supplier["id"]
    for result in submitted:
        record = form_of.get(result.get("supplier")) or {}
        supplier_id = id_by_website.get(record.get("website")) or id_by_name.get(
            record.get("name")
        )
        if supplier_id is None:
            continue
        app.db.record_quote_request(
            supplier_id,
            "form",
            quantity_liters=args.quantity_liters,
            postcode=args.postcode,
            note=result.get("message", ""),
        )


def _email_quote_requests(app: OilWatchApp, args: argparse.Namespace) -> list[dict[str, Any]]:
    """Ask the register's hand-answered suppliers by email.

    The register drives this, as it drives the forms, because who gets asked is
    policy and belongs in the version-controlled file rather than a list here.
    Only an *active* supplier with no form is asked: a form is a page this same
    command drives without --by-email, and a supplier already asked that way must
    not be asked twice, while a retired tombstone is not a supplier at all. The
    app never rings a supplier, so one with no address comes back as *not asked*
    rather than as a number to call: a supplier that cannot be reached must not
    read as one that has been.
    """
    from oilwatch.config import load_supplier_registry
    from oilwatch.graph_email import GraphEmailMonitor, request_body, request_subject

    monitor = GraphEmailMonitor()
    subject = request_subject(args.postcode, args.quantity_liters)
    ids_by_name = {
        row.get("name"): row.get("id")
        for row in app.db.list_suppliers(include_inactive=True)
        if row.get("status") == "active"
    }
    results: list[dict[str, Any]] = []
    for record in load_supplier_registry(app.root)["suppliers"]:
        if (record.get("status") or "active") != "active":
            continue
        request = record.get("quote_request") or {}
        if request.get("form"):
            continue  # the form path owns this supplier; do not ask it twice
        if not request.get("no_form"):
            # No quote_request at all: a connector prices this one, so there is
            # nobody to ask and nothing to report. Naming it here would read as
            # "needs asking" for a supplier that is already quoted.
            continue
        name = record.get("name") or "supplier"
        address = record.get("email")
        if not address:
            results.append(
                {
                    "supplier": name,
                    "status": "no_address",
                    "message": (
                        "Not asked: the register carries no form and no email "
                        "address for this supplier."
                    ),
                }
            )
            continue
        try:
            monitor.send(
                address,
                subject,
                request_body(
                    name=args.name,
                    email=args.email,
                    phone=args.phone,
                    address=args.address or app.settings.home.label,
                    postcode=args.postcode,
                    quantity_liters=args.quantity_liters,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - reported per supplier, like the forms
            results.append({"supplier": name, "status": "error", "message": str(exc)})
            continue
        results.append(
            {"supplier": name, "status": "sent", "message": f"Asked by email to {address}."}
        )
        supplier_id = ids_by_name.get(name)
        if supplier_id is not None:
            app.db.record_quote_request(
                supplier_id,
                "email",
                quantity_liters=args.quantity_liters,
                postcode=args.postcode,
                note=f"Asked by email to {address}",
            )
    return results


def _cmd_submit_requests(app: OilWatchApp, args: argparse.Namespace) -> None:
    # Both ways of asking — the form path and --by-email — are made in the
    # owner's own name, so the identity is checked before either one runs.
    _require_a_contact(args)

    if args.by_email:
        _print(_email_quote_requests(app, args))
        return

    from oilwatch.browser_auth import BrowserAuth
    from oilwatch.config import load_supplier_registry
    from oilwatch.form_submit import requests_from, submit_all

    # Derived from the supplier register, which is version controlled, rather
    # than from a list in the gitignored settings.json. Only a record with a
    # form is work here: a supplier with no form is asked by email instead
    # (--by-email), and one with neither a form nor an address is not asked at
    # all, so it is neither driven nor reported as unreachable.
    if args.suppliers:
        supplier_keys = [s.strip() for s in args.suppliers.split(",") if s.strip()]
    else:
        registry = load_supplier_registry(app.root)
        supplier_keys = requests_from(registry["suppliers"])
    if not supplier_keys:
        raise CliError(
            "Error: no suppliers given and none configured with a quote form. "
            "Pass --suppliers, or ask the address-only ones with --by-email."
        )

    # Launched only when there is a form to drive: a register with none should
    # not open a browser to do nothing.
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
    _record_submitted_requests(app, results, args)
    _print(results)


def _cmd_monitor_email(app: OilWatchApp, args: argparse.Namespace) -> None:
    # Go through the service rather than straight to GraphEmailMonitor: the
    # service initialises the schema first (this path used to skip that, so a
    # newly added table was simply missing on an existing database) and turns
    # a transient failure into a message instead of a traceback.
    result = app.monitor_email()
    _print(result)
    if result.get("error"):
        # The service reports a failure as data so the scheduler's job cannot
        # die on it; at the terminal that same failure must not look like a
        # sweep that simply found nothing.
        raise CliError(f"Error: the sweep failed: {result['error']}")


def _cmd_login_email(app: OilWatchApp, args: argparse.Namespace) -> None:
    from oilwatch.graph_email import GraphEmailMonitor

    result = GraphEmailMonitor().interactive_login()
    if "access_token" not in result:
        raise CliError(
            "Authentication failed: "
            f"{result.get('error_description', result.get('error', result))}"
        )
    print("Email authentication successful - token cached.")


# One handler per subcommand, keyed by the parser's command name. The table and
# the parser are checked against each other in tests/test_cli.py: a command with
# no handler, or a handler with no command, fails the suite rather than a KeyError
# at the point of use.
HANDLERS: dict[str, Callable[[OilWatchApp, argparse.Namespace], None]] = {
    "init": _cmd_init,
    "discover": _cmd_discover,
    "suppliers": _cmd_suppliers,
    "duplicates": _cmd_duplicates,
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
    "api-discover": _cmd_api_discover,
    "register": _cmd_register,
    "login": _cmd_login,
    "submit-requests": _cmd_submit_requests,
    "monitor-email": _cmd_monitor_email,
    "login-email": _cmd_login_email,
}
