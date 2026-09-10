"""Undetected-browser authentication for fuel supplier portals.

Some supplier quote systems (e.g. Scottish Fuels) are protected by reCAPTCHA or
bot detection that a plain headless Playwright browser cannot pass. This module
provides a `undetected-chromedriver`-based browser with a **persistent Chrome
profile**, so the user can sign in *once* (solving any CAPTCHA/2FA by hand) and
the session is reused on subsequent automated runs.

Approach (adapted from the user's Ancestry project):
- ``uc.Chrome`` (undetected-chromedriver) handles its own anti-detection.
- ``--user-data-dir`` persists the login session across runs.
- Cookies are additionally saved/loaded to/from JSON for resilience.

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
import winreg
from pathlib import Path

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
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument(f"--user-data-dir={self.profile_dir}")
        if headless:
            options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
        return options

    def launch(self, headless: bool = False) -> uc.Chrome:
        """Launch (or reuse) the persistent undetected Chrome session."""
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # Let undetected-chromedriver auto-detect the installed Chrome version;
        # the registry value can be stale right after a Chrome auto-update.
        self.driver = uc.Chrome(
            options=self._options(headless),
            use_subprocess=False,
            suppress_welcome=True,
        )
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
            except Exception:  # noqa: BLE001 - domain/path mismatch is normal
                continue

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:  # noqa: BLE001
                pass
            self.driver = None
