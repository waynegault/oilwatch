"""A bounded poll, used in place of a flat sleep.

The Playwright connectors get this from ``BrowserConnector._wait_for``; the
Selenium flows (``oilwatch.form_submit`` and the Scottish Fuels connector) used
to sleep a fixed span instead, which is both slower than it needs to be (a fast
page still waits the whole time) and flaky (a slow one is cut off early). This
is the Selenium-side equivalent: poll a readiness condition and give up after a
bound.

The sleep is the stdlib ``time.sleep``, so patching it in a test — on whatever
module the caller imported ``time`` from — avoids the wait entirely.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any


def wait_until(
    condition: Callable[[], Any],
    *,
    timeout_s: float = 15.0,
    interval_s: float = 0.25,
    what: str = "the page",
) -> bool:
    """Poll ``condition`` until it is truthy, bounded by ``timeout_s``.

    Returns ``True`` as soon as the condition holds, so a fast page is not
    padded with dead time, and keeps polling up to the bound so a slow one is
    not cut off. A condition that (for example) looks up an element not on the
    page yet is expected to return falsy or raise; neither is fatal, and a
    condition that never holds simply returns ``False`` rather than raising.
    """
    waited = 0.0
    while waited < timeout_s:
        try:
            if condition():
                return True
        except Exception:  # noqa: BLE001 - "not there yet" is the normal case for a poll
            pass
        time.sleep(interval_s)
        waited += interval_s
    return False
