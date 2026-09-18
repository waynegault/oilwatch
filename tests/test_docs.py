"""PROGRESS.md and ROADMAP.md state numbers; this checks them against the code.

Hand-maintained counts drift, and they did: PROGRESS.md claimed 216 tests in its
summary and 88 two sections later, and two of its four mentions of the MCP tool
count said eight when the server serves nine; ROADMAP.md claimed 8 tools, 19
commands and 88 tests, and still listed the ordering platform as complete after
it had been dropped. The numbers that can be derived are read back out of the
code here, so a count that goes stale fails the suite instead of quietly
misinforming.

The same applies to a pair of files rather than a number: config/settings.json
is gitignored while config/settings.example.json is shipped, so a setting added
to only one of them goes unnoticed — it either reads its dataclass default here,
or is absent from the file a new install copies. The launchers are guarded the
same way: one value written into two scripts drifts, so the unattended log path
is defined once and both scripts have to call the file that holds it.

Deliberately not checked: counts in the narrative sections, which describe past
states and were true when written, and prose that counts something fuzzy such as
"4 generic connectors".
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from oilwatch.logging_setup import LOG_FILE_ENV
from oilwatch.models import QUOTE_STATUSES

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = (ROOT / "PROGRESS.md").read_text(encoding="utf-8")
ROADMAP = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
LIVE_SETTINGS = ROOT / "config" / "settings.json"
EXAMPLE_SETTINGS = ROOT / "config" / "settings.example.json"
SHARED_ENV = ROOT / "oilwatch_env.bat"
LAUNCHERS = ("start_scheduler.bat", "monitor_email.bat")


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


def _cli_commands() -> set[str]:
    """Every subcommand name the CLI parser defines."""
    tree = ast.parse((ROOT / "oilwatch" / "cli.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_parser"):
            continue
        argument = node.args[0] if node.args else None
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            names.add(argument.value)
    return names


def _mcp_tool_names() -> set[str]:
    """Every function name the MCP server decorates with ``@mcp.tool``."""
    tree = ast.parse((ROOT / "oilwatch" / "mcp_server.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                names.add(node.name)
    return names


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


class ReferenceTests(unittest.TestCase):
    """README is the tool reference, so it has to name every tool and command.

    ``AGENTS.md`` points an agent at it for the tool reference, which only works
    if it is complete: a command that exists but is documented nowhere is worse
    than one that does not exist, because the reader concludes the app cannot do
    the thing. Seven of the twenty-one commands were missing on 2026-09-18, which
    is the same drift the count guards above exist to catch - so this reads both
    lists out of the code rather than trusting the prose.
    """

    def test_every_cli_command_is_named_in_the_readme(self) -> None:
        missing = sorted(
            name
            for name in _cli_commands()
            # In a command position, so the prose word "quote" cannot stand in
            # for the `quote` command.
            if not re.search(rf"oilwatch(?:\.cli)? {re.escape(name)}\b", README)
        )
        self.assertEqual(missing, [], f"README.md does not name these commands: {missing}")

    def test_every_mcp_tool_is_named_in_the_readme(self) -> None:
        missing = sorted(
            name for name in _mcp_tool_names() if not re.search(rf"\b{re.escape(name)}\b", README)
        )
        self.assertEqual(missing, [], f"README.md does not name these tools: {missing}")

    def test_every_cli_command_is_named_in_agents_md(self) -> None:
        """The contract lists them too, so it drifts the same way.

        AGENTS.md is what an agent reads first; a list there that has fallen
        behind the parser is the same failure as one in the README. Names must
        appear in backticks here, which is how the file writes code, so the word
        "quote" in a sentence cannot stand in for the command.
        """
        missing = sorted(name for name in _cli_commands() if f"`{name}`" not in AGENTS)
        self.assertEqual(missing, [], f"AGENTS.md does not name these commands: {missing}")

    def test_every_mcp_tool_is_named_in_agents_md(self) -> None:
        missing = sorted(name for name in _mcp_tool_names() if f"`{name}`" not in AGENTS)
        self.assertEqual(missing, [], f"AGENTS.md does not name these tools: {missing}")

    def test_every_quote_status_is_documented(self) -> None:
        """The vocabulary a consumer branches on has to be written down.

        ``QUOTE_STATUSES`` is a closed set, and the nullability is part of the
        contract — the price fields are set only for ``ok``. A consumer that has
        to guess either of those reads the wrong thing out of a row, so this
        checks the docs against the constant rather than against a list copied
        into this file.
        """
        for document, name in ((README, "README.md"), (AGENTS, "AGENTS.md")):
            missing = sorted(value for value in QUOTE_STATUSES if value not in document)
            with self.subTest(document=name):
                self.assertEqual(missing, [], f"{name} does not name these statuses: {missing}")


#: Settings objects whose keys are schema. Every other object in the file is
#: either a scalar or a map whose entries are data, and those legitimately differ
#: between an install and the shipped example (login_urls names this owner's
#: supplier, the example ships an empty map), so their entries are not compared.
_FIXED_OBJECTS = ("home", "scheduler")


def _settings_keys(document: object) -> set[str]:
    """The configurable keys a settings document offers, nested ones as ``a.b``."""
    if not isinstance(document, dict):
        return set()
    keys = set(document)
    for name in _FIXED_OBJECTS:
        nested = document.get(name)
        if isinstance(nested, dict):
            keys |= {f"{name}.{key}" for key in nested}
    return keys


@unittest.skipUnless(
    LIVE_SETTINGS.exists(),
    "config/settings.json is gitignored, so a fresh clone has nothing to compare",
)
class SettingsDriftTests(unittest.TestCase):
    """The shipped example and the live settings must offer the same keys."""

    def test_the_example_and_the_live_settings_agree_on_keys(self) -> None:
        live = _settings_keys(json.loads(LIVE_SETTINGS.read_text(encoding="utf-8")))
        example = _settings_keys(json.loads(EXAMPLE_SETTINGS.read_text(encoding="utf-8")))
        self.assertEqual(
            example - live,
            set(),
            "settings.example.json documents keys the live settings.json lacks",
        )
        self.assertEqual(
            live - example,
            set(),
            "settings.json sets keys settings.example.json does not document",
        )


class LogPathSourceTests(unittest.TestCase):
    """The unattended log path is defined once, in the file both launchers call.

    It was a literal ``set`` in each launcher until 2026-09-13: two copies of one
    value, which is the drift the settings pair above is guarded against.
    """

    def test_the_shared_env_file_is_where_the_log_path_lives(self) -> None:
        self.assertIn(
            LOG_FILE_ENV,
            SHARED_ENV.read_text(encoding="utf-8"),
            "the shared env file is where the log path is meant to be defined",
        )

    def test_a_launcher_calls_the_shared_file_rather_than_redefining_it(self) -> None:
        for name in LAUNCHERS:
            text = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(launcher=name):
                self.assertIn(SHARED_ENV.name, text, f"{name} must call the shared env file")
                for line in text.splitlines():
                    self.assertFalse(
                        line.strip().lower().startswith("set") and LOG_FILE_ENV in line,
                        f"{name} defines the log path itself; it belongs in {SHARED_ENV.name}",
                    )


if __name__ == "__main__":
    unittest.main()
