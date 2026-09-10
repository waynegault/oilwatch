from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import WebDriverException

from oilwatch.browser_auth import BrowserAuth, detect_chrome_major_version


class FakeChromeOptions:
    def __init__(self) -> None:
        self.args: list[str] = []

    def add_argument(self, arg: str) -> None:
        self.args.append(arg)


class FakeUC:
    ChromeOptions = FakeChromeOptions

    def __init__(self) -> None:
        self.Chrome = MagicMock(return_value="DRIVER")


class FakeDriver:
    def __init__(
        self,
        *,
        cookies: list | None = None,
        quit_raises: bool = False,
        add_cookie_raises: bool = False,
        logout_links: list | None = None,
    ) -> None:
        self._cookies = cookies or []
        self._logout_links = logout_links or []
        self.quit_raises = quit_raises
        self.add_cookie_raises = add_cookie_raises
        self.urls: list[str] = []
        self.added: list = []
        self.scripts: list[str] = []
        self.quit_calls = 0

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

    def execute_script(self, script: str, *args) -> None:
        self.scripts.append(script)

    def find_elements(self, by: str, selector: str) -> list:
        return self._logout_links


class FakeField:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.sent: list = []
        self.clicked = 0

    def get_attribute(self, name: str):
        return self.value if name == "value" else None

    def click(self) -> None:
        self.clicked += 1

    def send_keys(self, *keys) -> None:
        for key in keys:
            if isinstance(key, str) and len(key) == 1:
                self.value += key
            self.sent.append(key)


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
        joined = " ".join(self.auth._options(headless=True).args)
        self.assertIn("--headless=new", joined)
        self.assertIn(f"--user-data-dir={self.auth.profile_dir}", joined)
        self.assertIn("AutofillServerCommunication", joined)

    def test_headful_options_have_no_headless_flag(self) -> None:
        self.assertNotIn("--headless=new", " ".join(self.auth._options(headless=False).args))

    def test_launch_resets_preferences_and_pins_the_chrome_version(self) -> None:
        with patch("oilwatch.browser_auth.detect_chrome_major_version", return_value=131):
            driver = self.auth.launch(headless=False)

        self.assertEqual(driver, "DRIVER")
        kwargs = self.uc.Chrome.call_args.kwargs
        self.assertEqual(kwargs["version_main"], 131)
        self.assertFalse(kwargs["use_subprocess"])
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

    def test_has_session_tracks_the_cookie_file(self) -> None:
        self.assertFalse(self.auth.has_session())
        self.auth.cookies_path().write_text("[]", encoding="utf-8")
        self.assertTrue(self.auth.has_session())

    def test_save_writes_the_driver_cookies(self) -> None:
        self.auth.driver = FakeDriver(cookies=[{"name": "session", "value": "abc"}])
        path = self.auth.save_cookies()
        self.assertEqual(json.loads(path.read_text()), [{"name": "session", "value": "abc"}])

    def test_save_without_a_driver_writes_nothing(self) -> None:
        self.assertFalse(self.auth.save_cookies().exists())

    def test_load_adds_every_applicable_cookie(self) -> None:
        self.auth.cookies_path().write_text(json.dumps([{"name": "a"}, {"name": "b"}]), encoding="utf-8")
        driver = FakeDriver()
        self.auth.driver = driver
        self.auth.load_cookies()
        self.assertEqual(driver.added, [{"name": "a"}, {"name": "b"}])

    def test_load_skips_a_cookie_for_another_domain(self) -> None:
        self.auth.cookies_path().write_text(json.dumps([{"name": "a"}]), encoding="utf-8")
        driver = FakeDriver(add_cookie_raises=True)
        self.auth.driver = driver
        self.auth.load_cookies()  # must not raise
        self.assertEqual(driver.added, [])

    def test_load_without_a_cookie_file_is_a_noop(self) -> None:
        self.auth.driver = FakeDriver()
        self.auth.load_cookies()

    def test_close_quits_and_clears_the_driver(self) -> None:
        driver = FakeDriver()
        self.auth.driver = driver
        self.auth.close()
        self.assertEqual(driver.quit_calls, 1)
        self.assertIsNone(self.auth.driver)

    def test_close_survives_a_dead_browser(self) -> None:
        self.auth.driver = FakeDriver(quit_raises=True)
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

    def test_sign_in_reports_missing_login_fields(self) -> None:
        with patch("oilwatch.browser_auth.time.sleep"), patch.object(BrowserAuth, "_find_first", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw")
        self.assertIn("login fields", str(ctx.exception))

    def test_sign_in_reports_a_missing_submit_button(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", side_effect=[FakeField(), FakeField(), None]),
            patch.object(BrowserAuth, "_set_field_value"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw")
        self.assertIn("Sign In button", str(ctx.exception))

    def test_sign_in_reports_a_button_that_will_not_activate(self) -> None:
        with (
            patch("oilwatch.browser_auth.time.sleep"),
            patch.object(BrowserAuth, "_find_first", return_value=FakeField()),
            patch.object(BrowserAuth, "_set_field_value"),
            patch.object(BrowserAuth, "_submit_sign_in", return_value=False),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.auth.sign_in(self.driver, self.URL, "owner@example.test", "pw")
        self.assertIn("activate", str(ctx.exception))

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
        self.assertTrue(BrowserAuth._submit_sign_in(FakeDriver(), button, MagicMock()))
        button.click.assert_called_once_with()

    def test_submit_falls_back_to_enter_then_javascript(self) -> None:
        button = MagicMock()
        button.click.side_effect = WebDriverException("not clickable")
        password = MagicMock()
        password.send_keys.side_effect = WebDriverException("no keyboard")
        driver = FakeDriver()

        self.assertTrue(BrowserAuth._submit_sign_in(driver, button, password))
        self.assertTrue(driver.scripts)  # the JS click was the last resort

    def test_set_field_value_leaves_a_correct_field_alone(self) -> None:
        field = FakeField(value="owner@example.test")
        BrowserAuth._set_field_value(FakeDriver(), field, "owner@example.test")
        self.assertEqual(field.sent, [])

    def test_set_field_value_raises_when_the_field_will_not_stick(self) -> None:
        with patch("oilwatch.browser_auth.time.sleep"):
            with self.assertRaises(RuntimeError):
                BrowserAuth._set_field_value(FakeDriver(), FakeField(value="wrong"), "owner@example.test")


if __name__ == "__main__":
    unittest.main()
