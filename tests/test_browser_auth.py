from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.keys import Keys

from oilwatch.browser_auth import (
    PAGE_LOAD_TIMEOUT_S,
    BrowserAuth,
    detect_chrome_major_version,
)


class FakeChromeOptions:
    def __init__(self) -> None:
        self.args: list[str] = []

    def add_argument(self, arg: str) -> None:
        self.args.append(arg)


class FakeUC:
    ChromeOptions = FakeChromeOptions

    def __init__(self) -> None:
        self.Chrome = MagicMock(return_value=MagicMock(name="driver"))


class FakeElement:
    """A login-form element, as Selenium reports it.

    ``is_displayed``/``is_enabled`` is exactly what the field search filters on,
    and a stale handle raises from both, so the outcome is modelled rather than
    assumed to be True.
    """

    def __init__(self, *, displayed: bool = True, enabled: bool = True, stale: bool = False) -> None:
        self.displayed = displayed
        self.enabled = enabled
        self.stale = stale

    def is_displayed(self) -> bool:
        if self.stale:
            raise WebDriverException("stale element reference")
        return self.displayed

    def is_enabled(self) -> bool:
        if self.stale:
            raise WebDriverException("stale element reference")
        return self.enabled


class FakeDriver:
    def __init__(
        self,
        *,
        cookies: list | None = None,
        quit_raises: bool = False,
        add_cookie_raises: bool = False,
        logout_links: list | None = None,
        elements_by_selector: dict[str, list] | None = None,
    ) -> None:
        self._cookies = cookies or []
        self._logout_links = logout_links or []
        self._elements_by_selector = elements_by_selector
        self.quit_raises = quit_raises
        self.add_cookie_raises = add_cookie_raises
        self.urls: list[str] = []
        self.current_url: str = ""
        self.title: str = ""
        self.added: list = []
        self.scripts: list[str] = []
        self.quit_calls = 0
        #: What a script reports back. The sign-in diagnostic reads its page
        #: state from one, so a test can dictate what it sees.
        self.script_result: object = None

    def get(self, url: str) -> None:
        self.urls.append(url)

    def get_cookies(self) -> list:
        return self._cookies

    def add_cookie(self, cookie) -> None:
        if self.add_cookie_raises:
            raise RuntimeError("cookie belongs to another domain")
        self.added.append(cookie)

    def quit(self) -> None:
        self.quit_calls += 1
        if self.quit_raises:
            raise RuntimeError("browser already gone")

    def execute_script(self, script: str, *args):
        self.scripts.append(script)
        if args and hasattr(args[0], "apply_dom_script"):
            args[0].apply_dom_script(script, *args[1:])
        return self.script_result

    def find_elements(self, by: str, selector: str) -> list:
        if self._elements_by_selector is not None:
            return list(self._elements_by_selector.get(selector, []))
        return self._logout_links


class FakeField:
    """A login input whose value moves only when a script says so."""

    def __init__(self, value: str = "", *, refuses: bool = False) -> None:
        self.value = value
        self.refuses = refuses
        self.sent: list = []
        self.clicked = 0
        self.cleared = 0

    def get_attribute(self, name: str):
        return self.value if name == "value" else None

    def click(self) -> None:
        self.clicked += 1

    def clear(self) -> None:
        self.cleared += 1
        if self.refuses:
            # Selenium raises on an element it cannot interact with, and a field
            # the page will not let us blank is exactly that.
            raise WebDriverException("element not interactable")
        self.value = ""

    def send_keys(self, *keys) -> None:
        for key in keys:
            if key == Keys.DELETE:
                # The Ctrl+A just before it selected everything in the field.
                self.value = ""
            elif isinstance(key, str) and key.isprintable() and not self.refuses:
                self.value += key
            self.sent.append(key)

    def apply_dom_script(self, script: str, *args) -> None:
        """Stand in for the browser running ``script`` against this element."""
        if self.refuses:
            return  # the page keeps its own value, whatever the script says
        if "arguments[0].value = ''" in script:
            self.value = ""
        elif "arguments[0].value = arguments[1]" in script:
            self.value = args[0]


class FakePageBody:
    """The page body, as Selenium reports it (``.text`` only)."""

    def __init__(self, text: str) -> None:
        self.text = text


class RecaptchaPageDriver:
    """A page whose reCAPTCHA fields hold real tokens.

    The production probe is JavaScript, so this stands in for the browser and
    answers as that script does: it reports the *lengths* of the values on the
    page, which is the contract the caller depends on. ``scripts`` keeps the
    probe script it was handed, so a test can check what was asked for.
    """

    url = "https://quote.scottishfuels.co.uk/customer/account/login/"

    def __init__(
        self,
        recaptcha_tokens: list[str] | None = None,
        form_tokens: list[str] | None = None,
    ) -> None:
        self.recaptcha_tokens = recaptcha_tokens or []
        self.form_tokens = form_tokens or []
        self.scripts: list[str] = []
        self.current_url = self.url
        self.title = "Customer Login | Scottish Fuels"

    def find_element(self, by: str, value: str) -> FakePageBody:
        return FakePageBody("CUSTOMER LOGIN\nEmail or account number\nPassword\nSign In")

    def find_elements(self, by: str, selector: str) -> list:
        return []

    def execute_script(self, script: str, *args):
        self.scripts.append(script)
        return {
            "recaptcha": [len(token) for token in self.recaptcha_tokens],
            "token": [len(token) for token in self.form_tokens],
            "forms": 1,
        }


class UnreadableDriver:
    """A browser that has already gone: every read raises.

    The sign-in diagnostic runs precisely when the browser is misbehaving, so it
    has to survive this rather than mask the failure it exists to report.
    """

    def __getattr__(self, name: str):
        raise WebDriverException("no such window: target window already closed")


class BrowserAuthTests(unittest.TestCase):
    def test_detect_chrome_major_version_returns_int_or_none(self) -> None:
        # On Windows this resolves to the installed Chrome major version.
        version = detect_chrome_major_version()
        self.assertTrue(version is None or isinstance(version, int))

    def test_profile_dir_is_namespaced(self) -> None:
        auth = BrowserAuth("scottish_fuels")
        self.assertEqual(auth.profile_dir.name, "scottish_fuels")
        self.assertEqual(auth.cookies_path().name, "cookies.json")


class ChromeVersionTests(unittest.TestCase):
    def test_non_windows_returns_none(self) -> None:
        with patch("oilwatch.browser_auth.platform.system", return_value="Linux"):
            self.assertIsNone(detect_chrome_major_version())

    def test_reads_the_major_version_from_the_registry(self) -> None:
        with (
            patch("oilwatch.browser_auth.platform.system", return_value="Windows"),
            patch("oilwatch.browser_auth.winreg.OpenKey", return_value=MagicMock()),
            patch("oilwatch.browser_auth.winreg.QueryValueEx", return_value=("131.0.6778.86", 1)),
        ):
            self.assertEqual(detect_chrome_major_version(), 131)

    def test_no_chrome_installed_returns_none(self) -> None:
        with (
            patch("oilwatch.browser_auth.platform.system", return_value="Windows"),
            patch("oilwatch.browser_auth.winreg.OpenKey", side_effect=FileNotFoundError),
        ):
            self.assertIsNone(detect_chrome_major_version())


class LaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uc = FakeUC()
        patcher = patch("oilwatch.browser_auth.uc", self.uc)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.auth = BrowserAuth("scottish_fuels", profile_root=Path(self.tmp.name))

    def test_headless_options_disable_autofill_and_pin_the_profile(self) -> None:
        # _options is typed as the real uc.ChromeOptions; this suite patches uc,
        # so what it returns is FakeChromeOptions.
        options = cast(FakeChromeOptions, self.auth._options(headless=True))
        joined = " ".join(options.args)
        self.assertIn("--headless=new", joined)
        self.assertIn(f"--user-data-dir={self.auth.profile_dir}", joined)
        self.assertIn("AutofillServerCommunication", joined)

    def test_headful_options_have_no_headless_flag(self) -> None:
        options = cast(FakeChromeOptions, self.auth._options(headless=False))
        self.assertNotIn("--headless=new", " ".join(options.args))

    def test_launch_resets_preferences_and_pins_the_chrome_version(self) -> None:
        with patch("oilwatch.browser_auth.detect_chrome_major_version", return_value=131):
            # launch() is typed as returning uc.Chrome; the fake uc is patched in
            # for this suite, so what comes back is a MagicMock.
            driver = cast(MagicMock, self.auth.launch(headless=False))

        self.assertIs(driver, self.uc.Chrome.return_value)
        kwargs = self.uc.Chrome.call_args.kwargs
        self.assertEqual(kwargs["version_main"], 131)
        # Chrome must be started with a plain subprocess. uc's multiprocessing
        # helper never reports back inside the long-running MCP server, and its
        # wait has no timeout, so the sweep used to hang for ever.
        self.assertTrue(kwargs["use_subprocess"])
        # A page load must be bounded, or a stalled third-party script blocks a
        # sign-in indefinitely.
        driver.set_page_load_timeout.assert_called_once_with(PAGE_LOAD_TIMEOUT_S)
        preferences = json.loads((self.auth.profile_dir / "Default" / "Preferences").read_text())
        self.assertTrue(preferences["profile"]["exited_cleanly"])

    def test_launch_without_a_detected_version_omits_the_pin(self) -> None:
        with patch("oilwatch.browser_auth.detect_chrome_major_version", return_value=None):
            self.auth.launch()
        self.assertNotIn("version_main", self.uc.Chrome.call_args.kwargs)


class CookieTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BrowserAuth("scottish_fuels", profile_root=Path(self.tmp.name))
        self.auth.profile_dir.mkdir(parents=True, exist_ok=True)

    def _use_driver(self, driver: FakeDriver) -> None:
        """Point the auth at a fake driver.

        ``BrowserAuth.driver`` is typed as ``uc.Chrome``; these tests drive it
        with a fake, and the flows only use the subset both provide.
        """
        self.auth.driver = driver  # type: ignore[assignment]

    def test_has_session_tracks_the_cookie_file(self) -> None:
        self.assertFalse(self.auth.has_session())
        self.auth.cookies_path().write_text("[]", encoding="utf-8")
        self.assertTrue(self.auth.has_session())

    def test_save_writes_the_driver_cookies(self) -> None:
        self._use_driver(FakeDriver(cookies=[{"name": "session", "value": "abc"}]))
        path = self.auth.save_cookies()
        self.assertEqual(json.loads(path.read_text()), [{"name": "session", "value": "abc"}])

    def test_save_without_a_driver_writes_nothing(self) -> None:
        self.assertFalse(self.auth.save_cookies().exists())

    def test_load_adds_every_applicable_cookie(self) -> None:
        self.auth.cookies_path().write_text(json.dumps([{"name": "a"}, {"name": "b"}]), encoding="utf-8")
        driver = FakeDriver()
        self._use_driver(driver)
        self.auth.load_cookies()
        self.assertEqual(driver.added, [{"name": "a"}, {"name": "b"}])

    def test_load_skips_a_cookie_for_another_domain(self) -> None:
        self.auth.cookies_path().write_text(json.dumps([{"name": "a"}]), encoding="utf-8")
        driver = FakeDriver(add_cookie_raises=True)
        self._use_driver(driver)
        self.auth.load_cookies()  # must not raise
        self.assertEqual(driver.added, [])

    def test_load_without_a_cookie_file_is_a_noop(self) -> None:
        self._use_driver(FakeDriver())
        self.auth.load_cookies()

    def test_close_quits_and_clears_the_driver(self) -> None:
        driver = FakeDriver()
        self._use_driver(driver)
        self.auth.close()
        self.assertEqual(driver.quit_calls, 1)
        self.assertIsNone(self.auth.driver)

    def test_close_survives_a_dead_browser(self) -> None:
        self._use_driver(FakeDriver(quit_raises=True))
        self.auth.close()
        self.assertIsNone(self.auth.driver)

    def test_close_without_a_driver_is_a_noop(self) -> None:
        """Callers close in a finally, so this runs even when launch never did."""
        self.auth.close()
        self.assertIsNone(self.auth.driver)


class SignInTests(unittest.TestCase):
    URL = "https://quote.scottishfuels.co.uk/customer/account/login/"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BrowserAuth("scottish_fuels", profile_root=Path(self.tmp.name))
        self.driver = FakeDriver()

    def test_sign_in_returns_the_authentication_result(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", return_value=FakeField()),
            patch.object(BrowserAuth, "_set_field_value"),
            patch.object(BrowserAuth, "_submit_sign_in", return_value=True),
            patch.object(BrowserAuth, "is_authenticated", return_value=True),
        ):
            self.assertTrue(self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw"))

        self.assertEqual(self.driver.urls, [self.URL])

    def test_sign_in_retries_when_the_first_submit_does_not_take(self) -> None:
        """One untrusted reCAPTCHA token must not cost the session: it retries."""
        outcomes = iter([False, True])
        with patch.object(
            BrowserAuth, "_attempt_sign_in", side_effect=lambda *a, **k: next(outcomes)
        ) as attempt:
            self.assertTrue(self.auth.sign_in(self.driver, self.URL, "o@e.test", "pw"))

        self.assertEqual(attempt.call_count, 2)
        self.assertIs(attempt.call_args.args[0], self.driver)

    def test_sign_in_gives_up_after_the_configured_attempts(self) -> None:
        with patch.object(BrowserAuth, "_attempt_sign_in", return_value=False) as attempt:
            self.assertFalse(self.auth.sign_in(self.driver, self.URL, "o@e.test", "pw"))

        self.assertEqual(attempt.call_count, 2)

    def test_the_authenticated_wait_returns_as_soon_as_the_marker_appears(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "is_authenticated", side_effect=[False, False, True]),
        ):
            self.assertTrue(BrowserAuth._wait_until_authenticated(self.driver, 5.0, interval=0.5))

    def test_the_authenticated_wait_gives_up_after_bounded_polls(self) -> None:
        """Bounded by a poll count, so a stubbed sleep cannot spin the timeout."""
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "is_authenticated", return_value=False) as check,
        ):
            self.assertFalse(BrowserAuth._wait_until_authenticated(self.driver, 1.0, interval=0.5))

        self.assertEqual(check.call_count, 2)  # 1.0s / 0.5s, plus the final check

    def test_sign_in_reports_missing_login_fields(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", return_value=None),
            self.assertRaises(RuntimeError) as ctx,
        ):
            self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw")
        self.assertIn("login fields", str(ctx.exception))

    def test_sign_in_reports_a_missing_submit_button(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", side_effect=[FakeField(), FakeField(), None]),
            patch.object(BrowserAuth, "_set_field_value"),
            self.assertRaises(RuntimeError) as ctx,
        ):
            self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw")
        self.assertIn("Sign In button", str(ctx.exception))

    def test_sign_in_reports_a_button_that_will_not_activate(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", return_value=FakeField()),
            patch.object(BrowserAuth, "_set_field_value"),
            patch.object(BrowserAuth, "_submit_sign_in", return_value=False),
            self.assertRaises(RuntimeError) as ctx,
        ):
            self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw")
        self.assertIn("activate", str(ctx.exception))

    def test_a_sign_in_that_takes_is_reported_as_successful(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", return_value=FakeField()),
            patch.object(BrowserAuth, "_set_field_value"),
            patch.object(BrowserAuth, "_submit_sign_in", return_value=True),
            patch.object(BrowserAuth, "_wait_until_authenticated", return_value=True),
        ):
            took = self.auth._attempt_sign_in(
                self.driver, self.URL, "owner@example.test", "pw", 0.0, 0.0
            )

        self.assertTrue(took)

    def test_a_sign_in_that_does_not_take_records_what_the_page_showed(self) -> None:
        """A bare "did not take" leaves the next fix a guess, so the page is named."""
        self.driver.current_url = self.URL
        self.driver.title = "Customer Login | Scottish Fuels"
        self.driver.script_result = {"recaptcha": [0], "token": [0], "forms": 1}

        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", return_value=FakeField()),
            patch.object(BrowserAuth, "_set_field_value"),
            patch.object(BrowserAuth, "_submit_sign_in", return_value=True),
            patch.object(BrowserAuth, "_wait_until_authenticated", return_value=False),
            self.assertLogs("oilwatch.browser_auth", level="WARNING") as captured,
        ):
            took = self.auth._attempt_sign_in(
                self.driver, self.URL, "owner@example.test", "pw", 0.0, 0.0
            )

        self.assertFalse(took)
        logged = "\n".join(captured.output)
        self.assertIn("sign-in did not take", logged)
        self.assertIn(self.URL, logged)
        self.assertIn("still_on_login_form=True", logged)
        self.assertIn("'recaptcha': [0]", logged)

    def test_the_sign_in_diagnostic_measures_tokens_instead_of_logging_them(self) -> None:
        """A reCAPTCHA token is a single-use credential, so only its length is safe."""
        token = "03AGdBq26-single-use-recaptcha-token"
        driver = RecaptchaPageDriver(recaptcha_tokens=[token], form_tokens=[""])

        with self.assertLogs("oilwatch.browser_auth", level="WARNING") as captured:
            BrowserAuth._log_sign_in_failure(driver, self.URL)

        logged = "\n".join(captured.output)
        self.assertIn(f"'recaptcha': [{len(token)}]", logged)
        self.assertNotIn(token, logged)
        self.assertIn(".length", driver.scripts[0])  # the probe measures, it does not copy
        self.assertIn("CUSTOMER LOGIN", logged)  # the page text is recorded too

    def test_the_sign_in_diagnostic_survives_a_dead_browser(self) -> None:
        """It runs when the browser is misbehaving, so it must not mask the failure."""
        with self.assertLogs("oilwatch.browser_auth", level="WARNING") as captured:
            BrowserAuth._log_sign_in_failure(UnreadableDriver(), self.URL)

        logged = "\n".join(captured.output)
        self.assertIn(self.URL, logged)
        self.assertIn("<unreadable", logged)

    def test_automated_login_saves_cookies_only_on_success(self) -> None:
        self.auth.launch = MagicMock(return_value=self.driver)
        self.auth.close = MagicMock()
        for logged_in in (True, False):
            with self.subTest(logged_in=logged_in):
                self.auth.save_cookies = MagicMock()
                with patch.object(BrowserAuth, "sign_in", return_value=logged_in):
                    self.assertEqual(self.auth.automated_login(self.URL, "owner@example.test", "pw"), logged_in)
                self.assertEqual(self.auth.save_cookies.called, logged_in)

    def test_interactive_login_saves_the_session_and_closes(self) -> None:
        self.auth.launch = MagicMock(return_value=self.driver)
        self.auth.save_cookies = MagicMock()
        self.auth.close = MagicMock()
        with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            self.auth.interactive_login(self.URL)

        self.auth.save_cookies.assert_called_once()
        self.auth.close.assert_called_once()
        self.assertEqual(self.driver.urls, [self.URL])

    def test_interactive_login_still_closes_when_cookies_cannot_be_read(self) -> None:
        self.auth.launch = MagicMock(return_value=self.driver)
        self.auth.save_cookies = MagicMock(side_effect=RuntimeError("browser closed"))
        self.auth.close = MagicMock()
        with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            self.auth.interactive_login(self.URL)
        self.auth.close.assert_called_once()


class ElementHelpersTests(unittest.TestCase):
    def test_is_authenticated_true_when_a_logout_link_exists(self) -> None:
        self.assertTrue(BrowserAuth.is_authenticated(FakeDriver(logout_links=[object()])))

    def test_is_authenticated_false_without_a_logout_link(self) -> None:
        self.assertFalse(BrowserAuth.is_authenticated(FakeDriver()))

    def test_submit_uses_a_plain_click_first(self) -> None:
        button = MagicMock()
        driver = FakeDriver()
        driver.script_result = False  # the click submitted: the page navigated

        self.assertTrue(BrowserAuth._submit_sign_in(driver, button, MagicMock()))

        button.click.assert_called_once_with()

    def test_a_click_that_submits_nothing_falls_through_to_enter(self) -> None:
        """A click that does not raise is not evidence that anything submitted."""
        button = MagicMock()
        password = MagicMock()
        driver = FakeDriver()

        with patch.object(BrowserAuth, "_page_moved_on", side_effect=[False, True]) as moved:
            self.assertTrue(BrowserAuth._submit_sign_in(driver, button, password))

        self.assertEqual(moved.call_count, 2)  # the click did nothing, so Enter was tried
        button.click.assert_called_once()
        password.send_keys.assert_called_once()

    def test_submitting_nothing_at_all_is_reported(self) -> None:
        button = MagicMock()
        password = MagicMock()
        driver = FakeDriver()

        with (
            patch.object(BrowserAuth, "_page_moved_on", return_value=False),
            self.assertLogs("oilwatch.browser_auth", level="WARNING") as captured,
        ):
            self.assertFalse(BrowserAuth._submit_sign_in(driver, button, password))

        self.assertIn("no Sign In interaction submitted", "\n".join(captured.output))

    def test_the_submit_marker_surviving_means_nothing_was_submitted(self) -> None:
        driver = FakeDriver()
        driver.script_result = True  # the marker is still on the page: no navigation

        with patch("oilwatch.browser_auth.time.sleep"):
            self.assertFalse(BrowserAuth._page_moved_on(driver))

    def test_a_navigation_clears_the_submit_marker(self) -> None:
        driver = FakeDriver()
        driver.script_result = False

        self.assertTrue(BrowserAuth._page_moved_on(driver))

    def test_a_page_torn_down_by_a_navigation_counts_as_submitted(self) -> None:
        """A navigation destroys the execution context, so a raise means it moved."""
        driver = FakeDriver()
        driver.execute_script = MagicMock(side_effect=WebDriverException("context destroyed"))

        self.assertTrue(BrowserAuth._page_moved_on(driver))

    def test_submit_falls_back_to_enter_then_javascript(self) -> None:
        button = MagicMock()
        button.click.side_effect = WebDriverException("not clickable")
        password = MagicMock()
        password.send_keys.side_effect = WebDriverException("no keyboard")
        driver = FakeDriver()

        self.assertTrue(BrowserAuth._submit_sign_in(driver, button, password))
        self.assertIn("arguments[0].click();", " ".join(driver.scripts))  # the last resort

    def test_set_field_value_leaves_a_correct_field_alone(self) -> None:
        field = FakeField(value="owner@example.test")
        driver = FakeDriver()
        BrowserAuth._set_field_value(driver, field, "owner@example.test")
        self.assertEqual(field.sent, [])
        self.assertEqual(driver.scripts, [], "an already-correct field is not touched")

    def test_set_field_value_raises_when_the_field_will_not_stick(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            self.assertRaises(RuntimeError),
        ):
            BrowserAuth._set_field_value(
                FakeDriver(), FakeField(value="wrong", refuses=True), "owner@example.test"
            )

    def test_set_field_value_survives_a_clear_that_raises(self) -> None:
        """The clear() handler must not be a NameError in disguise.

        ``_set_field_value`` catches ``WebDriverException`` from ``clear()`` and
        carries on, because the DOM assignment above it has already blanked the
        field. The name was not in scope — the import sat inside
        ``_submit_sign_in`` — so the handler raised ``NameError`` instead, and
        nothing exercised it because this fake's ``clear()`` never raised.
        """
        field = FakeField(value="stored@example.com", refuses=True)

        with (
            patch("oilwatch.browser_auth.time.sleep"),
            self.assertLogs("oilwatch.browser_auth", level="DEBUG") as captured,
            self.assertRaises(RuntimeError),
        ):
            BrowserAuth._set_field_value(FakeDriver(), field, "owner@example.test")

        self.assertTrue(
            any("WebDriver clear failed" in line for line in captured.output),
            f"a refused clear should be logged, not raised: {captured.output}",
        )

    def test_set_field_value_replaces_an_autofilled_value(self) -> None:
        """The doubled-username bug: autofill's value is replaced, not added to."""
        field = FakeField(value="stored@example.com")
        driver = FakeDriver()

        BrowserAuth._set_field_value(driver, field, "owner@example.test")

        self.assertEqual(field.value, "owner@example.test")
        self.assertEqual(field.sent, [], "nothing is typed, so autofill cannot interleave")
        self.assertIn("arguments[0].value = ''", driver.scripts[0], "blanked through the DOM")
        self.assertTrue(
            any("arguments[1]" in script for script in driver.scripts),
            "the value is assigned through the DOM, not typed",
        )

    def test_submit_falls_back_to_enter_when_the_click_is_blocked(self) -> None:
        button = MagicMock()
        button.click.side_effect = WebDriverException("element not interactable")
        password = MagicMock()
        driver = FakeDriver()

        self.assertTrue(BrowserAuth._submit_sign_in(driver, button, password))

        password.send_keys.assert_called_once()
        self.assertNotIn("arguments[0].click();", " ".join(driver.scripts))

    def test_submit_reports_failure_when_nothing_activates_the_button(self) -> None:
        button = MagicMock()
        button.click.side_effect = WebDriverException("not clickable")
        password = MagicMock()
        password.send_keys.side_effect = WebDriverException("no keyboard")
        driver = FakeDriver()
        driver.execute_script = MagicMock(side_effect=WebDriverException("no script"))

        self.assertFalse(BrowserAuth._submit_sign_in(driver, button, password))

    def test_is_authenticated_is_false_when_the_browser_is_dead(self) -> None:
        driver = FakeDriver()
        driver.find_elements = MagicMock(side_effect=WebDriverException("no such window"))

        self.assertFalse(BrowserAuth.is_authenticated(driver))


class FindFirstTests(unittest.TestCase):
    """The login-field search, which the sign-in tests patch out entirely.

    It has to wait for the form to render and pick a *visible* field: the pages
    carry more than one email-looking input (a newsletter signup, for one), and
    typing into a hidden one fails in a way that reads as autofill interference.
    """

    def _find(self, driver, *selectors):
        with patch("oilwatch.browser_auth.time.sleep"):
            return BrowserAuth._find_first(driver, selectors, timeout=0)

    def test_returns_the_first_visible_enabled_field(self) -> None:
        hidden, visible = FakeElement(displayed=False), FakeElement()
        driver = FakeDriver(elements_by_selector={"input#email": [hidden, visible]})

        self.assertIs(self._find(driver, "input#email"), visible)

    def test_a_disabled_field_is_not_used(self) -> None:
        disabled, enabled = FakeElement(enabled=False), FakeElement()
        driver = FakeDriver(elements_by_selector={"input#pass": [disabled, enabled]})

        self.assertIs(self._find(driver, "input#pass"), enabled)

    def test_a_later_selector_is_tried(self) -> None:
        newsletter, real = FakeElement(displayed=False), FakeElement()
        driver = FakeDriver(
            elements_by_selector={
                "input[type='email']": [newsletter],
                "input[name='email']": [real],
            }
        )

        self.assertIs(self._find(driver, "input[type='email']", "input[name='email']"), real)

    def test_an_element_that_will_not_answer_is_skipped(self) -> None:
        stale, live = FakeElement(stale=True), FakeElement()
        driver = FakeDriver(elements_by_selector={"input#email": [stale, live]})

        self.assertIs(self._find(driver, "input#email"), live)

    def test_gives_up_when_nothing_becomes_visible(self) -> None:
        driver = FakeDriver(elements_by_selector={"input#email": [FakeElement(displayed=False)]})

        self.assertIsNone(self._find(driver, "input#email"))

    def test_keeps_looking_until_the_form_renders(self) -> None:
        """Searching straight after driver.get() matches nothing, so it retries."""
        calls = {"n": 0}

        class SlowDriver(FakeDriver):
            def find_elements(self, by: str, selector: str) -> list:
                calls["n"] += 1
                return [] if calls["n"] < 3 else [FakeElement()]

        with patch("oilwatch.browser_auth.time.sleep"):
            found = BrowserAuth._find_first(SlowDriver(), ("input#email",), timeout=5)

        self.assertIsInstance(found, FakeElement)
        self.assertEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main()
