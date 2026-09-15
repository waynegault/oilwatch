"""Undetected-browser authentication for fuel supplier portals.

Some supplier quote systems (e.g. Scottish Fuels) are protected by reCAPTCHA or
bot detection that a plain headless Playwright browser cannot pass. This module
provides a `undetected-chromedriver`-based browser with a **persistent Chrome
profile**, so the user can sign in *once* (solving any CAPTCHA/2FA by hand) and
the session is reused on subsequent automated runs.

Approach (adapted from the user's Ancestry project):
- ``uc.Chrome`` (undetected-chromedriver) handles its own anti-detection.
- ``--user-data-dir`` persists the login session across runs.
- Cookies are additionally saved to JSON as a backup; the persistent profile,
  not that file, is what a later run reuses.

Usage:
    auth = BrowserAuth("scottish_fuels")
    if not auth.has_session():
        auth.interactive_login("https://quote.scottishfuels.co.uk/quote/")
    driver = auth.launch()      # already signed in
    ...                        # scrape
    auth.close()
"""

from __future__ import annotations

import json
import platform
import time
import winreg
from pathlib import Path
from typing import Any

from oilwatch.logging_setup import get_logger

log = get_logger("browser_auth")

# Login form fields, most specific first. Magento themes differ between the
# Luma theme (#email/#pass) and blank-theme forms (login[username]).
EMAIL_SELECTORS = (
    "input[name='login[username]']",
    "input#email",
    "input[name='email']",
    "input[type='email']",
)
PASSWORD_SELECTORS = (
    "input[name='login[password]']",
    "input#pass",
    "input[name='password']",
    "input[type='password']",
)
SUBMIT_SELECTORS = (
    "button#send2",
    "button[type='submit']",
    "input[type='submit']",
)

#: Seconds a single page load may take before it is treated as a failure.
#: Measured loads on this site run 2-25s; the bound exists so a stalled
#: third-party script cannot block a sign-in indefinitely.
PAGE_LOAD_TIMEOUT_S = 60

#: Planted on the page before a Sign In interaction. A navigation discards it,
#: so its survival is the only local evidence that nothing was submitted.
_SUBMIT_MARKER_FLAG = "__oilwatchSubmitMark"
_SUBMIT_MARKER_JS = f"window.{_SUBMIT_MARKER_FLAG} = true;"
#: How long a submission may take to start before it is judged not to have
#: happened. Generous on purpose: repeating a submit that was merely slow is
#: worse than waiting, so this is a ceiling, not an expectation.
SUBMIT_SETTLE_S = 8.0
SUBMIT_POLL_S = 0.25

# undetected-chromedriver imports distutils, which was removed from the stdlib
# in Python 3.12+. Importing setuptools first provides the distutils shim.
import setuptools  # noqa: F401

import undetected_chromedriver as uc


def detect_chrome_major_version() -> int | None:
    """Return the installed Chrome major version from the Windows registry."""
    if platform.system() != "Windows":
        return None
    for hive, subkey in [
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
    ]:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                version_str, _ = winreg.QueryValueEx(key, "version")
                return int(str(version_str).split(".")[0])
        except (FileNotFoundError, OSError, ValueError):
            continue
    return None


class BrowserAuth:
    """A persistent, undetected Chrome session scoped to one supplier."""

    def __init__(self, name: str, profile_root: Path | None = None) -> None:
        self.name = name
        self.profile_root = profile_root or (Path.home() / ".oilwatch" / "browser_profiles")
        self.profile_dir = self.profile_root / name
        self.driver: uc.Chrome | None = None

    def _options(self, headless: bool) -> uc.ChromeOptions:
        options = uc.ChromeOptions()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        # Autofill is actively harmful here: it pre-fills the Magento login
        # fields and re-populates them mid-typing, which appends to what we send
        # and doubles the username.
        options.add_argument(
            "--disable-features=AutofillServerCommunication,AutofillEnableAccountWalletStorage"
        )
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        options.add_argument("--profile-directory=Default")
        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
        return options

    def _reset_preferences(self) -> None:
        """Write a minimal Chrome ``Preferences`` file before launching.

        Adapted from the Ancestry project. A profile that does not record
        ``exited_cleanly`` makes Chrome offer to restore pages, and an unset
        welcome flag can open a first-run tab — either steals focus from, or
        covers, the sign-in form on a headful run.
        """
        profile = self.profile_dir / "Default"
        profile.mkdir(parents=True, exist_ok=True)
        preferences = {
            "profile": {
                "exit_type": "Normal",
                "exited_cleanly": True,
                # Chrome's own password manager refills these login fields even
                # with server-side autofill off, which is how a second copy ends
                # up appended to the value.
                "password_manager_enabled": False,
            },
            "credentials_enable_service": False,
            "browser": {"has_seen_welcome_page": True},
            "sync": {"allowed": False},
            "session": {"restore_on_startup": 4, "startup_urls": []},
        }
        (profile / "Preferences").write_text(json.dumps(preferences), encoding="utf-8")

    def launch(self, headless: bool = False) -> uc.Chrome:
        """Launch (or reuse) the persistent undetected Chrome session."""
        self._reset_preferences()
        kwargs: dict[str, object] = {
            "options": self._options(headless),
            # uc's ``use_subprocess=False`` starts Chrome through a
            # multiprocessing helper and then waits on a pipe for the pid with
            # no timeout. Inside a long-running threaded server — the MCP one —
            # that helper never reports back, so ``recv()`` blocks for ever and
            # the whole price sweep hangs with no error at all. ``True`` is uc's
            # own default and starts Chrome with a plain subprocess, so a launch
            # that fails says so instead of hanging.
            "use_subprocess": True,
            "suppress_welcome": True,
        }
        # Pin chromedriver to the installed Chrome, as Ancestry does: a mismatched
        # driver is both fragile and easier to fingerprint. Fall back to
        # auto-detection when the registry value is stale (e.g. mid auto-update).
        chrome_major = detect_chrome_major_version()
        if chrome_major is not None:
            kwargs["version_main"] = chrome_major
        self.driver = uc.Chrome(**kwargs)
        # Every step of this flow is a page load, and a third-party script that
        # never finishes would otherwise block one for as long as it likes — a
        # sign-in that stalled past 15 minutes was observed. Bounding the load
        # turns that into an error the caller already reports. Measured loads on
        # this site run 2-25s, so this is headroom rather than a budget.
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_S)
        return self.driver

    def cookies_path(self) -> Path:
        return self.profile_dir / "cookies.json"

    def has_session(self) -> bool:
        return self.cookies_path().exists()

    def interactive_login(self, url: str) -> None:
        """Open a visible browser and wait for the user to sign in manually.

        Solves reCAPTCHA/2FA by hand once; the session then persists via the
        Chrome profile (and a JSON cookie backup, when the browser is still open).
        """
        driver = self.launch(headless=False)
        driver.get(url)
        print(f"\n=== Please sign in at {url} ===")
        print("Complete any CAPTCHA / 2FA, then LEAVE THE BROWSER OPEN and come back here.")
        input("Press Enter once you are signed in (leave the browser open): ")
        try:
            self.save_cookies()
            print("Session saved (cookies + profile).")
        except Exception as exc:  # noqa: BLE001 - browser may have been closed
            print(f"Could not read cookies (browser may have been closed): {exc}")
            print("The session is still held in the persistent Chrome profile.")
        finally:
            self.close()

    def sign_in(
        self,
        driver,
        url: str,
        email: str,
        password: str,
        *,
        wait_before_submit: float = 5.0,
        wait_after_submit: float = 30.0,
        attempts: int = 2,
    ) -> bool:
        """Sign in on an already-open driver. Returns True when authenticated.

        The form is guarded by *invisible* reCAPTCHA v3: a hidden ``token`` field
        is populated by its JavaScript a moment after load. Clicking Sign In
        before that fires silently reloads the login page — indistinguishable
        from wrong credentials — so the wait below is load-bearing.

        One submit is not trusted to have worked. The token can be judged
        untrusted and the page simply reload, and a freshly established Magento
        session can take longer than a single pause to show its signed-in
        marker; either reads as a failure from here. So the attempt is made
        again on a fresh navigation (a new token each time), and the marker is
        *polled* for the whole ``wait_after_submit`` rather than checked once.

        Split out from :meth:`automated_login` so a connector that finds its
        session expired can re-use the browser it already has open: the session
        cookie here lasts only ~15 minutes, so re-authenticating mid-run is
        routine rather than exceptional.
        """
        for attempt in range(1, attempts + 1):
            if self._attempt_sign_in(
                driver, url, email, password, wait_before_submit, wait_after_submit
            ):
                return True
            if attempt < attempts:
                log.warning("sign-in attempt %d did not take; retrying once", attempt)
        log.warning("sign-in did not take after %d attempt(s)", attempts)
        return False

    def _attempt_sign_in(
        self,
        driver,
        url: str,
        email: str,
        password: str,
        wait_before_submit: float,
        wait_after_submit: float,
    ) -> bool:
        """One fill-and-submit round; True when the signed-in marker appears."""
        driver.get(url)
        # A login URL for a session that is already live redirects to the
        # account page. That both short-circuits a needless sign-in and stops a
        # retry from fighting one that in fact succeeded, just slowly.
        if self.is_authenticated(driver):
            return True

        email_field = self._find_first(driver, EMAIL_SELECTORS)
        password_field = self._find_first(driver, PASSWORD_SELECTORS)
        if email_field is None or password_field is None:
            raise RuntimeError(f"Could not find the login fields on {url}")

        # These fields arrive pre-populated by Chrome autofill, which re-fills
        # them *after* a plain clear() — so the form would submit the stored
        # username instead of ours and come back as "invalid login".
        self._set_field_value(driver, email_field, email)
        self._set_field_value(driver, password_field, password)

        time.sleep(wait_before_submit)  # let the reCAPTCHA token populate
        log.debug("reCAPTCHA state before submitting: %s", self._recaptcha_state(driver))

        submit = self._find_first(driver, SUBMIT_SELECTORS)
        if submit is None:
            raise RuntimeError(f"Could not find the Sign In button on {url}")

        # Submit like a person. reCAPTCHA v3 weighs whether the interaction was
        # a *trusted* event, and execute_script() clicks are not — the form then
        # reloads silently, indistinguishable from a missing token. A normal
        # click, then Enter in the password field, are both trusted; the JS
        # click is kept only as a last resort.
        if not self._submit_sign_in(driver, submit, password_field):
            raise RuntimeError(f"Could not activate the Sign In button on {url}")

        if self._wait_until_authenticated(driver, wait_after_submit):
            return True

        # A bare "did not take" cannot tell a rejected credential from a
        # reCAPTCHA token that never populated from a click the page ignored, so
        # the page is described instead — as a failed price parse is.
        self._log_sign_in_failure(driver, url)
        return False

    @staticmethod
    def _recaptcha_state(driver) -> dict[str, Any]:
        """What reCAPTCHA has actually put on the page — lengths, never values.

        reCAPTCHA delivers its token into a ``g-recaptcha-response`` field, so
        that is what says whether the widget has minted anything yet; an empty
        one is the whole diagnostic. Only lengths are reported: the token is a
        single-use credential.
        """
        return driver.execute_script(
            """
            const lengths = (sel) => Array.from(document.querySelectorAll(sel))
                .map(e => (e.value || '').length);
            return {
                recaptcha: lengths("textarea[name='g-recaptcha-response'], #g-recaptcha-response"),
                token: lengths("input[name='token']"),
                forms: document.querySelectorAll("form").length,
            };
            """
        )

    @classmethod
    def _log_sign_in_failure(cls, driver, url: str) -> None:
        """Record what the page actually showed when a sign-in did not take."""
        from selenium.webdriver.common.by import By

        def safe(read, default):
            try:
                return read()
            except Exception as exc:  # noqa: BLE001 - diagnostics must not hide the real error
                return f"<unreadable: {exc}>"

        body = safe(lambda: driver.find_element(By.TAG_NAME, "body").text, "") or ""
        log.warning(
            "sign-in did not take. requested=%s landed=%s title=%r "
            "still_on_login_form=%s recaptcha=%s recaptcha_widgets=%s; "
            "page text follows:\n%s",
            url,
            safe(lambda: driver.current_url, "<unreadable>"),
            safe(lambda: driver.title, "<unreadable>"),
            safe(
                lambda: cls._find_first(driver, EMAIL_SELECTORS, timeout=0) is not None,
                "<unreadable>",
            ),
            safe(lambda: cls._recaptcha_state(driver), "<unreadable>"),
            safe(
                lambda: len(
                    driver.find_elements(
                        By.CSS_SELECTOR, ".g-recaptcha, iframe[src*='recaptcha']"
                    )
                ),
                "<unreadable>",
            ),
            body[:2000],
        )

    @classmethod
    def _wait_until_authenticated(
        cls, driver, timeout: float, *, interval: float = 0.5
    ) -> bool:
        """Poll for the signed-in marker for up to ``timeout`` seconds.

        Bounded by a *count* of polls rather than the wall clock, so a caller —
        or a test — that stubs out ``sleep`` cannot turn the wait into a busy
        spin that still runs the full timeout in real time.
        """
        polls = max(1, int(timeout / interval))
        for poll in range(polls):
            if cls.is_authenticated(driver):
                return True
            if poll < polls - 1:
                time.sleep(interval)
        return False

    @classmethod
    def _submit_sign_in(cls, driver, button, password_field) -> bool:
        """Activate Sign In, and insist that something was actually submitted.

        A click that does not raise has not necessarily submitted anything: the
        page's own handler can swallow the event and leave the login form
        sitting there, with no request sent and no error to see. Trusting the
        absence of an exception therefore leaves the Enter and JavaScript-click
        fallbacks unreachable in exactly the case they exist for, so each
        interaction must be followed by the page actually moving on. The settle
        time is deliberately generous: an interaction whose submission is
        merely slow must be judged to have worked rather than repeated.
        """
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.common.keys import Keys

        interactions = (
            ("click", lambda: button.click()),
            ("Enter in the password field", lambda: password_field.send_keys(Keys.ENTER)),
            ("JavaScript click", lambda: driver.execute_script("arguments[0].click();", button)),
        )
        for name, interact in interactions:
            try:
                driver.execute_script(_SUBMIT_MARKER_JS)
                interact()
            except WebDriverException as exc:
                log.debug("%s on Sign In failed: %s", name, exc)
                continue
            if cls._page_moved_on(driver):
                return True
            log.debug("%s on Sign In did not submit the form; trying the next", name)
        log.warning("no Sign In interaction submitted the login form")
        return False

    @classmethod
    def _page_moved_on(cls, driver, *, settle_s: float = SUBMIT_SETTLE_S) -> bool:
        """Whether the page navigated since the submit marker was planted.

        A navigation tears down the page's execution context, so a script that
        raises here is evidence the page moved rather than evidence of failure.
        Bounded by a poll *count*, so a stubbed sleep cannot spin the wait.
        """
        from selenium.common.exceptions import WebDriverException

        polls = max(1, int(settle_s / SUBMIT_POLL_S))
        for poll in range(polls):
            try:
                if not driver.execute_script(f"return Boolean({_SUBMIT_MARKER_FLAG});"):
                    return True
            except WebDriverException:
                return True
            if poll < polls - 1:
                time.sleep(SUBMIT_POLL_S)
        return False

    @staticmethod
    def _set_field_value(
        driver,
        element,
        value: str,
        *,
        attempts: int = 3,
    ) -> None:
        """Put ``value`` into a field without racing Chrome autofill.

        Autofill pre-fills these Magento fields, and a typed value either lands
        nowhere or *appends* — submitting ``user@example.comuser@example.com`` and
        failing as an invalid login. So nothing is typed: as the Ancestry project
        does, the field is blanked and its ``value`` assigned through the DOM,
        then a bubbling ``input`` (and ``change``) event is dispatched so the
        page's own listeners see the edit. There is no keystroke to lose or
        duplicate, which typing could never guarantee.

        Deliberately never puts the value in an error message: one of the two
        callers is passing a password.
        """
        for _ in range(attempts):
            if (element.get_attribute("value") or "") == value:
                return  # already correct; leave the page's state alone

            driver.execute_script(
                "arguments[0].setAttribute('autocomplete', 'off');"
                "arguments[0].setAttribute('autocorrect', 'off');"
                "arguments[0].setAttribute('spellcheck', 'false');"
                "arguments[0].value = '';",
                element,
            )
            try:
                element.clear()
            except WebDriverException as exc:
                # The DOM assignment above already blanked it, so a field that
                # refuses the WebDriver clear is not fatal on its own.
                log.debug("WebDriver clear failed, DOM blank already applied: %s", exc)

            driver.execute_script(
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));"
                "arguments[0].dispatchEvent(new Event('change', {bubbles: true}));",
                element,
                value,
            )
            if (element.get_attribute("value") or "") == value:
                return
            time.sleep(0.3)
        raise RuntimeError("a login field would not accept the intended value")

    def automated_login(
        self,
        url: str,
        email: str,
        password: str,
        *,
        wait_before_submit: float = 5.0,
        wait_after_submit: float = 30.0,
        headless: bool = False,
    ) -> bool:
        """Launch a browser, sign in with stored credentials, save the session.

        Convenience wrapper around :meth:`sign_in` for one-shot use.
        """
        driver = self.launch(headless=headless)
        try:
            logged_in = self.sign_in(
                driver,
                url,
                email,
                password,
                wait_before_submit=wait_before_submit,
                wait_after_submit=wait_after_submit,
            )
            if logged_in:
                self.save_cookies()
            return logged_in
        finally:
            self.close()

    @classmethod
    def _find_first(cls, driver, selectors: tuple[str, ...], *, timeout: float = 15.0):
        """Return the first *visible* element matching any selector, waiting for it.

        Two reasons this is not a plain find_element:

        * The page carries more than one email-looking input (a newsletter
          signup, for one), and typing into a hidden one fails in a way that
          looks like autofill interference.
        * Searching immediately after ``driver.get()`` can match nothing at all,
          because the form has not rendered yet.
        """
        from selenium.common.exceptions import WebDriverException
        from selenium.webdriver.common.by import By

        deadline = time.time() + timeout
        while True:
            for selector in selectors:
                for element in driver.find_elements(By.CSS_SELECTOR, selector):
                    try:
                        if element.is_displayed() and element.is_enabled():
                            return element
                    except WebDriverException:
                        continue
            if time.time() >= deadline:
                return None
            time.sleep(0.25)

    @classmethod
    def is_authenticated(cls, driver) -> bool:
        """True when a sign-out link is present, which only exists when signed in.

        Text markers were tried first and they lie: the sign-in page itself
        contains "My Account" in its breadcrumb, so matching on page text
        reported a successful sign-in when no session had been established.
        A logout link is the unambiguous signal.
        """
        from selenium.webdriver.common.by import By

        for selector in ("a[href*='logout']", "a[href*='account/logout']"):
            try:
                if driver.find_elements(By.CSS_SELECTOR, selector):
                    return True
            except Exception:  # noqa: BLE001 - a dead browser is simply "not signed in"
                return False
        return False

    def save_cookies(self, path: Path | None = None) -> Path:
        path = path or self.cookies_path()
        if self.driver is None:
            return path
        cookies = self.driver.get_cookies()
        path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
        return path

    def load_cookies(self, path: Path | None = None) -> None:
        path = path or self.cookies_path()
        if not path.exists():
            return
        assert self.driver is not None
        for cookie in json.loads(path.read_text(encoding="utf-8")):
            try:
                self.driver.add_cookie(cookie)
            except Exception as exc:  # noqa: BLE001 - domain/path mismatch is normal
                log.debug("skipped cookie that does not apply to this domain: %s", exc)
                continue

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception as exc:  # noqa: BLE001
                log.debug("browser quit raised: %s", exc)
            self.driver = None
