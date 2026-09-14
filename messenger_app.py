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

import re
import sys
from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QIcon, QAction, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QSystemTrayIcon,
    QMenu,
    QInputDialog,
)
from PyQt6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEnginePage,
    QWebEngineSettings,
    QWebEnginePermission,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from icon_assets import build_app_icon
from windows_taskbar import TaskbarBadge
from startup_manager import is_startup_enabled, set_startup_enabled
from global_hotkey import GlobalHotkey, MOD_CONTROL, MOD_ALT, MOD_NOREPEAT
from trusted_origins import (
    is_trusted_navigation_target,
    is_trusted_permission_origin,
    is_externally_openable,
)

# Matches Messenger's title prefix, e.g. "(3) Messenger"
UNREAD_TITLE_RE = re.compile(r"^\((\d+)\)")

MESSENGER_URL = "https://messenger.com"

# The actual entry point, used to build a correct startup command
# regardless of which module happens to be executing.
ENTRY_SCRIPT = Path(__file__).resolve()

# Permission types we're willing to auto-grant, and only ever to a
# trusted (Messenger/Facebook) origin — needed for calls, screen
# share, and native-looking notifications. Desktop capture also
# requires completing the desktopMediaRequested picker flow below;
# granting the permission alone doesn't let a source be selected.
GRANTABLE_PERMISSIONS = {
    QWebEnginePermission.PermissionType.MediaAudioCapture,
    QWebEnginePermission.PermissionType.MediaVideoCapture,
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture,
    QWebEnginePermission.PermissionType.DesktopVideoCapture,
    QWebEnginePermission.PermissionType.DesktopAudioVideoCapture,
    QWebEnginePermission.PermissionType.Notifications,
}


class MessengerPage(QWebEnginePage):
    """Enforces a trust boundary on top-level navigation: Messenger and
    Facebook-family origins stay inside the app; anything else (a
    clicked link, a redirect, a compromised/injected page) is sent to
    the system browser instead of loading inside this Messenger-branded
    native window — and only if it's a scheme worth handing to the OS
    at all (see trusted_origins.is_externally_openable)."""

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if not is_main_frame:
            return True  # don't gate subresources/iframes (CDN assets etc.)
        if is_trusted_navigation_target(url.scheme(), url.host()):
            return True
        if is_externally_openable(url.scheme()):
            QDesktopServices.openUrl(url)
        return False


class MessengerView(QWebEngineView):
    """QWebEngineView with a trimmed, chat-app-appropriate context menu
    instead of Chromium's full default (which includes irrelevant items
    like View Page Source, Save As, and Print)."""

    # Keeps "Inspect Element" available for debugging. Set False before
    # handing this off to a non-technical user.
    DEV_MODE = True

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
        self._current_notification = None  # kept alive so .click() works

        # Persistent profile so login/session survives restarts.
        profile = QWebEngineProfile("messenger-standalone", self)
        self.profile = profile  # keep a strong reference alongside the page's

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

        profile.setSpellCheckEnabled(True)
        # Adjust to match your locale/keyboard if needed, e.g. "en-GB".
        profile.setSpellCheckLanguages(["en-US"])

        # Native notification path — no injected JS/WebChannel bridge.
        # Qt hands us a QWebEngineNotification with title/message/origin
        # already parsed; we retain it, .show() it, and forward a click
        # back into the page via .click() so Messenger's own
        # notification-click handling actually runs.
        profile.setNotificationPresenter(self._present_notification)

        self.view = MessengerView(self)
        self.page = MessengerPage(profile, self.view)
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
        if trusted and permission.permissionType() in GRANTABLE_PERMISSIONS:
            permission.grant()
        else:
            permission.deny()

    def _handle_desktop_media_requested(self, request):
        # Reached only after DesktopVideoCapture/DesktopAudioVideoCapture
        # was already granted to a trusted origin above; this step just
        # lets the user pick *which* screen/window to share — without
        # it, Messenger's getDisplayMedia() call would hang forever
        # with no source ever selected.
        screens_model = request.screensModel()
        windows_model = request.windowsModel()

        options = []
        for row in range(screens_model.rowCount()):
            label = screens_model.index(row, 0).data(Qt.ItemDataRole.DisplayRole)
            options.append((f"Screen: {label}", "screen", row))
        for row in range(windows_model.rowCount()):
            label = windows_model.index(row, 0).data(Qt.ItemDataRole.DisplayRole)
            options.append((f"Window: {label}", "window", row))

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

        _, kind, row = options[labels.index(choice)]
        if kind == "screen":
            request.selectScreen(screens_model.index(row, 0))
        else:
            request.selectWindow(windows_model.index(row, 0))

    def _handle_new_window_request(self, request):
        # Messenger uses target="_blank" for some links (e.g. external
        # sites shared in chat). Qt does nothing with these unless
        # handled: trusted destinations open in this same window,
        # externally-openable ones go to the system browser, anything
        # else is dropped rather than silently failing or handing an
        # arbitrary scheme to the OS.
        url = request.requestedUrl()
        if is_trusted_navigation_target(url.scheme(), url.host()):
            request.openIn(self.page)
        elif is_externally_openable(url.scheme()):
            QDesktopServices.openUrl(url)

    def _present_notification(self, notification):
        origin = notification.origin()
        if not is_trusted_permission_origin(origin.scheme(), origin.host(), origin.port()):
            return  # ignore notifications from any non-Messenger origin

        self._current_notification = notification
        notification.show()
        self.tray.showMessage(
            notification.title() or "Messenger",
            notification.message(),
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def _on_tray_message_clicked(self):
        if self._current_notification is not None:
            self._current_notification.click()

    def _setup_global_hotkey(self):
        # Ctrl+Alt+M to show/hide from anywhere. Change the modifiers/
        # key here if it clashes with something else on your system.
        self.hotkey = GlobalHotkey(modifiers=MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, key="M")
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
        if self.taskbar_badge is not None:
            self.taskbar_badge.cleanup()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Messenger")

    window = MessengerWindow()
    app.aboutToQuit.connect(window.cleanup)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
