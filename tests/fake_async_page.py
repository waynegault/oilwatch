"""A minimal async stand-in for a Playwright page.

The browser connectors differ only in the page interactions they perform; the
launch/login machinery in ``BrowserConnector`` is shared. Driving a connector's
own ``login``/``get_quote_with_browser`` against this fake exercises that
supplier-specific logic without a browser or a network.
"""

from __future__ import annotations

from typing import Any


class FakeElement:
    def __init__(
        self,
        *,
        tag: str = "INPUT",
        text: str = "",
        attributes: dict[str, str] | None = None,
        options: list[Any] | None = None,
    ) -> None:
        self.tag = tag
        self.text = text
        self.attributes = attributes or {}
        self.options = options or []
        self.filled: list[str] = []
        self.clicked = 0
        self.selected: list[Any] = []

    async def fill(self, value: str) -> None:
        self.filled.append(value)

    async def click(self, **kwargs: Any) -> None:
        self.clicked += 1

    async def text_content(self) -> str:
        return self.text

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    async def evaluate(self, script: str) -> str:
        return self.tag

    async def query_selector_all(self, selector: str) -> list[Any]:
        return self.options

    async def select_option(self, value: Any = None, **kwargs: Any) -> None:
        self.selected.append(value)


class FakeBrowserContext:
    """The context a page belongs to; closing it is how a connector tears down."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeAsyncPage:
    """Matches selectors by substring, so a connector's exact selector strings
    (which are long, comma-joined lists) do not have to be reproduced."""

    def __init__(
        self,
        *,
        content: str = "",
        url: str = "",
        elements: list[tuple[str, FakeElement]] | None = None,
        selector_all: list[tuple[str, list[FakeElement]]] | None = None,
    ) -> None:
        self._content = content
        self.url = url
        self._elements = elements or []
        self._all = selector_all or []
        self.goto_urls: list[str] = []
        self.waits: list[int] = []
        self.context = FakeBrowserContext()

    async def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_urls.append(url)
        self.url = url

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(ms)

    async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def content(self) -> str:
        return self._content

    async def query_selector(self, selector: str) -> FakeElement | None:
        for needle, element in self._elements:
            if needle in selector:
                return element
        return None

    async def query_selector_all(self, selector: str) -> list[FakeElement]:
        for needle, elements in self._all:
            if needle in selector:
                return elements
        return []
