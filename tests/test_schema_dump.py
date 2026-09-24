"""The committed schema says what the code produces.

``docs/schema.sql`` exists so the shape of the database can be read — and reviewed
in a diff — without the rows, which stay local. It is generated, so the thing
worth pinning is that it still matches: a stale or hand-edited copy describes a
database that does not exist, and it would be believed, because nothing else in
the repository shows the post-migration shape.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dump_schema():
    spec = importlib.util.spec_from_file_location(
        "dump_schema", ROOT / "tools" / "dump_schema.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dump_schema = _load_dump_schema()


class CommittedSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = dump_schema.TARGET.read_text(encoding="utf-8")

    def test_the_committed_schema_is_what_the_code_produces(self) -> None:
        """A schema change that forgets to regenerate this leaves a stale file."""
        self.assertEqual(
            self.text,
            dump_schema.collect(),
            "docs/schema.sql is stale; regenerate it with python tools/dump_schema.py",
        )

    def test_it_carries_statements_and_no_rows(self) -> None:
        """A dump that started emitting data would put rows in a public file."""
        body = self.text[self.text.index("CREATE") :]
        statements = [part.strip() for part in body.split(";") if part.strip()]

        self.assertTrue(statements, "the file carries no statements")
        for statement in statements:
            with self.subTest(statement=statement[:40]):
                self.assertTrue(
                    statement.upper().startswith("CREATE"),
                    f"not a schema statement: {statement[:60]}",
                )

    def test_it_shows_the_migrated_shape_rather_than_the_first_release(self) -> None:
        """The columns that exist only because ``init_schema`` altered them in.

        This is why the file is generated from a real database: reading the
        CREATE statements in ``db.py`` alone would leave these out, and they are
        exactly what an older install has to be compared against.
        """
        for column in ("valid_until", "reason", "covers_through", "kind"):
            with self.subTest(column=column):
                self.assertIn(column, self.text)


if __name__ == "__main__":
    unittest.main()
