"""Raise a Windows toast notification, best-effort.

Exists so a failed quote run can tell the owner directly instead of relying on
someone reading a log file. Deliberately forgiving: a notification is a nicety,
so every failure path here is swallowed and logged at debug — nothing in
OilWatch should break because a toast could not be shown.

No dependency is needed: PowerShell can reach the WinRT toast API directly, and
the title/message are passed through the environment (escaped) rather than
interpolated into the script, so notification content cannot inject code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

from oilwatch.logging_setup import get_logger

log = get_logger("notify")

APP_ID = "OilWatch"
TIMEOUT_SECONDS = 20

_TOAST_POWERSHELL = """
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
[void][Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml('<toast><visual><binding template="ToastGeneric"><text>' + $env:OILWATCH_TOAST_TITLE + '</text><text>' + $env:OILWATCH_TOAST_MESSAGE + '</text></binding></visual></toast>')
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:OILWATCH_TOAST_APP).Show($toast)
"""


def available() -> bool:
    """True when a toast can plausibly be raised here."""
    return sys.platform == "win32" and shutil.which("powershell") is not None


def notify(title: str, message: str, *, runner=subprocess.run) -> bool:
    """Show a toast. Returns True when one was raised. Never raises.

    ``runner`` is injectable so tests can assert the invocation without
    actually putting a notification on screen.
    """
    if not available():
        log.debug("no toast backend available; skipping: %s - %s", title, message)
        return False

    environment = {
        **os.environ,
        "OILWATCH_TOAST_TITLE": escape(title),
        "OILWATCH_TOAST_MESSAGE": escape(message),
        "OILWATCH_TOAST_APP": APP_ID,
    }
    try:
        completed = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_POWERSHELL],
            env=environment,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - a notification must never break a run
        log.debug("toast failed: %s", exc)
        return False

    if completed.returncode != 0:
        detail = (completed.stderr or b"")[:200]
        log.debug("toast exited %s: %s", completed.returncode, detail)
        return False
    return True


def notify_errors(failures: list[str], *, subject: str = "OilWatch quote collection") -> bool:
    """One toast summarising failed suppliers, rather than one per failure."""
    if not failures:
        return False
    shown = failures[:3]
    more = len(failures) - len(shown)
    body = ", ".join(shown) + (f" (+{more} more)" if more > 0 else "")
    return notify(subject, f"{len(failures)} supplier(s) failed: {body}")
