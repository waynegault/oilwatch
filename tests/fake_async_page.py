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
        click_raises: bool = False,
        fill_raises: bool = False,
        select_raises: bool = False,
    ) -> None:
        self.tag = tag
        self.text = text
        self.attributes = attributes or {}
        self.options = options or []
        self.click_raises = click_raises
        self.fill_raises = fill_raises
        self.select_raises = select_raises
        self.filled: list[str] = []
        self.clicked = 0
        self.selected: list[Any] = []

    async def fill(self, value: str, **kwargs: Any) -> None:
        # Playwright's fill(value, timeout=...) takes keyword options; accept and
        # ignore them so a connector can cap the wait on a possibly-hidden field
        # without the fake rejecting the call.
        if self.fill_raises:
            raise RuntimeError("element is not editable")
        self.filled.append(value)

    async def click(self, **kwargs: Any) -> None:
        # A click that raises is not a click: a banner that will not take the
        # click, or a submit that navigates the page out from under Playwright.
        if self.click_raises:
            raise RuntimeError("element is not clickable at point (1, 2)")
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
        if self.select_raises:
            raise RuntimeError("element is not selectable")
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


class FakeRequest:
    def __init__(self, url: str = "https://example.test/api/quote") -> None:
        self.url = url
        self.method = "GET"
        self.headers: dict[str, str] = {}
        self.post_data = None


class FakeResponse:
    def __init__(self, text: str = '{"PPL": 1.05}', content_type: str = "application/json") -> None:
        self._text = text
        self.status = 200
        self.headers = {"content-type": content_type}

    async def text(self) -> str:
        return self._text


class FakeRoute:
    """A request the tool intercepted, and the response it can be given."""

    def __init__(self, request: FakeRequest) -> None:
        self.request = request
        self._response = FakeResponse()
        self.fulfilled = False

    async def fetch(self) -> FakeResponse:
        return self._response

    async def fulfill(self, response: Any = None) -> None:
        self.fulfilled = True


class FakeContext:
    """The browser context: it records the route handler and hands out the page."""

    def __init__(self, page: Any) -> None:
        self._page = page
        self.route_handler = None
        self.unrouted_with = None
        self.closed = False

    async def route(self, pattern: str, handler) -> None:
        self.route_handler = handler

    async def unroute_all(self, behavior: str | None = None) -> None:
        # Mirrors Playwright's browser_context.unroute_all(); teardown passes
        # 'ignoreErrors' so an in-flight callback cannot raise as it closes.
        self.unrouted_with = behavior

    async def new_page(self) -> Any:
        return self._page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self._context = context
        self.closed = False

    async def new_context(self, **kwargs: Any) -> FakeContext:
        return self._context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self, browser: FakeBrowser) -> None:
        self._browser = browser

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        return self._browser


class FakeAsyncPlaywright:
    """Stands in for ``async_playwright`` — patch the module's name with it.

    Shared by the connector that launches its own browser (``browser_base``) and
    by the API-discovery tool, which does the same playwright sequence; one fake
    serves both rather than each test file growing its own.
    """

    def __init__(self, browser: FakeBrowser) -> None:
        self.chromium = FakeChromium(browser)
        self.stopped = False

    async def start(self) -> "FakeAsyncPlaywright":
        return self

    async def stop(self) -> None:
        self.stopped = True
