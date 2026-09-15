"""
Unit tests for the pure-logic pieces of the app — the trust/navigation
policy, the telemetry-filter matcher, the screen-share label rule, the
startup command, and unread-title parsing. No Qt/WebEngine needed.

Run with:
    python -m unittest discover -s tests
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trusted_origins import (
    classify_navigation,
    classify_new_window,
    is_trusted_permission_origin,
    is_externally_openable,
    NAV_IN_APP,
    NAV_REWRITE_TO_MESSAGES,
    NAV_EXTERNAL,
    NAV_DROP,
)
from request_filter import should_block

# Mirrors messenger_app.UNREAD_TITLE_RE without importing the Qt-heavy module.
UNREAD_TITLE_RE = re.compile(r"^\((\d+)\)")


class TestNavigationPolicy(unittest.TestCase):
    def test_messenger_com_stays_in_app(self):
        for host in ("messenger.com", "www.messenger.com"):
            self.assertEqual(classify_navigation("https", host, "/"), NAV_IN_APP)
            self.assertEqual(classify_navigation("https", host, "/t/123"), NAV_IN_APP)

    def test_facebook_messages_surface_stays_in_app(self):
        # The whole point: the chat now lives on facebook.com/messages and
        # must NOT be externalised.
        for path in ("/messages", "/messages/", "/messages/t/999", "/e2ee/t/1"):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", path), NAV_IN_APP
            )

    def test_facebook_messages_boundary_not_overmatched(self):
        # "/messagesomething" is NOT the messages surface.
        self.assertEqual(
            classify_navigation("https", "www.facebook.com", "/messagesX"), NAV_EXTERNAL
        )

    def test_facebook_auth_paths_stay_in_app(self):
        # Login/checkpoint must stay in-app or sign-in breaks (an external
        # browser wouldn't share this app's session cookies). Matched on a
        # segment boundary now.
        for path in ("/login", "/login/", "/checkpoint/?next=x",
                     "/two_factor", "/recover/initiate", "/privacy/policy",
                     "/oauth/authorize", "/dialog/oauth"):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", path), NAV_IN_APP
            )

    def test_loose_auth_probes_are_now_external(self):
        # Regression for the reviewer's finding: loose prefix matching used
        # to let all of these through as IN_APP. Boundary matching rejects
        # them. (/login.php-style real endpoints, if any, are added to
        # config after the dev log shows them.)
        for path in ("/login.evil", "/settings-malicious", "/privacy-invasive",
                     "/helpful", "/oauth-malicious", "/login.php"):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", path), NAV_EXTERNAL
            )

    def test_help_path_now_opens_externally(self):
        # /help was dropped from the in-app allowlist (not an auth flow).
        self.assertEqual(
            classify_navigation("https", "www.facebook.com", "/help"), NAV_EXTERNAL
        )

    def test_bare_facebook_feed_is_rewritten_to_messages(self):
        self.assertEqual(
            classify_navigation("https", "www.facebook.com", "/"),
            NAV_REWRITE_TO_MESSAGES,
        )
        self.assertEqual(
            classify_navigation("https", "www.facebook.com", ""),
            NAV_REWRITE_TO_MESSAGES,
        )

    def test_facebook_content_goes_external(self):
        # Watch, Reels, a shared post, a profile, Marketplace, a video —
        # all leave the app for the real browser.
        for path in ("/watch", "/reel/123", "/marketplace/", "/groups/x",
                     "/stories/1", "/some.person", "/photo/?fbid=1",
                     "/permalink.php", "/videos/123"):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", path), NAV_EXTERNAL
            )

    def test_offsite_links_go_external(self):
        self.assertEqual(
            classify_navigation("https", "example.com", "/article"), NAV_EXTERNAL
        )

    def test_plain_http_facebook_is_not_loaded_in_app(self):
        # Wrong scheme must not inherit trust; http is OS-openable so it's
        # handed out rather than loaded in-app.
        self.assertEqual(
            classify_navigation("http", "www.facebook.com", "/messages"), NAV_EXTERNAL
        )

    def test_lookalike_hosts_not_in_app(self):
        for host in ("notfacebook.com", "facebook.com.evil.net", "evil.com"):
            self.assertEqual(
                classify_navigation("https", host, "/messages"), NAV_EXTERNAL
            )

    def test_internal_scheme_empty_host_allowed_as_top_level(self):
        # about:blank as a real main-frame load is fine.
        self.assertEqual(classify_navigation("about", "", "/"), NAV_IN_APP)
        self.assertEqual(classify_navigation("qrc", "", ""), NAV_IN_APP)

    def test_non_openable_scheme_empty_host_dropped(self):
        self.assertEqual(classify_navigation("data", "", ""), NAV_DROP)
        self.assertEqual(classify_navigation("javascript", "", ""), NAV_DROP)

    def test_case_insensitive_host(self):
        self.assertEqual(
            classify_navigation("https", "WWW.Facebook.COM", "/messages"), NAV_IN_APP
        )

    def test_arbitrary_subdomains_are_now_external(self):
        # Regression for the reviewer's HIGH finding: navigation used a
        # subdomain suffix match, so evil.facebook.com/messages and
        # attacker.messenger.com/anything were IN_APP. Now navigation uses
        # an exact-host allowlist — an unlisted subdomain is externalised.
        self.assertEqual(
            classify_navigation("https", "evil.facebook.com", "/messages"), NAV_EXTERNAL
        )
        self.assertEqual(
            classify_navigation("https", "attacker.messenger.com", "/anything"), NAV_EXTERNAL
        )
        self.assertEqual(
            classify_navigation("https", "m.facebook.com", "/messages"), NAV_EXTERNAL
        )

    def test_root_or_empty_prefix_cannot_collapse_boundary(self):
        # Defence in depth for the config finding: even if a "/" or ""
        # prefix reaches the classifier directly, it must not turn every
        # path into IN_APP.
        for bad in (("/",), ("",)):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", "/marketplace",
                                    None, bad, ()),
                NAV_EXTERNAL,
            )
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", "/watch",
                                    None, ("/messages",), bad),
                NAV_EXTERNAL,
            )


class TestNewWindowPolicy(unittest.TestCase):
    def test_about_blank_popup_is_dropped_not_routed_to_main_page(self):
        # Regression: reusing the nav policy let a window.open() about:blank
        # popup load into the main page and blank out the chat. New-window
        # policy must DROP internal-scheme popups.
        self.assertEqual(classify_new_window("about", "", "/"), NAV_DROP)
        self.assertEqual(classify_new_window("qrc", "", ""), NAV_DROP)

    def test_new_window_otherwise_matches_navigation(self):
        self.assertEqual(
            classify_new_window("https", "www.facebook.com", "/messages"), NAV_IN_APP
        )
        self.assertEqual(
            classify_new_window("https", "example.com", "/x"), NAV_EXTERNAL
        )


class TestNavigationPort(unittest.TestCase):
    def test_default_ports_stay_in_app(self):
        for port in (None, -1, 443):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", "/messages", port),
                NAV_IN_APP,
            )

    def test_non_default_ports_are_externalised(self):
        # Regression for the reviewer's finding: navigation used to ignore
        # the port, so :4443 was treated like ordinary 443.
        for port in (4443, 8443, 8080):
            self.assertEqual(
                classify_navigation("https", "www.facebook.com", "/messages", port),
                NAV_EXTERNAL,
            )

    def test_new_window_also_honours_port(self):
        self.assertEqual(
            classify_new_window("https", "www.facebook.com", "/messages", 4443),
            NAV_EXTERNAL,
        )


class TestPermissionTrust(unittest.TestCase):
    def test_explicit_allowed_hosts_are_trusted(self):
        for host in ("messenger.com", "www.messenger.com", "facebook.com",
                     "www.facebook.com", "web.facebook.com"):
            self.assertTrue(is_trusted_permission_origin("https", host, -1))

    def test_explicit_default_port_443_is_trusted(self):
        self.assertTrue(is_trusted_permission_origin("https", "www.facebook.com", 443))

    def test_non_default_port_is_not_trusted(self):
        self.assertFalse(is_trusted_permission_origin("https", "www.facebook.com", 4443))

    def test_http_is_not_trusted(self):
        self.assertFalse(is_trusted_permission_origin("http", "www.facebook.com", -1))

    def test_empty_host_is_never_trusted_for_permissions(self):
        self.assertFalse(is_trusted_permission_origin("about", "", -1))
        self.assertFalse(is_trusted_permission_origin("data", "", -1))

    def test_subdomain_wildcard_no_longer_applies_to_permissions(self):
        # Regression for the reviewer's finding: permissions used a
        # subdomain suffix match, so any *.facebook.com could hold a
        # capability. Now it's an exact-host allowlist — an unlisted
        # subdomain is denied even though navigation may tolerate it.
        for host in ("foo.facebook.com", "m.facebook.com",
                     "scontent.fbcdn.net", "media.fbsbx.com"):
            self.assertFalse(is_trusted_permission_origin("https", host, -1))


class TestExternallyOpenable(unittest.TestCase):
    def test_http_https_mailto_are_openable(self):
        for scheme in ("http", "https", "mailto"):
            self.assertTrue(is_externally_openable(scheme))

    def test_arbitrary_custom_schemes_are_not_openable(self):
        for scheme in ("file", "data", "javascript", "ms-word", "steam"):
            self.assertFalse(is_externally_openable(scheme))


class TestTelemetryMatcher(unittest.TestCase):
    def test_blocks_configured_telemetry(self):
        self.assertTrue(should_block("www.facebook.com", "/ajax/bz"))
        self.assertTrue(should_block("facebook.com", "/tr/"))
        self.assertTrue(should_block("web.facebook.com", "/tr?ev=1"))

    def test_does_not_block_messaging_paths(self):
        # Must never block the messaging surface or GraphQL API.
        self.assertFalse(should_block("www.facebook.com", "/messages"))
        self.assertFalse(should_block("www.facebook.com", "/api/graphql/"))
        self.assertFalse(should_block("edge-chat.facebook.com", "/"))

    def test_does_not_block_unrelated_hosts(self):
        self.assertFalse(should_block("example.com", "/ajax/bz"))

    def test_empty_host_rule_matches_any_host(self):
        self.assertTrue(should_block("anything.test", "/x", rules=(("", "/x"),)))


class TestConfigLoader(unittest.TestCase):
    import tempfile

    def _write(self, text):
        import tempfile
        d = tempfile.mkdtemp()
        p = Path(d) / "config.json"
        p.write_text(text, encoding="utf-8")
        return p

    def test_missing_file_falls_back_to_defaults(self):
        import app_config
        cfg = app_config.load_config(Path("/no/such/config.json"))
        msg, auth = app_config.navigation_prefixes(cfg)
        self.assertIn("/messages", msg)
        self.assertIn("/login", auth)

    def test_malformed_json_falls_back_to_defaults(self):
        import app_config
        p = self._write("{ this is not valid json ")
        cfg = app_config.load_config(p)
        enabled, log, rules = app_config.telemetry_settings(cfg)
        self.assertFalse(enabled)          # default posture is off
        self.assertTrue(len(rules) > 0)    # but the default rules are present

    def test_note_keys_are_ignored(self):
        import app_config
        p = self._write('{"navigation": {"_note": "hi", '
                        '"facebook_messaging_prefixes": ["/only"]}}')
        cfg = app_config.load_config(p)
        msg, auth = app_config.navigation_prefixes(cfg)
        self.assertEqual(msg, ("/only",))
        # untouched section keeps defaults
        self.assertIn("/login", auth)

    def test_bad_field_type_falls_back_per_field(self):
        import app_config
        p = self._write('{"chrome_filter": {"hide_selectors": "not-a-list", '
                        '"pause_autoplay": false}}')
        cfg = app_config.load_config(p)
        selectors, pause = app_config.chrome_settings(cfg)
        self.assertTrue(len(selectors) > 0)   # fell back to defaults
        self.assertFalse(pause)               # valid bool honoured

    def test_block_rules_normalised_to_tuples(self):
        import app_config
        p = self._write('{"telemetry_filter": {"block_rules": '
                        '[{"host": "x.com", "path": "/y"}, {"nope": 1}]}}')
        cfg = app_config.load_config(p)
        _, _, rules = app_config.telemetry_settings(cfg)
        self.assertIn(("x.com", "/y"), rules)
        # the malformed rule (no path) is dropped
        self.assertTrue(all(len(r) == 2 for r in rules))

    def test_telemetry_ships_disabled_by_default(self):
        import app_config
        cfg = app_config.load_config(Path("/no/such/config.json"))
        enabled, _, _ = app_config.telemetry_settings(cfg)
        self.assertFalse(enabled)

    def test_dev_mode_scalar_loads_and_falls_back(self):
        import app_config
        # explicit false honoured
        p = self._write('{"dev_mode": false}')
        self.assertFalse(app_config.dev_mode_enabled(app_config.load_config(p)))
        # wrong type -> default (True)
        p2 = self._write('{"dev_mode": "yes"}')
        self.assertTrue(app_config.dev_mode_enabled(app_config.load_config(p2)))


class TestCssFromSelectors(unittest.TestCase):
    def test_builds_display_none_rules(self):
        from chrome_filter import css_from_selectors
        css = css_from_selectors(['[role="banner"]', ".x"])
        self.assertIn('[role="banner"] { display: none !important; }', css)
        self.assertIn('.x { display: none !important; }', css)

    def test_empty_list_is_empty_css(self):
        from chrome_filter import css_from_selectors
        self.assertEqual(css_from_selectors([]), "")


class TestChromeInjectionGate(unittest.TestCase):
    def test_injection_self_gates_to_messages_surface(self):
        from chrome_filter import build_injection_js, css_from_selectors
        js = build_injection_js(css_from_selectors(['.x']), True,
                                ("/messages", "/e2ee"))
        # The script must bail unless on a messaging surface, so login/
        # settings pages are never touched.
        self.assertIn("onMessages()", js)
        self.assertIn("if (!onMessages()) return;", js)
        self.assertIn("facebook.com", js)

    def test_autoplay_tamer_is_gesture_aware(self):
        from chrome_filter import build_injection_js
        js = build_injection_js("", True)
        # Must track a user gesture and only pause when none is recent —
        # i.e. it does NOT pause user-initiated playback.
        self.assertIn("lastGesture", js)
        self.assertIn("Date.now() - lastGesture", js)

    def test_autoplay_tamer_omitted_when_disabled(self):
        from chrome_filter import build_injection_js
        self.assertNotIn("lastGesture", build_injection_js("", False))


try:
    import PyQt6  # noqa: F401
    _HAVE_QT = True
except Exception:
    _HAVE_QT = False


@unittest.skipUnless(_HAVE_QT, "PyQt6 not installed in this environment")
class TestMediaConsentKeying(unittest.TestCase):
    """Regression for the reviewer's HIGH finding: consent used one global
    boolean, so approving the mic auto-approved the camera and other
    origins. The key must distinguish origin AND capability."""

    def test_key_distinguishes_capability_and_origin(self):
        import messenger_app as m
        from PyQt6.QtWebEngineCore import QWebEnginePermission as P
        mic = P.PermissionType.MediaAudioCapture
        cam = P.PermissionType.MediaVideoCapture
        k_mic = m.media_permission_key("https", "www.facebook.com", -1, mic)
        k_cam = m.media_permission_key("https", "www.facebook.com", -1, cam)
        k_other = m.media_permission_key("https", "web.facebook.com", -1, mic)
        self.assertNotEqual(k_mic, k_cam)     # mic approval != camera
        self.assertNotEqual(k_mic, k_other)   # different origin distinct

    def test_capability_label_names_the_actual_capability(self):
        import messenger_app as m
        from PyQt6.QtWebEngineCore import QWebEnginePermission as P
        self.assertEqual(
            m.media_capability_label(P.PermissionType.MediaAudioCapture), "microphone")
        self.assertEqual(
            m.media_capability_label(P.PermissionType.MediaVideoCapture), "camera")
        self.assertEqual(
            m.media_capability_label(P.PermissionType.MediaAudioVideoCapture),
            "microphone and camera")


class TestClassifyWithCustomPrefixes(unittest.TestCase):
    def test_custom_messaging_prefix_is_honoured(self):
        # A path that's external by default becomes in-app when configured.
        self.assertEqual(
            classify_navigation("https", "www.facebook.com", "/inbox",
                                messaging_prefixes=("/inbox",),
                                auth_prefixes=()),
            NAV_IN_APP,
        )

    def test_removing_default_prefix_externalises_messages(self):
        # If a config omitted /messages, it would no longer stay in-app —
        # proves the prefixes actually drive the decision.
        self.assertEqual(
            classify_navigation("https", "www.facebook.com", "/messages",
                                messaging_prefixes=("/somethingelse",),
                                auth_prefixes=()),
            NAV_EXTERNAL,
        )


class TestExternalLaunchIntent(unittest.TestCase):
    def test_user_driven_nav_may_launch(self):
        from trusted_origins import external_launch_allowed
        self.assertTrue(external_launch_allowed(True, None))
        self.assertTrue(external_launch_allowed(True, 999999))

    def test_recent_user_action_authorises_a_redirect(self):
        from trusted_origins import external_launch_allowed
        # redirect (not user-driven) 500ms after a user nav -> allowed
        self.assertTrue(external_launch_allowed(False, 500, window_ms=3000))

    def test_spontaneous_redirect_is_suppressed(self):
        from trusted_origins import external_launch_allowed
        # no user nav yet, or one long ago -> not allowed
        self.assertFalse(external_launch_allowed(False, None))
        self.assertFalse(external_launch_allowed(False, 10000, window_ms=3000))


class TestPrefixValidation(unittest.TestCase):
    def _cfg(self, messaging=None, auth=None):
        cfg = {"navigation": {}}
        if messaging is not None:
            cfg["navigation"]["facebook_messaging_prefixes"] = messaging
        if auth is not None:
            cfg["navigation"]["facebook_auth_prefixes"] = auth
        return cfg

    def test_root_and_empty_prefixes_are_rejected_fall_back_to_defaults(self):
        import app_config
        msg, _ = app_config.navigation_prefixes(self._cfg(messaging=["/"]))
        self.assertNotIn("/", msg)
        self.assertIn("/messages", msg)          # fell back to defaults
        msg2, _ = app_config.navigation_prefixes(self._cfg(messaging=[""]))
        self.assertIn("/messages", msg2)

    def test_non_absolute_and_whitespace_rejected(self):
        import app_config
        msg, _ = app_config.navigation_prefixes(
            self._cfg(messaging=["messages", "  ", "/valid"]))
        self.assertIn("/valid", msg)
        self.assertNotIn("messages", msg)
        self.assertNotIn("  ", msg)

    def test_valid_custom_prefix_is_kept(self):
        import app_config
        msg, _ = app_config.navigation_prefixes(self._cfg(messaging=["/inbox"]))
        self.assertEqual(msg, ("/inbox",))

    def test_partial_invalid_list_keeps_valid_entries(self):
        import app_config
        _, auth = app_config.navigation_prefixes(self._cfg(auth=["/login", "/", ""]))
        self.assertIn("/login", auth)
        self.assertNotIn("/", auth)


class TestSpellcheckConfig(unittest.TestCase):
    def test_available_languages_detects_bdic(self):
        import tempfile, app_config
        d = tempfile.mkdtemp()
        (Path(d) / "en-US-3-0.bdic").write_text("x")
        avail = app_config.available_spellcheck_languages(
            ["en-US", "fr-FR"], [d, "/nonexistent"])
        self.assertEqual(avail, ["en-US"])   # fr-FR has no .bdic

    def test_no_dictionaries_means_empty(self):
        import app_config
        self.assertEqual(
            app_config.available_spellcheck_languages(["en-US"], ["/nope"]), [])

    def test_settings_defaults_and_bad_path(self):
        import app_config
        cfg = app_config.load_config(Path("/no/such/config.json"))
        langs, path = app_config.spellcheck_settings(cfg)
        self.assertIn("en-US", langs)
        self.assertIsNone(path)


class TestHotkeyConfig(unittest.TestCase):
    def _cfg(self, **hk):
        return {"hotkey": hk}

    def test_default_is_ctrl_alt_m_with_norepeat(self):
        import app_config
        enabled, mask, key = app_config.hotkey_settings(app_config.DEFAULTS)
        self.assertTrue(enabled)
        self.assertEqual(key, "M")
        self.assertEqual(mask, 0x4000 | 0x0002 | 0x0001)  # NOREPEAT|CTRL|ALT

    def test_custom_modifiers_and_key(self):
        import app_config
        _, mask, key = app_config.hotkey_settings(
            self._cfg(modifiers=["shift", "win"], key="k"))
        self.assertEqual(key, "K")
        self.assertEqual(mask, 0x4000 | 0x0004 | 0x0008)  # NOREPEAT|SHIFT|WIN

    def test_unknown_modifier_is_skipped(self):
        import app_config
        _, mask, _ = app_config.hotkey_settings(
            self._cfg(modifiers=["ctrl", "bogus"], key="M"))
        self.assertEqual(mask, 0x4000 | 0x0002)  # NOREPEAT|CTRL only

    def test_empty_modifiers_fall_back_to_ctrl_alt(self):
        import app_config
        _, mask, _ = app_config.hotkey_settings(self._cfg(modifiers=[], key="M"))
        self.assertEqual(mask, 0x4000 | 0x0002 | 0x0001)

    def test_bad_key_falls_back_to_default(self):
        import app_config
        _, _, key = app_config.hotkey_settings(self._cfg(key="Ctrl"))  # not 1 char
        self.assertEqual(key, "M")

    def test_disabled(self):
        import app_config
        enabled, _, _ = app_config.hotkey_settings(self._cfg(enabled=False))
        self.assertFalse(enabled)


class TestDesktopMediaLabelDisambiguation(unittest.TestCase):
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
