"""PROGRESS.md and ROADMAP.md state numbers; this checks them against the code.

Hand-maintained counts drift, and they did: PROGRESS.md claimed 216 tests in its
summary and 88 two sections later, and two of its four mentions of the MCP tool
count said eight when the server serves nine; ROADMAP.md claimed 8 tools, 19
commands and 88 tests, and still listed the ordering platform as complete after
it had been dropped. The numbers that can be derived are read back out of the
code here, so a count that goes stale fails the suite instead of quietly
misinforming.

Deliberately not checked: counts in the narrative sections, which describe past
states and were true when written, and prose that counts something fuzzy such as
"4 generic connectors".
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "PROGRESS.md").read_text(encoding="utf-8")
ROADMAP = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")


def _decorated_tools() -> int:
    """How many functions the MCP server decorates with ``@mcp.tool``."""
    tree = ast.parse((ROOT / "oilwatch" / "mcp_server.py").read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            # ``@mcp.tool(...)`` is a call; ``@mcp.tool`` would be the attribute.
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                count += 1
    return count


def _subcommands() -> int:
    """How many subcommands the CLI parser defines."""
    tree = ast.parse((ROOT / "oilwatch" / "cli.py").read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
    )


def _discovered_tests() -> int:
    """How many tests a fresh discovery run finds, this one included."""
    loader = unittest.TestLoader()
    return loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)).countTestCases()


def _package_modules() -> int:
    """How many Python files the package holds."""
    return len(list((ROOT / "oilwatch").rglob("*.py")))


def _stated(pattern: str, *, what: str, document: str = PROGRESS) -> int:
    match = re.search(pattern, document, re.MULTILINE)
    if match is None:
        raise AssertionError(f"nothing in the document matches {what}")
    return int(match.group(1))


class CountTests(unittest.TestCase):
    def test_the_mcp_tool_count_matches_the_server(self) -> None:
        expected = _decorated_tools()
        self.assertEqual(
            _stated(r"^\| MCP tools \| (\d+)", what="the MCP tools row"), expected
        )
        self.assertEqual(
            _stated(r"FastMCP server, (\d+) tools", what="the mcp_server.py row"), expected
        )

    def test_the_cli_command_count_matches_the_parser(self) -> None:
        expected = _subcommands()
        self.assertEqual(
            _stated(r"^\| CLI commands \| (\d+)", what="the CLI commands row"), expected
        )
        self.assertEqual(
            _stated(r"CLI entry point \((\d+) commands\)", what="the cli.py row"), expected
        )

    def test_the_test_count_matches_a_discovery_run(self) -> None:
        expected = _discovered_tests()
        self.assertEqual(
            _stated(r"^\| Tests \| (\d+)", what="the Tests row"),
            expected,
            "PROGRESS.md's test count is stale; run the suite and update it",
        )
        self.assertEqual(
            _stated(r"— (\d+) tests, all offline", what="the Tests section count"),
            expected,
            "PROGRESS.md's test count is stale; run the suite and update it",
        )

    def test_the_module_count_matches_the_package(self) -> None:
        self.assertEqual(
            _stated(r"^\| Modules under `oilwatch/` \| (\d+)", what="the modules row"),
            _package_modules(),
        )


class RoadmapCountTests(unittest.TestCase):
    """ROADMAP.md states the same three counts, and drifted the same way."""

    def _roadmap(self, pattern: str, what: str) -> int:
        return _stated(pattern, what=what, document=ROADMAP)

    def test_the_mcp_tool_count_matches_the_server(self) -> None:
        self.assertEqual(
            self._roadmap(r"\| (\d+) tools over streamable HTTP", "the MCP row"),
            _decorated_tools(),
        )
        self.assertEqual(
            self._roadmap(r"mcp_server\.py` - (\d+) MCP tools exposed", "the phase 6 note"),
            _decorated_tools(),
        )

    def test_the_cli_command_count_matches_the_parser(self) -> None:
        self.assertEqual(
            self._roadmap(r"\| CLI \| ✅ Complete \| (\d+) commands", "the CLI row"),
            _subcommands(),
        )

    def test_the_test_count_matches_a_discovery_run(self) -> None:
        self.assertEqual(
            self._roadmap(r"\| Tests \| ✅ Complete \| (\d+) tests", "the Tests row"),
            _discovered_tests(),
            "ROADMAP.md's test count is stale; run the suite and update it",
        )


if __name__ == "__main__":
    unittest.main()
