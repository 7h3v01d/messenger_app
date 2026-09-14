# Messenger (standalone desktop app)

A standalone desktop client for Facebook Messenger, built to replace
the official app Facebook discontinued. It's a native window wrapped
around the real `messenger.com` web app (via PyQt6 + QtWebEngine),
not a reverse-engineered API client — so it stays in sync with
whatever Facebook changes on their end, with no ToS-risky login
spoofing.

## Features

- Native window, system tray icon, and app icon (drawn at runtime —
  no external image asset needed)
- Native desktop notifications, via Qt's own notification presenter
  (no injected JS bridge)
- Minimizes to tray on close instead of quitting
- Windows taskbar overlay badge showing the unread count
- Trimmed right-click context menu (spellcheck, cut/copy/paste,
  link/image actions) instead of Chromium's full default menu
- "Launch at Startup" toggle in the tray menu
- Global hotkey (default **Ctrl+Alt+M**) to show/hide from anywhere
- Top-level navigation, popups, and browser permissions (mic/camera/
  screen share/notifications) are all restricted to Messenger/
  Facebook-family origins — see Security notes below

## Requirements

- Python 3.10+
- Windows for the full feature set (taskbar badge, startup toggle,
  global hotkey). Runs on macOS/Linux for development — those three
  features just no-op there and the rest works normally.

## Installation

```
pip install -r requirements.txt
```

## Usage

```
python messenger_app.py
```

First run opens the normal Messenger login page inside the app
window; your session persists between launches (stored in a local
QtWebEngine profile named `messenger-standalone`), so you only log
in once.

## Running the tests

```
python -m unittest discover -s tests
```

Covers the pure-logic pieces: trusted-origin classification, unread
title parsing, and startup-command generation (this last one exists
specifically because that's where a real bug was once found — see
Changelog).

## Files

| File | Purpose |
|---|---|
| `messenger_app.py` | Main window, tray icon, navigation/permission gating, context menu |
| `icon_assets.py` | Draws the app icon and unread-count badge icons |
| `windows_taskbar.py` | Windows taskbar overlay badge (`ITaskbarList3` via `comtypes`) |
| `startup_manager.py` | Launch-at-startup toggle (`HKCU\...\Run` registry key) |
| `global_hotkey.py` | System-wide show/hide hotkey (`RegisterHotKey` via `ctypes`) |
| `trusted_origins.py` | Shared Messenger/Facebook host allowlist used for navigation, popups, and permissions |
| `tests/test_pure_logic.py` | Unit tests for the above pure-logic pieces |

## Security notes

This app embeds a full browser engine pointed at an authenticated
site, so the trust boundary matters:

- **Navigation**: `MessengerPage.acceptNavigationRequest()` only
  allows top-level navigation to `messenger.com`, `facebook.com`,
  `fbcdn.net`, and `fbsbx.com` (and their subdomains). Anything else —
  a clicked link, a redirect, a compromised page — opens in your
  default system browser instead of loading inside this
  Messenger-branded window.
- **Popups**: `target="_blank"` links follow the same allowlist —
  trusted destinations load in the same window, everything else goes
  to the system browser.
- **Permissions**: microphone, camera, screen/desktop capture, and
  notification permissions are only auto-granted to trusted origins
  (`_handle_permission_requested`); everything else is denied.
- **Notifications**: only shown for notifications originating from a
  trusted origin.

If you ever need to widen or narrow this allowlist, it's all in
`trusted_origins.py` — update it there and every consumer (nav,
popups, permissions) picks it up automatically.

## Customization

- **Hotkey**: change the modifiers/key in
  `MessengerWindow._setup_global_hotkey()` in `messenger_app.py` if
  Ctrl+Alt+M clashes with something else on your system.
- **Spellcheck language**: set in `MessengerWindow.__init__` via
  `profile.setSpellCheckLanguages([...])` — defaults to `en-US`.
- **Developer tools**: `MessengerView.DEV_MODE = True` keeps
  "Inspect Element" in the right-click menu. Set to `False` if this
  is ever handed off to a non-technical user.
- **App icon**: currently a drawn placeholder (blue rounded square,
  "M" mark). Replace the body of `build_app_icon()` in
  `icon_assets.py` with a real designed icon whenever you have one.
- **Trusted origins**: see Security notes above.

## Known limitations

- The startup toggle registers the *current* Python interpreter and
  the app's entry-point script path in the registry. If this is later
  packaged as a standalone `.exe` (e.g. via PyInstaller),
  `startup_manager.py`'s `_launch_command()` will need updating to
  point at that instead — otherwise a moved/renamed dev folder will
  silently break the startup entry.
- Voice/video calls rely on QtWebEngine's Chromium build supporting
  whatever WebRTC features Messenger currently needs; not
  exhaustively tested end-to-end.
- Console warnings about `Permissions-Policy` headers and `unload`
  listeners in the terminal are harmless — they come from Facebook's
  own page code hitting minor gaps in QtWebEngine's bundled Chromium
  version, not from this app.
- `requirements.txt` uses a `>=` floor rather than a fully pinned
  lockfile. For an app whose job is rendering an authenticated site,
  the WebEngine/Chromium version is effectively the security
  perimeter — update deliberately (`pip install --upgrade -r
  requirements.txt`) rather than letting it drift.

## Changelog

**v0.3** — Fixes from an adversarial code review:
- Fixed a release-blocking bug where "Launch at Startup" registered a
  command pointing at `startup_manager.py` instead of
  `messenger_app.py`, so the app would never actually start at login
- Added a navigation trust boundary (`acceptNavigationRequest`) —
  previously any link/redirect could navigate the whole window to an
  arbitrary origin while keeping the Messenger-branded chrome
  (phishing risk)
- Added permission gating for mic/camera/screen-share/notifications,
  restricted to trusted origins
- Replaced the injected-JS/WebChannel notification bridge with Qt's
  native `setNotificationPresenter` — smaller attack surface, no
  script injected into every navigation, no JS lying about
  `Notification.permission` state
- Fixed the tray context menu being held only as a local variable
  (`QSystemTrayIcon` doesn't take ownership of it — this could have
  caused intermittent failures once garbage-collected)
- Fixed `target="_blank"` links doing nothing (unhandled
  `newWindowRequested`)
- Fixed a GDI handle leak in the taskbar badge (`DestroyIcon` was
  never called after `SetOverlayIcon`)
- Taskbar badge temp directory is now Windows-only and cleaned up on
  quit
- Removed the hardcoded, already-outdated Chrome-124 user-agent
  string in favor of QtWebEngine's authentic UA
- Disabled `JavascriptCanAccessClipboard` (governs page-script
  clipboard access, not normal user copy/paste)
- Added `MOD_NOREPEAT` to the global hotkey so holding it down
  doesn't repeatedly toggle visibility
- Fixed right-click context menus accumulating as live child objects
  over a long session
- Added a small unit test suite for the pure-logic pieces
- Raised the dependency floor to Qt 6.8+ (required for the new
  permission API; also meaningfully more current/patched than the
  previous 6.6 floor)

**v0.2** — App icon, taskbar unread badge, trimmed context menu with
spellcheck, launch-at-startup toggle, global hotkey.

**v0.1** — Initial QWebEngineView wrapper with tray icon and
notification bridge.
