"""AccountRegistrar: the shared field-filling helper and the run orchestration."""

from __future__ import annotations

import asyncio
import contextlib
import io
import unittest
from unittest.mock import AsyncMock, patch

from oilwatch.auto_register import AccountRegistrar
from tests.fake_async_page import FakeAsyncPage, FakeElement


class FillFieldsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registrar = AccountRegistrar()

    def test_fills_matching_fields_and_counts_them(self) -> None:
        email, password = FakeElement(), FakeElement()
        page = FakeAsyncPage(elements=[("email", email), ("password", password)])

        filled = asyncio.run(
            self.registrar._fill_fields(
                page, {'input[name="email"]': "owner@example.test", 'input[name="password"]': "pw"}
            )
        )

        self.assertEqual(filled, 2)
        self.assertEqual(email.filled, ["owner@example.test"])
        self.assertEqual(password.filled, ["pw"])

    def test_skips_empty_values(self) -> None:
        filled = asyncio.run(
            self.registrar._fill_fields(FakeAsyncPage(), {'input[name="email"]': ""})
        )
        self.assertEqual(filled, 0)

    def test_unmatched_selectors_do_not_count(self) -> None:
        filled = asyncio.run(
            self.registrar._fill_fields(FakeAsyncPage(), {'input[name="email"]': "a@b.c"})
        )
        self.assertEqual(filled, 0)


class RunAllSuppliersTests(unittest.TestCase):
    def test_collects_each_registration_result_in_order(self) -> None:
        registrar = AccountRegistrar()
        results = [
            {"supplier": "Scottish Fuels", "status": "registered", "message": "ok"},
            {"supplier": "ValueOils", "status": "manual_review", "message": "check email"},
            {"supplier": "HomeFuels Direct", "status": "already_registered", "message": "exists"},
        ]
        fake = AsyncMock(side_effect=results)

        with (
            patch.object(registrar, "register_scottish_fuels", fake),
            patch.object(registrar, "register_valueoils", fake),
            patch.object(registrar, "register_homefuels_direct", fake),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            collected = asyncio.run(
                registrar.register_all_suppliers(
                    "Wayne", "owner@example.test", "01224 000000", "Hatton of Fintry", "AB21 0YA"
                )
            )

        self.assertEqual(collected, results)
        self.assertEqual(fake.await_count, 3)


if __name__ == "__main__":
    unittest.main()
