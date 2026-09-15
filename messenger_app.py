"""
Standalone Facebook Messenger desktop app.

A PyQt6 + QtWebEngine wrapper around messenger.com, giving it a native
window, system tray icon, and desktop notifications.

Requirements:
    pip install -r requirements.txt

Files:
    messenger_app.py    - this file, the main window/app
    icon_assets.py       - draws the app icon and unread badge icons
    windows_taskbar.py   - Windows taskbar overlay badge (ITaskbarList3)
    startup_manager.py   - Windows launch-at-startup toggle
    global_hotkey.py     - system-wide show/hide hotkey
    trusted_origins.py   - shared navigation/permission trust policies

Run:
    python messenger_app.py
"""

import os
import re
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt, QTimer, QPersistentModelIndex, QModelIndex
from PyQt6.QtGui import QIcon, QAction, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSystemTrayIcon,
    QMenu,
    QInputDialog,
    QMessageBox,
)
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEnginePage,
    QWebEngineSettings,
    QWebEnginePermission,
    QWebEngineScript,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from icon_assets import build_app_icon
from windows_taskbar import TaskbarBadge
from startup_manager import is_startup_enabled, set_startup_enabled
from global_hotkey import GlobalHotkey
from trusted_origins import (
    classify_navigation,
    classify_new_window,
    is_trusted_permission_origin,
    is_externally_openable,
    external_launch_allowed,
    NAV_IN_APP,
    NAV_REWRITE_TO_MESSAGES,
    NAV_EXTERNAL,
)
from request_filter import TelemetryBlocker
from chrome_filter import build_injection_js, css_from_selectors
from app_config import (
    load_config,
    navigation_prefixes,
    telemetry_settings,
    chrome_settings,
    dev_mode_enabled,
    spellcheck_settings,
    available_spellcheck_languages,
    hotkey_settings,
)

# Matches Messenger's title prefix, e.g. "(3) Messenger"
UNREAD_TITLE_RE = re.compile(r"^\((\d+)\)")

# Entry point. messenger.com still serves the login page; once you're
# authenticated Meta redirects desktop web messaging to
# facebook.com/messages (messenger.com is being retired ~April 2026), and
# the navigation policy in trusted_origins keeps you pinned to the
# messages surface from there.
MESSENGER_URL = "https://www.messenger.com/"

# Where the feed (bare facebook.com/) gets bounced to, and the canonical
# in-app messaging destination.
FACEBOOK_MESSAGES_URL = "https://www.facebook.com/messages"

# The actual entry point, used to build a correct startup command
# regardless of which module happens to be executing.
ENTRY_SCRIPT = Path(__file__).resolve()

# Permission types we're willing to consider granting, and only ever
# to a trusted (Messenger/Facebook) origin.
GRANTABLE_PERMISSIONS = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
    QWebEnginePermission.PermissionType.DesktopVideoCapture,
    QWebEnginePermission.PermissionType.DesktopAudioVideoCapture,
    QWebEnginePermission.PermissionType.Notifications,
}

# Mic/camera get an extra native confirmation prompt beyond the origin
# check, since granting the OS-level permission silently would let any
# trusted-origin page turn them on with no human decision point at
# all. Screen share already has an equivalent gate (the source
# picker); notifications are low-stakes enough not to need one.
MEDIA_CAPTURE_PERMISSIONS = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
}

# Human-readable capability names so the consent prompt names what's
# actually being requested, instead of always saying "microphone and
# camera".
_MEDIA_CAPABILITY_LABELS = {
    QWebEnginePermission.PermissionType.MediaAudioCapture: "microphone",
    QWebEnginePermission.PermissionType.MediaVideoCapture: "camera",
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture: "microphone and camera",
}


def media_capability_label(permission_type) -> str:
    return _MEDIA_CAPABILITY_LABELS.get(permission_type, "microphone/camera")


def media_permission_key(scheme, host, port, permission_type):
    """Cache key for a media-consent decision. Keyed by full origin AND
    capability, so approving the mic on one origin does NOT silently
    approve the camera, or a different origin."""
    return ((scheme or "").lower(), (host or "").lower(), port, permission_type)


# Navigation types that represent a fresh user action (vs. a page- or
# server-driven one). Used to decide whether an EXTERNAL navigation may
# launch the system browser — see external_launch_allowed.
_USER_DRIVEN_NAV_TYPES = frozenset({
    QWebEnginePage.NavigationType.NavigationTypeLinkClicked,
    QWebEnginePage.NavigationType.NavigationTypeTyped,
    QWebEnginePage.NavigationType.NavigationTypeFormSubmitted,
    QWebEnginePage.NavigationType.NavigationTypeBackForward,
})


class MessengerPage(QWebEnginePage):
    """Enforces a message-only trust boundary on top-level navigation.

    The chat surface now lives at facebook.com/messages, right next to the
    feed/Watch/Reels/Marketplace this app exists to avoid, so a yes/no host
    check is no longer enough — trusted_origins.classify_navigation sorts
    each main-frame URL into one of four actions:

      IN_APP              stay in this window (messenger.com; the
                          facebook.com messaging + auth paths)
      REWRITE_TO_MESSAGES bare facebook.com/ (the feed) -> bounce to the
                          messages surface so the feed never renders
      EXTERNAL            hand to the system browser (other facebook.com
                          content, off-site links, and — usefully — video,
                          which plays there since this engine lacks the
                          H.264 codecs)
      DROP                ignore (unsafe scheme, hostile popup)
    """

    def __init__(self, profile, parent=None,
                 messaging_prefixes=None, auth_prefixes=None, dev_mode=False):
        super().__init__(profile, parent)
        # facebook.com path allowlists, threaded in from config.json. None
        # means "use trusted_origins' built-in defaults".
        self._messaging_prefixes = messaging_prefixes
        self._auth_prefixes = auth_prefixes
        self._dev_mode = dev_mode
        # Monotonic ms timestamp of the last user-driven main-frame
        # navigation, used to authorise a following external launch.
        self._last_user_nav_ms = None

    def _classify(self, url):
        if self._messaging_prefixes is None or self._auth_prefixes is None:
            return classify_navigation(
                url.scheme(), url.host(), url.path(), url.port())
        return classify_navigation(
            url.scheme(), url.host(), url.path(), url.port(),
            self._messaging_prefixes, self._auth_prefixes,
        )

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if not is_main_frame:
            return True  # don't gate subresources/iframes (CDN assets etc.)

        is_user_nav = nav_type in _USER_DRIVEN_NAV_TYPES
        now_ms = time.monotonic() * 1000.0
        if is_user_nav:
            self._last_user_nav_ms = now_ms

        action = self._classify(url)

        if action != NAV_IN_APP and self._dev_mode:
            # The navigation-logging the README promises. With dev_mode on,
            # every URL that DOESN'T stay in-app is printed with the action
            # taken — this is how you spot a login/auth path or host that
            # got wrongly externalised and needs adding to config/allowlist.
            print(f"[nav] {action:20} {nav_type} {url.toString()}")

        if action == NAV_IN_APP:
            return True

        if action == NAV_REWRITE_TO_MESSAGES:
            # Defer the reload: mutating the page from inside
            # acceptNavigationRequest is reentrant. singleShot(0) runs it
            # after this call returns. The messages URL re-enters here and
            # classifies as IN_APP, so there's no loop.
            QTimer.singleShot(0, lambda: self.setUrl(QUrl(FACEBOOK_MESSAGES_URL)))
            return False

        if action == NAV_EXTERNAL and is_externally_openable(url.scheme()):
            # Only launch the system browser if this navigation carries
            # user intent: it's itself a user action, or it's the redirect
            # tail of a link the user clicked moments ago. A spontaneous
            # page-driven redirect to an external site does NOT auto-launch
            # the browser.
            ms_since = None if self._last_user_nav_ms is None else (now_ms - self._last_user_nav_ms)
            if external_launch_allowed(is_user_nav, ms_since):
                QDesktopServices.openUrl(url)
            elif self._dev_mode:
                print(f"[nav] external-suppressed (no user intent) {url.toString()}")
        return False


class MessengerView(QWebEngineView):
    """QWebEngineView with a trimmed, chat-app-appropriate context menu
    instead of Chromium's full default (which includes irrelevant items
    like View Page Source, Save As, and Print)."""

    # Runtime value is set from config.json (dev_mode) in MessengerWindow;
    # this class default is the safe fallback if the view is ever
    # constructed without the window wiring it up.
    DEV_MODE = False

    def contextMenuEvent(self, event):
        request = self.lastContextMenuRequest()
        menu = QMenu(self)
        # QMenu.popup() is asynchronous; without this, a new QMenu
        # parented to the view accumulates as a live child object on
        # every right-click for the life of the window.
        menu.aboutToHide.connect(menu.deleteLater)
        page = self.page()

        misspelled = request.misspelledWord()
        if misspelled:
            suggestions = request.spellCheckerSuggestions()
            if suggestions:
                for suggestion in suggestions[:5]:
                    action = menu.addAction(suggestion)
                    action.triggered.connect(
                        lambda checked=False, s=suggestion: page.replaceMisspelledWord(s)
                    )
            else:
                none_action = menu.addAction(f'No suggestions for "{misspelled}"')
                none_action.setEnabled(False)
            menu.addSeparator()

        editable = request.isContentEditable()
        has_selection = bool(request.selectedText())

        if editable:
            undo_action = menu.addAction("Undo")
            undo_action.triggered.connect(
                lambda: page.triggerAction(QWebEnginePage.WebAction.Undo)
            )
            redo_action = menu.addAction("Redo")
            redo_action.triggered.connect(
                lambda: page.triggerAction(QWebEnginePage.WebAction.Redo)
            )
            menu.addSeparator()

            cut_action = menu.addAction("Cut")
            cut_action.setEnabled(has_selection)
            cut_action.triggered.connect(
                lambda: page.triggerAction(QWebEnginePage.WebAction.Cut)
            )

        copy_action = menu.addAction("Copy")
        copy_action.setEnabled(has_selection)
        copy_action.triggered.connect(
            lambda: page.triggerAction(QWebEnginePage.WebAction.Copy)
        )

        if editable:
            paste_action = menu.addAction("Paste")
            paste_action.triggered.connect(
                lambda: page.triggerAction(QWebEnginePage.WebAction.Paste)
            )

        select_all_action = menu.addAction("Select All")
        select_all_action.triggered.connect(
            lambda: page.triggerAction(QWebEnginePage.WebAction.SelectAll)
        )

        link_url = request.linkUrl()
        if not link_url.isEmpty():
            menu.addSeparator()
            copy_link_action = menu.addAction("Copy Link")
            copy_link_action.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(link_url.toString())
            )
            open_link_action = menu.addAction("Open Link in Browser")
            open_link_action.triggered.connect(
                lambda: QDesktopServices.openUrl(link_url)
                if is_externally_openable(link_url.scheme())
                else None
            )

        media_url = request.mediaUrl()
        if not media_url.isEmpty():
            menu.addSeparator()
            copy_image_action = menu.addAction("Copy Image")
            copy_image_action.triggered.connect(
                lambda: page.triggerAction(QWebEnginePage.WebAction.CopyImageToClipboard)
            )

        menu.addSeparator()
        reload_action = menu.addAction("Reload")
        reload_action.triggered.connect(
            lambda: page.triggerAction(QWebEnginePage.WebAction.Reload)
        )

        if self.DEV_MODE:
            menu.addSeparator()
            inspect_action = menu.addAction("Inspect Element")
            inspect_action.triggered.connect(
                lambda: page.triggerAction(QWebEnginePage.WebAction.InspectElement)
            )

        menu.popup(event.globalPos())


class MessengerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Messenger")
        self.resize(1100, 750)

        self.app_icon = build_app_icon()
        self.setWindowIcon(self.app_icon)
        self.taskbar_badge = None  # created after the native window exists
        self._current_notification = None  # kept alive so .click()/.close() work
        # Media consent cache, keyed by (scheme, host, port, permission_type)
        # so one approval can't leak across origins or capabilities.
        self._media_permission_cache = {}

        # Single source of user-tunable behaviour (config.json). Fail-safe:
        # a missing/broken config falls back to built-in defaults.
        self.config = load_config()
        self._messaging_prefixes, self._auth_prefixes = navigation_prefixes(self.config)
        self._dev_mode = dev_mode_enabled(self.config)
        # DEV_MODE governs the right-click "Inspect Element" item too.
        MessengerView.DEV_MODE = self._dev_mode

        # Persistent profile so login/session survives restarts.
        profile = QWebEngineProfile("messenger-standalone", self)
        self.profile = profile  # keep a strong reference alongside the page's

        # A named (persistent) profile defaults to storing granted
        # permissions on disk — notably Notifications, which is a persistent
        # permission — so an accepted decision could outlive and bypass our
        # own permissionRequested policy across restarts. This app's Python
        # policy is meant to be authoritative, so force every permission
        # back through it each session.
        profile.setPersistentPermissionsPolicy(
            QWebEngineProfile.PersistentPermissionsPolicy.AskEveryTime
        )

        # No hardcoded UA override: an outdated frozen UA string is
        # more likely to trigger unsupported-browser warnings than
        # QtWebEngine's own authentic Chromium UA is to cause problems.

        settings = profile.settings()
        # Deliberately left off: this only governs JS-initiated
        # clipboard access (document.execCommand/Clipboard API calls
        # from page scripts), not normal user copy/paste, which
        # doesn't need it. Qt recommends leaving it disabled unless a
        # specific feature requires it.
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.ScreenCaptureEnabled, True)

        # Spellcheck, but only if the Hunspell .bdic dictionaries are
        # actually present — the PyQt6 wheels don't ship them, and enabling
        # it without them just prints a "could not find dictionaries"
        # warning and does nothing. Languages / an optional custom path come
        # from config.json (spellcheck). Drop .bdic files into a
        # qtwebengine_dictionaries folder (or point dictionaries_path at
        # one) to turn it on. See the README.
        langs, dict_path = spellcheck_settings(self.config)
        available = available_spellcheck_languages(
            langs, self._spellcheck_search_dirs(dict_path))
        if available:
            profile.setSpellCheckEnabled(True)
            profile.setSpellCheckLanguages(list(available))
        else:
            profile.setSpellCheckEnabled(False)
            if self._dev_mode and langs:
                print(f"[spellcheck] no .bdic dictionaries found for "
                      f"{list(langs)}; spellcheck off (see README to enable)")

        # Network-level filter: drop Facebook's telemetry/beacon traffic to
        # cut the background resource drain. Settings come from config.json
        # (telemetry_filter). Conservative and logged by default — set
        # "enabled": false there if messaging ever misbehaves.
        if TelemetryBlocker is not None:
            enabled, log_blocked, block_rules = telemetry_settings(self.config)
            self._interceptor = TelemetryBlocker(
                rules=block_rules, enabled=enabled, log_blocked=log_blocked,
                parent=self,
            )
            profile.setUrlRequestInterceptor(self._interceptor)

        # Cosmetic filter: hide residual Facebook chrome on the messages
        # page and pause feed-style autoplay video (also stops the codec
        # error firing on H.264 clips this engine can't play). Selectors +
        # the autoplay toggle come from config.json (chrome_filter). Runs at
        # document-ready in the page's own world.
        hide_selectors, pause_autoplay = chrome_settings(self.config)
        chrome_script = QWebEngineScript()
        chrome_script.setName("chrome_filter")
        chrome_script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        chrome_script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        chrome_script.setRunsOnSubFrames(False)
        chrome_script.setSourceCode(
            build_injection_js(
                css_from_selectors(hide_selectors),
                pause_autoplay,
                self._messaging_prefixes,
            )
        )
        profile.scripts().insert(chrome_script)

        # Native notification path — no injected JS/WebChannel bridge.
        # Qt hands us a QWebEngineNotification with title/message/origin
        # already parsed; we retain it, .show() it, and forward a click
        # back into the page via .click() so Messenger's own
        # notification-click handling actually runs.
        profile.setNotificationPresenter(self._present_notification)

        self.view = MessengerView(self)
        self.page = MessengerPage(
            profile, self.view,
            messaging_prefixes=self._messaging_prefixes,
            auth_prefixes=self._auth_prefixes,
            dev_mode=self._dev_mode,
        )
        self.page.permissionRequested.connect(self._handle_permission_requested)
        self.page.newWindowRequested.connect(self._handle_new_window_request)
        self.page.desktopMediaRequested.connect(self._handle_desktop_media_requested)
        self.view.setPage(self.page)

        self.setCentralWidget(self.view)
        self.view.load(QUrl(MESSENGER_URL))
        self.view.titleChanged.connect(self._on_title_changed)

        self._build_tray_icon()
        self._setup_global_hotkey()

    def _handle_permission_requested(self, permission):
        origin = permission.origin()
        trusted = is_trusted_permission_origin(origin.scheme(), origin.host(), origin.port())
        permission_type = permission.permissionType()

        if not trusted or permission_type not in GRANTABLE_PERMISSIONS:
            permission.deny()
            return

        if permission_type in MEDIA_CAPTURE_PERMISSIONS and \
                not self._confirm_media_permission(origin, permission_type):
            permission.deny()
            return

        permission.grant()

    def _confirm_media_permission(self, origin, permission_type) -> bool:
        """Native Allow/Deny prompt for microphone/camera, on top of the
        origin check. The decision is cached per (origin, capability), NOT
        globally — approving the mic on www.facebook.com does not silently
        approve the camera, or the mic on a different origin. The prompt
        names the actual origin and the actual capability requested."""
        key = media_permission_key(
            origin.scheme(), origin.host(), origin.port(), permission_type)
        if key in self._media_permission_cache:
            return self._media_permission_cache[key]

        capability = media_capability_label(permission_type)
        host = origin.host() or "This site"
        reply = QMessageBox.question(
            self,
            "Messenger",
            f"{host} wants to use your {capability}.\n\nAllow?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        allowed = reply == QMessageBox.StandardButton.Yes
        self._media_permission_cache[key] = allowed
        return allowed

    def _handle_desktop_media_requested(self, request):
        # Reached only after DesktopVideoCapture/DesktopAudioVideoCapture
        # was already granted to a trusted origin above; this step just
        # lets the user pick *which* screen/window to share.
        #
        # The screen/window list is a live Qt model that can change
        # while the picker dialog is open (a window closes, a new one
        # appears). Snapshotting a plain row number and recreating
        # model.index(row, 0) later can silently end up pointing at a
        # different source than the one the user actually picked — for
        # a screen-share capability that's a privacy bug, not just a
        # UI glitch. QPersistentModelIndex tracks the same underlying
        # item across model changes instead of a row position, and is
        # revalidated immediately before use.
        screens_model = request.screensModel()
        windows_model = request.windowsModel()

        options = []  # (label, QPersistentModelIndex, kind)
        for row in range(screens_model.rowCount()):
            index = screens_model.index(row, 0)
            label = index.data(Qt.ItemDataRole.DisplayRole)
            # Row number folded into the label so two sources with an
            # identical title are still distinguishable in the list.
            options.append((f"Screen {row + 1}: {label}", QPersistentModelIndex(index), "screen"))
        for row in range(windows_model.rowCount()):
            index = windows_model.index(row, 0)
            label = index.data(Qt.ItemDataRole.DisplayRole)
            options.append((f"Window {row + 1}: {label}", QPersistentModelIndex(index), "window"))

        if not options:
            request.cancel()
            return

        labels = [label for label, _, _ in options]
        choice, ok = QInputDialog.getItem(
            self, "Share your screen", "Choose what to share with Messenger:", labels, 0, False
        )
        if not ok:
            request.cancel()
            return

        _, persistent_index, kind = options[labels.index(choice)]
        if not persistent_index.isValid():
            # The source disappeared (e.g. window closed) while the
            # picker was open — don't guess, just cancel the request.
            request.cancel()
            return

        index = QModelIndex(persistent_index)
        if kind == "screen":
            request.selectScreen(index)
        else:
            request.selectWindow(index)

    def _handle_new_window_request(self, request):
        # Messenger/Facebook use target="_blank" for some links (external
        # sites shared in chat, media viewers). Qt does nothing with these
        # unless handled. classify_new_window applies the same message-only
        # policy as top-level navigation, with one extra guard: an
        # internal-scheme popup (e.g. window.open()'s about:blank) is
        # DROPPED, never routed into the main page — otherwise a stray
        # about:blank popup would blank out the live chat view.
        url = request.requestedUrl()
        action = classify_new_window(
            url.scheme(), url.host(), url.path(), url.port(),
            self._messaging_prefixes, self._auth_prefixes,
        )

        # A page can script-open a popup with no user action at all. We let
        # an automatic popup navigate IN-APP (a benign same-surface open),
        # but launching the *external* system browser must require a real
        # user gesture — otherwise a hostile/needy page could auto-pop your
        # real browser to arbitrary sites. Qt exposes isUserInitiated()
        # precisely for this distinction; don't rely on Chromium having
        # already filtered it.
        user_initiated = request.isUserInitiated()

        if action == NAV_IN_APP:
            request.openIn(self.page)
        elif action == NAV_REWRITE_TO_MESSAGES:
            QTimer.singleShot(0, lambda: self.page.setUrl(QUrl(FACEBOOK_MESSAGES_URL)))
        elif action == NAV_EXTERNAL and user_initiated and is_externally_openable(url.scheme()):
            QDesktopServices.openUrl(url)
        elif self._dev_mode:
            reason = "no user gesture" if action == NAV_EXTERNAL and not user_initiated else action
            print(f"[popup] dropped ({reason}) {url.toString()}")
        # anything else: dropped

    def _present_notification(self, notification):
        origin = notification.origin()
        if not is_trusted_permission_origin(origin.scheme(), origin.host(), origin.port()):
            return  # ignore notifications from any non-Messenger origin

        # Only one active notification at a time: close the previous
        # one before showing the next, rather than just overwriting
        # the reference and leaking/never-closing it.
        self._close_current_notification()

        self._current_notification = notification
        notification.closed.connect(lambda n=notification: self._on_notification_closed(n))
        notification.show()

        self.tray.showMessage(
            notification.title() or "Messenger",
            notification.message(),
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
        # Keep our reference's lifetime roughly matching the balloon's
        # own ~5s display time. Bound to this specific notification
        # object (not "whatever is current" at fire time) so a newer
        # notification arriving in the meantime can't get closed early
        # by an older notification's expiry timer.
        QTimer.singleShot(5000, lambda n=notification: self._expire_notification(n))

    def _on_notification_closed(self, notification):
        if self._current_notification is notification:
            self._current_notification = None

    def _expire_notification(self, notification):
        if self._current_notification is notification:
            self._close_current_notification()

    def _close_current_notification(self):
        if self._current_notification is not None:
            notification = self._current_notification
            self._current_notification = None
            try:
                notification.close()
            except Exception:
                pass

    def _on_tray_message_clicked(self):
        # Note: Qt can also emit this when the tray icon itself is
        # clicked while a balloon happens to be showing, not only on a
        # genuine balloon click — there's no way to fully disambiguate
        # that from here. Closing immediately after click at least
        # keeps the window for a mis-click narrow.
        if self._current_notification is not None:
            notification = self._current_notification
            notification.click()
            self._close_current_notification()

    @staticmethod
    def _spellcheck_search_dirs(config_path):
        """Directories Qt WebEngine will actually search for .bdic
        dictionaries, in priority order: a config-supplied path, the
        QTWEBENGINE_DICTIONARIES_PATH env var, the PyQt6 Qt6 bundle dir,
        and a qtwebengine_dictionaries folder next to this script."""
        dirs = []
        if config_path:
            dirs.append(config_path)
        env = os.environ.get("QTWEBENGINE_DICTIONARIES_PATH")
        if env:
            dirs.append(env)
        try:
            import PyQt6
            dirs.append(str(Path(PyQt6.__file__).parent / "Qt6"
                            / "qtwebengine_dictionaries"))
        except Exception:
            pass
        dirs.append(str(Path(__file__).resolve().parent
                        / "qtwebengine_dictionaries"))
        return dirs

    def _setup_global_hotkey(self):
        # Global show/hide combo, from config.json (hotkey). Default is
        # Ctrl+Alt+M; change it there if it clashes with another app (the
        # symptom is a "could not register global hotkey" warning). Set
        # "enabled": false to skip the hotkey entirely.
        enabled, modifiers, key = hotkey_settings(self.config)
        if not enabled:
            self.hotkey = None
            return
        self.hotkey = GlobalHotkey(modifiers=modifiers, key=key)
        self.hotkey.activated.connect(self._toggle_visibility)
        QApplication.instance().installNativeEventFilter(self.hotkey)
        QApplication.instance().aboutToQuit.connect(self.hotkey.unregister)

    def _toggle_visibility(self):
        if self.isVisible() and self.isActiveWindow():
            self.hide()
        else:
            self._restore_from_tray()

    def _build_tray_icon(self):
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.app_icon)
        self.tray.setToolTip("Messenger")

        # Stored on self: QSystemTrayIcon.setContextMenu() does NOT take
        # ownership of the menu, so a menu kept only as a local variable
        # here would eventually be garbage-collected out from under the
        # tray icon — a bug that tends to show up intermittently, later.
        self.tray_menu = QMenu(self)
        show_action = QAction("Show Messenger", self)
        show_action.triggered.connect(self._restore_from_tray)

        startup_action = QAction("Launch at Startup", self)
        startup_action.setCheckable(True)
        startup_action.setChecked(is_startup_enabled(ENTRY_SCRIPT))
        startup_action.toggled.connect(
            lambda checked: set_startup_enabled(checked, ENTRY_SCRIPT)
        )

        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(QApplication.instance().quit)

        self.tray_menu.addAction(show_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(startup_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(quit_action)

        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_tray_message_clicked)
        self.tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._restore_from_tray()

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_title_changed(self, title: str):
        # Messenger sometimes prefixes the page title with an unread
        # count, e.g. "(3) Messenger". Surface that on the window title,
        # the taskbar overlay badge, and the tray tooltip.
        self.setWindowTitle(title or "Messenger")

        match = UNREAD_TITLE_RE.match(title or "")
        count = int(match.group(1)) if match else 0

        if self.taskbar_badge is not None:
            self.taskbar_badge.set_count(count)

        self.tray.setToolTip(f"Messenger ({count} unread)" if count else "Messenger")

    def showEvent(self, event):
        super().showEvent(event)
        # winId() only resolves to a real native handle once the window
        # has been shown, so the taskbar badge is created here rather
        # than in __init__.
        if self.taskbar_badge is None:
            self.taskbar_badge = TaskbarBadge(int(self.winId()))

    def closeEvent(self, event):
        # Minimize to tray instead of quitting, like a real chat app.
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Messenger",
            "Still running in the tray.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def cleanup(self):
        self._close_current_notification()
        if self.taskbar_badge is not None:
            self.taskbar_badge.cleanup()


def main():
    # If a custom spellcheck dictionaries path is configured, export it
    # BEFORE QtWebEngine initialises — Qt reads QTWEBENGINE_DICTIONARIES_PATH
    # at startup. (Dictionaries in the default Qt location are found without
    # this.) Done here rather than in the window so it lands early enough.
    try:
        _cfg = load_config()
        _, _dict_path = spellcheck_settings(_cfg)
        if _dict_path and os.path.isdir(_dict_path):
            os.environ.setdefault("QTWEBENGINE_DICTIONARIES_PATH", _dict_path)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Messenger")

    window = MessengerWindow()
    app.aboutToQuit.connect(window.cleanup)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
