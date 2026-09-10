from __future__ import annotations

import unittest

from oilwatch.browser_auth import BrowserAuth, detect_chrome_major_version


class BrowserAuthTests(unittest.TestCase):
    def test_detect_chrome_major_version_returns_int_or_none(self) -> None:
        # On Windows this resolves to the installed Chrome major version.
        version = detect_chrome_major_version()
        self.assertTrue(version is None or isinstance(version, int))

    def test_profile_dir_is_namespaced(self) -> None:
        auth = BrowserAuth("scottish_fuels")
        self.assertEqual(auth.profile_dir.name, "scottish_fuels")
        self.assertEqual(auth.cookies_path().name, "cookies.json")


if __name__ == "__main__":
    unittest.main()
