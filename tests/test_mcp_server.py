"""The MCP server's declared identity and its default delivery postcode.

This file was deleted once as five delegations and a getter, and this is not
that. What is pinned here is the wiring that had drifted: a served version the
handshake took from the MCP framework because the server never declared one, and
a delivery postcode repeated as a literal here while every other caller read it
from configuration.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from oilwatch import __version__, mcp_server
from oilwatch.identity import Contact


class DeclaredIdentityTests(unittest.TestCase):
    def test_the_server_declares_the_package_version(self) -> None:
        """The handshake reports this field; it reported FastMCP's own instead."""
        self.assertEqual(mcp_server.mcp._mcp_server.version, __version__)


class DefaultPostcodeTests(unittest.TestCase):
    def test_refresh_prices_defaults_to_the_configured_postcode(self) -> None:
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch.object(mcp_server._app, "quote_all", return_value=[]) as quote_all,
        ):
            mcp_server.refresh_prices()

        self.assertEqual(quote_all.call_args.kwargs["postcode"], "ZZ9 9ZZ")

    def test_an_explicit_postcode_is_used_as_given(self) -> None:
        with (
            patch("oilwatch.mcp_server.load_contact", return_value=Contact(postcode="ZZ9 9ZZ")),
            patch.object(mcp_server._app, "quote_all", return_value=[]) as quote_all,
        ):
            mcp_server.refresh_prices("AB1 1AA")

        self.assertEqual(quote_all.call_args.kwargs["postcode"], "AB1 1AA")


if __name__ == "__main__":
    unittest.main()
