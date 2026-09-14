"""
Unit tests for the pure-logic pieces of the app — the ones the review
specifically called out as easy to cover and exactly where real bugs
were found (startup command, trust policies, unread-title parsing).
No Qt/WebEngine needed to run these.

Run with:
    python -m unittest discover -s tests
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trusted_origins import (
    is_trusted_navigation_target,
    is_trusted_permission_origin,
    is_externally_openable,
)

# Mirrors messenger_app.UNREAD_TITLE_RE without importing the Qt-heavy module.
UNREAD_TITLE_RE = re.compile(r"^\((\d+)\)")


class TestNavigationTrust(unittest.TestCase):
    def test_https_exact_matches_are_trusted(self):
        for host in ("messenger.com", "facebook.com"):
            self.assertTrue(is_trusted_navigation_target("https", host))

    def test_https_subdomains_are_trusted(self):
        self.assertTrue(is_trusted_navigation_target("https", "www.messenger.com"))
        self.assertTrue(is_trusted_navigation_target("https", "m.facebook.com"))

    def test_cdn_hosts_are_not_trusted_as_top_level_navigation(self):
        # fbcdn.net/fbsbx.com are fine as subresources (images, media)
        # via acceptNavigationRequest's is_main_frame check, but must
        # NOT be able to replace the whole window as a top-level page.
        self.assertFalse(is_trusted_navigation_target("https", "scontent.fbcdn.net"))
        self.assertFalse(is_trusted_navigation_target("https", "media.fbsbx.com"))

    def test_plain_http_is_not_trusted(self):
        # Same host, wrong scheme — must not inherit trust.
        self.assertFalse(is_trusted_navigation_target("http", "messenger.com"))

    def test_internal_schemes_with_empty_host_are_trusted(self):
        self.assertTrue(is_trusted_navigation_target("about", ""))
        self.assertTrue(is_trusted_navigation_target("qrc", ""))

    def test_data_scheme_with_empty_host_is_not_trusted(self):
        # data: content is not inherently trusted just because it has
        # no host to check.
        self.assertFalse(is_trusted_navigation_target("data", ""))

    def test_unrelated_domains_are_not_trusted(self):
        self.assertFalse(is_trusted_navigation_target("https", "evil.com"))

    def test_lookalike_domains_are_not_trusted(self):
        self.assertFalse(is_trusted_navigation_target("https", "notfacebook.com"))
        self.assertFalse(is_trusted_navigation_target("https", "facebook.com.evil.net"))

    def test_case_insensitive(self):
        self.assertTrue(is_trusted_navigation_target("https", "Messenger.COM"))


class TestPermissionTrust(unittest.TestCase):
    def test_https_default_port_messenger_or_facebook_is_trusted(self):
        self.assertTrue(is_trusted_permission_origin("https", "messenger.com", -1))
        self.assertTrue(is_trusted_permission_origin("https", "www.facebook.com", -1))

    def test_explicit_default_port_443_is_trusted(self):
        self.assertTrue(is_trusted_permission_origin("https", "messenger.com", 443))

    def test_non_default_port_is_not_trusted(self):
        self.assertFalse(is_trusted_permission_origin("https", "messenger.com", 4443))

    def test_http_is_not_trusted(self):
        self.assertFalse(is_trusted_permission_origin("http", "messenger.com", -1))

    def test_empty_host_is_never_trusted_for_permissions(self):
        # Unlike navigation, there is no internal-scheme exception here —
        # permission grants must fail closed on an empty/ambiguous origin.
        self.assertFalse(is_trusted_permission_origin("about", "", -1))
        self.assertFalse(is_trusted_permission_origin("data", "", -1))

    def test_cdn_hosts_are_not_trusted_for_permissions(self):
        # fbcdn.net/fbsbx.com are fine as navigation targets but must
        # NOT be able to request microphone/camera/screen-share/
        # notification access just by being a valid content host.
        self.assertFalse(is_trusted_permission_origin("https", "scontent.fbcdn.net", -1))
        self.assertFalse(is_trusted_permission_origin("https", "media.fbsbx.com", -1))


class TestExternallyOpenable(unittest.TestCase):
    def test_http_https_mailto_are_openable(self):
        for scheme in ("http", "https", "mailto"):
            self.assertTrue(is_externally_openable(scheme))

    def test_arbitrary_custom_schemes_are_not_openable(self):
        for scheme in ("file", "data", "javascript", "ms-word", "steam"):
            self.assertFalse(is_externally_openable(scheme))


class TestDesktopMediaLabelDisambiguation(unittest.TestCase):
    """The screen-share picker folds a 1-based row number into each
    option's label (e.g. "Window 1: Chrome", "Window 2: Chrome") so
    that two sources sharing an identical title are still individually
    selectable — labels.index(choice) would otherwise always resolve
    to the first match. This tests just that string-building rule in
    isolation, without needing a live Qt model."""

    @staticmethod
    def build_labels(screen_titles, window_titles):
        labels = [f"Screen {i + 1}: {t}" for i, t in enumerate(screen_titles)]
        labels += [f"Window {i + 1}: {t}" for i, t in enumerate(window_titles)]
        return labels

    def test_duplicate_window_titles_produce_unique_labels(self):
        labels = self.build_labels([], ["My Browser", "My Browser"])
        self.assertEqual(len(labels), len(set(labels)))

    def test_duplicate_screen_and_window_titles_produce_unique_labels(self):
        labels = self.build_labels(["Display", "Display"], ["Display"])
        self.assertEqual(len(labels), len(set(labels)))


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
