"""The page surface the browser connectors actually use.

Playwright's ``Page`` and ``ElementHandle`` are broad, and a connector touches a
handful of members of each. Declaring that handful as protocols means a test can
drive a connector's own logic with a fake page — ``tests/fake_async_page.py`` —
without the fake having to *be* a Playwright page, while the source stays checked
against what it may call. (Using ``Any`` for these parameters would silence the
tests and stop checking the source, which is the worse half of the trade.)

The member signatures mirror Playwright's own — same parameter names, and the
keywords the connectors pass — because a protocol may not ask for *more* than the
real class offers: a ``**kwargs`` here would demand that Playwright's ``goto``
accept any keyword at all. The real classes therefore satisfy these protocols,
and ``_setup_browser`` still hands back a genuine ``Page``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Protocol

#: Playwright's own literal for a navigation wait state, inlined: the package
#: does not export one (``playwright.types`` does not exist in 1.x).
WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]

#: Playwright's own literal for a load state, inlined for the same reason.
LoadState = Literal["domcontentloaded", "load", "networkidle"]


class ElementLike(Protocol):
    """The subset of Playwright's ``ElementHandle`` a connector touches."""

    async def fill(self, value: str, *, timeout: float | None = None) -> None: ...

    async def click(self, *, timeout: float | None = None) -> None: ...

    async def text_content(self) -> str | None: ...

    async def get_attribute(self, name: str) -> str | None: ...

    async def evaluate(self, expression: str) -> Any: ...

    async def select_option(self, value: Any = None) -> Any: ...

    async def query_selector_all(self, selector: str) -> Sequence[ElementLike]: ...


class ContextLike(Protocol):
    """The subset of Playwright's ``BrowserContext`` a connector touches."""

    async def close(self) -> None: ...


class PageLike(Protocol):
    """The subset of Playwright's ``Page`` a connector touches."""

    #: Read-only, like ``url``: Playwright exposes it as a property and the fake
    #: as a plain attribute.
    @property
    def context(self) -> ContextLike: ...

    #: Declared read-only so both a Playwright ``Page`` (a property) and the fake
    #: (a plain attribute) satisfy it.
    @property
    def url(self) -> str: ...

    async def goto(self, url: str, *, wait_until: WaitUntil | None = None) -> Any: ...

    async def wait_for_timeout(self, timeout: float) -> None: ...

    async def wait_for_load_state(
        self, state: LoadState | None = None, *, timeout: float | None = None
    ) -> None: ...

    async def content(self) -> str: ...

    async def query_selector(self, selector: str) -> ElementLike | None: ...

    async def query_selector_all(self, selector: str) -> Sequence[ElementLike]: ...
