"""
Unit tests for the pure-logic pieces of the app — the ones the review
specifically called out as easy to cover and exactly where real bugs
were found (startup command generation, trusted-origin classification,
unread-title parsing). No Qt/WebEngine needed to run these.

Run with:
    python -m unittest discover -s tests
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trusted_origins import is_trusted_host

# Mirrors messenger_app.UNREAD_TITLE_RE without importing the Qt-heavy module.
UNREAD_TITLE_RE = re.compile(r"^\((\d+)\)")


class TestTrustedOrigins(unittest.TestCase):
    def test_exact_matches_are_trusted(self):
        for host in ("messenger.com", "facebook.com", "fbcdn.net", "fbsbx.com"):
            self.assertTrue(is_trusted_host(host))

    def test_subdomains_are_trusted(self):
        self.assertTrue(is_trusted_host("www.messenger.com"))
        self.assertTrue(is_trusted_host("static.xx.fbcdn.net"))
        self.assertTrue(is_trusted_host("m.facebook.com"))

    def test_empty_host_is_trusted(self):
        # about:blank, data:, qrc: — internal, not attacker-controlled.
        self.assertTrue(is_trusted_host(""))

    def test_unrelated_domains_are_not_trusted(self):
        self.assertFalse(is_trusted_host("evil.com"))
        self.assertFalse(is_trusted_host("google.com"))

    def test_lookalike_domains_are_not_trusted(self):
        # Must not match by substring — only real subdomains count.
        self.assertFalse(is_trusted_host("notfacebook.com"))
        self.assertFalse(is_trusted_host("facebook.com.evil.net"))
        self.assertFalse(is_trusted_host("messenger.com.attacker.io"))

    def test_case_insensitive(self):
        self.assertTrue(is_trusted_host("Messenger.COM"))


class TestUnreadTitleParsing(unittest.TestCase):
    def test_parses_leading_count(self):
        match = UNREAD_TITLE_RE.match("(3) Messenger")
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), 3)

    def test_no_count_when_absent(self):
        self.assertIsNone(UNREAD_TITLE_RE.match("Messenger"))

    def test_ignores_count_not_at_start(self):
        self.assertIsNone(UNREAD_TITLE_RE.match("Messenger (3)"))


class TestStartupCommand(unittest.TestCase):
    def test_command_points_at_entry_script_not_startup_manager(self):
        import startup_manager

        entry = Path("/fake/path/messenger_app.py")
        command = startup_manager._launch_command(entry)

        self.assertIn("messenger_app.py", command)
        self.assertNotIn("startup_manager.py", command)


if __name__ == "__main__":
    unittest.main()
