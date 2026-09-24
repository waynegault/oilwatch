"""The WSL helper launchers have to survive the Windows/WSL boundary.

``oil-mcp-up.sh``, ``oil-mcp-down.sh`` and ``install-wsl-helpers.sh`` are POSIX
shell scripts that live in a repo checked out on Windows and are *installed* into
WSL, which gives them one characteristic way to break: line endings. A CRLF
shebang is not a shebang, so the installed file fails to exec with
``execvpe(...) failed: No such file or directory``, which reads like a missing
file rather than a corrupt one — and it happened on 2026-09-24, when one of these
was rewritten from the Windows side.

Two different routes produce that, so two different things are pinned here: the
bytes in the worktree (what an editor or a writer without ``newline="\\n"``
leaves behind) and the ``.gitattributes`` rule (what a ``core.autocrlf=true``
checkout would otherwise convert on the way out). The third test is the shebang
itself, because the whole point of the file is that the shell can start it.

The scripts read like this rather than as a unit test because there is nothing
here to execute on Windows: they only run under a Linux shell that has Windows
interop. What is checkable is that the bytes are installable.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPERS = ("oil-mcp-up.sh", "oil-mcp-down.sh", "install-wsl-helpers.sh")
GITATTRIBUTES = ROOT / ".gitattributes"


class WslHelperScriptTests(unittest.TestCase):
    def test_no_helper_carries_a_carriage_return(self) -> None:
        """A CR anywhere breaks the installed file, not just in the shebang.

        The installers strip CRs on the way in, but the repo copy is the one a
        reader and an editor see, and ``oil-mcp-down.sh`` parses pids out of
        Windows ``netstat`` output — the one place a stray ``\\r`` would be read
        as part of a value rather than as whitespace.
        """
        for name in HELPERS:
            with self.subTest(helper=name):
                raw = (ROOT / name).read_bytes()
                self.assertNotIn(b"\r", raw, f"{name} carries a carriage return")

    def test_gitattributes_pins_shell_scripts_to_lf(self) -> None:
        """Without this rule a Windows checkout hands WSL a CRLF script.

        ``* text=auto`` with this repo's ``core.autocrlf=true`` converts to CRLF
        on checkout for any type it does not name, which is how a file that is
        LF in the index arrives in the worktree with CRLF — and the worktree copy
        is what ``install-wsl-helpers.sh`` copies.
        """
        rules = GITATTRIBUTES.read_text(encoding="utf-8")
        self.assertIn("*.sh text eol=lf", rules)

    def test_no_helper_opens_with_anything_but_a_sh_shebang(self) -> None:
        for name in HELPERS:
            with self.subTest(helper=name):
                first_line = (ROOT / name).read_bytes().split(b"\n", 1)[0]
                self.assertEqual(first_line, b"#!/bin/sh", f"{name} starts with {first_line!r}")


if __name__ == "__main__":
    unittest.main()
