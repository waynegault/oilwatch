"""The fuel-mail judgement: a probability, or nothing, and never a crash.

``quote_judge`` asks whether an unrecognised sender's mail reads like a fuel
quote. The sweep it feeds is unattended and hourly, so the contract that carries
the weight is the failure one: every way the judgement can go wrong returns
``None``, and ``None`` means "not judged" rather than "not fuel" - the caller
keeps the behaviour it had.

That fail-soft shape is also why the *request* is asserted here rather than
trusted to a live call. A wrong field is answered by the API with a 422, the
module turns any failure into ``None``, and the judgement then goes missing
without a symptom - which is exactly how the ``criteria`` string form was found
(see the module docstring). Nothing but an assertion catches that class.

No test here reaches the network: a ``MockTransport`` is handed in through the
``client`` parameter, and the cases that must not make a request at all say so.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

import httpx

from oilwatch import quote_judge


def _client(responder) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(responder))


def _must_not_be_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request should have been made, got {request.url}")


def _answer(probability: object) -> httpx.Response:
    """A well-formed 200. It carries a request because raise_for_status needs one.

    A bare httpx.Response raises RuntimeError from raise_for_status, which the
    fail-soft path would turn into a silent None - the tests came back green and
    meaningless until this was attached.
    """
    return httpx.Response(
        200,
        json={"answers": {quote_judge.QUESTION_ID: {"noul": probability}}},
        request=httpx.Request("POST", quote_judge.API_URL),
    )


class _KeyedTestCase(unittest.TestCase):
    """A test that expects the judgement to be attempted, with a key to hand."""

    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {quote_judge.ENV_VAR: "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)


class RequestShapeTests(_KeyedTestCase):
    """What goes out. Asserted because a wrong field fails silently, not loudly."""

    def setUp(self) -> None:
        super().setUp()
        self.sent: dict = {}

        def responder(request: httpx.Request) -> httpx.Response:
            self.sent["json"] = json.loads(request.content)
            self.sent["authorization"] = request.headers.get("Authorization")
            return _answer(0.9)

        self.client = _client(responder)
        self.addCleanup(self.client.close)

    def test_the_question_is_the_noul_object_the_api_requires(self) -> None:
        """A string ``criteria`` is a 422 the fail-soft path turns into None."""
        quote_judge.fuel_mail_probability("a message", client=self.client)

        question = self.sent["json"]["questions"][quote_judge.QUESTION_ID]
        self.assertEqual(question["type"], "noul")
        self.assertIsInstance(question["criteria"], dict)
        self.assertEqual(set(question["criteria"]), {"true", "false"})
        self.assertEqual(self.sent["json"]["model"], quote_judge.MODEL)

    def test_the_key_travels_as_a_bearer_token(self) -> None:
        quote_judge.fuel_mail_probability("a message", client=self.client)
        self.assertEqual(self.sent["authorization"], "Bearer test-key")

    def test_only_the_opening_of_a_message_is_judged(self) -> None:
        """What the mail *is* is legible from its opening; the rest is footer.

        TypeSafe bills input tokens, so this bound is also what caps the cost.
        """
        quote_judge.fuel_mail_probability("x" * (quote_judge.STATE_CHARS + 500), client=self.client)
        self.assertEqual(len(self.sent["json"]["state"]), quote_judge.STATE_CHARS)

    def test_a_blank_message_is_not_worth_a_request(self) -> None:
        for text in ("", "   \n\t "):
            with self.subTest(text=text):
                client = _client(_must_not_be_called)
                self.addCleanup(client.close)
                self.assertIsNone(quote_judge.fuel_mail_probability(text, client=client))

    def test_without_a_client_it_posts_its_own_short_request(self) -> None:
        """The path the sweep actually takes, because it injects no client.

        The injected-client tests never enter this branch, and a broken call here
        would surface as "no judgement available" rather than as a failure - the
        alert retiring quietly. The timeout is pinned with it: this request rides
        on an hourly sweep, so waiting on it is worse than going without it.
        """
        with patch("httpx.post", return_value=_answer(0.9)) as post:
            self.assertEqual(quote_judge.fuel_mail_probability("a message"), 0.9)

        self.assertEqual(post.call_args.args[0], quote_judge.API_URL)
        self.assertEqual(post.call_args.kwargs["timeout"], quote_judge.DEFAULT_TIMEOUT)


class KeyTests(unittest.TestCase):
    """Where the key is found, and what "no key" looks like."""

    def test_the_process_environment_wins_and_the_windows_store_is_the_fallback(self) -> None:
        """A key set for the process is the one used; the registry is not consulted.

        The order matters because the two can disagree: a process started through
        WSL interop or before the key was set has the stale or absent one.
        """
        with (
            patch.dict(os.environ, {quote_judge.ENV_VAR: "from-process"}),
            patch.object(quote_judge, "_key_from_windows_environment") as stored,
        ):
            self.assertEqual(quote_judge._api_key(), "from-process")
            stored.assert_not_called()

        with (
            patch.dict(os.environ, {quote_judge.ENV_VAR: ""}),
            patch.object(quote_judge, "_key_from_windows_environment", return_value="stored"),
        ):
            self.assertEqual(quote_judge._api_key(), "stored")

    def test_no_key_anywhere_means_no_judgement_and_no_request(self) -> None:
        with (
            patch.dict(os.environ, {quote_judge.ENV_VAR: ""}),
            patch.object(quote_judge, "_key_from_windows_environment", return_value=None),
        ):
            client = _client(_must_not_be_called)
            self.addCleanup(client.close)
            self.assertIsNone(quote_judge.fuel_mail_probability("a message", client=client))

    @unittest.skipUnless(sys.platform == "win32", "reads the Windows user environment")
    def test_the_windows_key_is_read_from_the_user_environment(self) -> None:
        """``HKCU\\Environment`` is where the key was put, and the hive is the point.

        Reading the machine hive or another subkey would find nothing, and finding
        nothing is indistinguishable from not having a key at all.
        """
        import winreg

        with patch("winreg.QueryValueEx", return_value=("from-registry", 1)) as query, patch(
            "winreg.OpenKey"
        ) as open_key:
            self.assertEqual(quote_judge._key_from_windows_environment(), "from-registry")

        self.assertEqual(open_key.call_args.args, (winreg.HKEY_CURRENT_USER, "Environment"))
        self.assertEqual(query.call_args.args[1], quote_judge.ENV_VAR)

    @unittest.skipUnless(sys.platform == "win32", "reads the Windows user environment")
    def test_a_missing_windows_key_is_none_rather_than_an_error(self) -> None:
        with patch("winreg.OpenKey", side_effect=OSError("no such value")):
            self.assertIsNone(quote_judge._key_from_windows_environment())


class AnswerTests(_KeyedTestCase):
    """What comes back, and every way it comes back as nothing."""

    def _judged(self, response: httpx.Response) -> float | None:
        client = _client(lambda request: response)
        self.addCleanup(client.close)
        return quote_judge.fuel_mail_probability("a message", client=client)

    def test_a_probability_comes_back_unchanged_including_zero(self) -> None:
        """0.0 is a verdict - "judged, and not believed" - and must not read as None.

        The caller thresholds it and caches it, so collapsing a real 0.0 into the
        no-judgement value would silently re-ask for it on every sweep.
        """
        for probability in (0.0, 0.93, 1.0):
            with self.subTest(probability=probability):
                self.assertEqual(self._judged(_answer(probability)), probability)

    def test_a_probability_outside_zero_to_one_is_not_a_judgement(self) -> None:
        for probability in (1.4, -0.1):
            with (
                self.subTest(probability=probability),
                self.assertLogs("oilwatch.quote_judge", level="WARNING"),
            ):
                self.assertIsNone(self._judged(_answer(probability)))

    def test_a_refused_request_is_not_a_judgement(self) -> None:
        """The status is what stops it, not a fixture that cannot be read at all.

        A response with no request attached also lands in None, so asserting on
        the status is what keeps this from passing for that reason instead.
        """
        with self.assertLogs("oilwatch.quote_judge", level="WARNING") as logged:
            self.assertIsNone(self._judged(httpx.Response(422, text="criteria must be an object")))

        self.assertIn("422", "\n".join(logged.output))

    def test_a_response_this_module_cannot_read_is_not_a_judgement(self) -> None:
        """A reshaped response is not a probability, whatever the status said."""
        unreadable = {
            "no answers": httpx.Response(200, json={}),
            "no such question": httpx.Response(200, json={"answers": {"other": {"noul": 0.9}}}),
            "no noul": httpx.Response(
                200, json={"answers": {quote_judge.QUESTION_ID: {"label": "yes"}}}
            ),
            "not a number": _answer("very likely"),
            "not json at all": httpx.Response(200, text="<html>gateway</html>"),
        }
        for shape, response in unreadable.items():
            with (
                self.subTest(shape=shape),
                self.assertLogs("oilwatch.quote_judge", level="WARNING"),
            ):
                self.assertIsNone(self._judged(response))


if __name__ == "__main__":
    unittest.main()
