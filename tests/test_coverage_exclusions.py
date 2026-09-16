"""The precondition behind the coverage exclusion in pyproject.toml.

``[tool.coverage.report] exclude_lines`` skips every line ending in an ellipsis,
which is only sound while all of them are Protocol member bodies — declarations
whose contract pyright checks rather than the interpreter. A `...` that is really
a placeholder for behaviour would leave the measurement without saying so, so the
precondition is asserted here instead of assumed:

* every ellipsis statement in the package sits in a class deriving from Protocol;
* the configured pattern still matches those lines, so the exclusion is not dead.

The pair matters together: the first says the exclusion is *safe*, the second
says it is *in effect*.
"""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "oilwatch"

#: Named explicitly rather than by import, since a class deriving from a
#: Protocol is not itself required to be one.
PROTOCOL_BASES = {"Protocol"}


def _base_names(node: ast.ClassDef) -> set[str]:
    """The class's base names, as written (``Protocol`` or ``typing.Protocol``)."""
    names: set[str] = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _ellipsis_bodies() -> list[tuple[Path, int, bool, str]]:
    """Every `...` statement in the package: path, line, whether in a Protocol."""
    found: list[tuple[Path, int, bool, str]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        _walk(ast.parse(text, filename=str(path)), in_protocol=False, path=path, lines=lines, found=found)
    return found


def _walk(
    node: ast.AST,
    *,
    in_protocol: bool,
    path: Path,
    lines: list[str],
    found: list[tuple[Path, int, bool, str]],
) -> None:
    """Collect ellipsis statements, tracking whether a Protocol encloses them.

    Hand-rolled rather than ``ast.walk`` because the enclosing class is the whole
    question, and a walk flattens that away.
    """
    if isinstance(node, ast.ClassDef):
        in_protocol = in_protocol or bool(_base_names(node) & PROTOCOL_BASES)
    if (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and node.value.value is Ellipsis
    ):
        found.append((path, node.lineno, in_protocol, lines[node.lineno - 1].strip()))
    for child in ast.iter_child_nodes(node):
        _walk(child, in_protocol=in_protocol, path=path, lines=lines, found=found)


class EllipsisIsADeclarationTests(unittest.TestCase):
    def test_every_ellipsis_body_is_a_protocol_member(self) -> None:
        bodies = _ellipsis_bodies()
        self.assertGreater(
            len(bodies),
            0,
            "no ellipsis bodies found at all: the scan, not the package, is wrong",
        )

        stray = [f"{path.relative_to(ROOT)}:{line} {text}" for path, line, in_protocol, text in bodies if not in_protocol]
        self.assertEqual(
            stray,
            [],
            "coverage skips every line ending in an ellipsis, so an ellipsis "
            "outside a Protocol would leave the measurement silently: " + "; ".join(stray),
        )

    def test_the_configured_exclusion_matches_those_lines(self) -> None:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = config["tool"]["coverage"]["report"]["exclude_lines"]

        unmatched = [
            f"{path.relative_to(ROOT)}:{line} {text}"
            for path, line, _, text in _ellipsis_bodies()
            if not any(re.search(pattern, text) for pattern in patterns)
        ]
        self.assertEqual(
            unmatched,
            [],
            "exclude_lines no longer covers these declaration bodies, so they "
            "are back in the measurement: " + "; ".join(unmatched),
        )
