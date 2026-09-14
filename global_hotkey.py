"""
A global hotkey (works even when the app isn't focused) to show/hide
the Messenger window, e.g. Ctrl+Alt+M.

Uses the native Win32 RegisterHotKey API directly via ctypes rather
than a third-party keylogging-adjacent package, and taps into Qt's
own native event loop via QAbstractNativeEventFilter — no separate
polling thread needed. No-ops cleanly on non-Windows platforms.
"""

import ctypes
import sys
from ctypes import wintypes

from PyQt6.QtCore import QObject, QAbstractNativeEventFilter, pyqtSignal

IS_WINDOWS = sys.platform == "win32"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# Without this, Windows re-fires WM_HOTKEY on key-repeat while the
# combo is held down, rapidly toggling window visibility.
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312

if IS_WINDOWS:
    user32 = ctypes.windll.user32


class GlobalHotkey(QObject, QAbstractNativeEventFilter):
    """Registers a system-wide hotkey and emits `activated` when pressed.

    Must be kept alive for as long as the hotkey should work (store it
    as an attribute, not a local variable), and must be installed on
    the QApplication via `app.installNativeEventFilter(instance)`.
    """

    activated = pyqtSignal()

    def __init__(
        self, modifiers=MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, key="M", hotkey_id=1, parent=None
    ):
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._id = hotkey_id
        self._registered = False

        if IS_WINDOWS:
            vk = ord(key.upper())
            self._registered = bool(user32.RegisterHotKey(None, self._id, modifiers, vk))
            if not self._registered:
                # Fail closed: another app likely already owns this
                # combination. Don't crash — just run without the
                # hotkey and let the tray/window controls still work.
                print(
                    f"Warning: could not register global hotkey (id={self._id}); "
                    "it may already be in use by another application."
                )

    def nativeEventFilter(self, eventType, message):
        if IS_WINDOWS and eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self._id:
                self.activated.emit()
        return False, 0

    def unregister(self):
        if IS_WINDOWS and self._registered:
            user32.UnregisterHotKey(None, self._id)
            self._registered = False
