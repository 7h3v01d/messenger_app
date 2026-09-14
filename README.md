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
- Native desktop notifications with full click-through: clicking the
  OS notification forwards the click back into Messenger's own
  notification-click handling
- Minimizes to tray on close instead of quitting
- Windows taskbar overlay badge showing the unread count
- Trimmed right-click context menu (spellcheck, cut/copy/paste,
  link/image actions) instead of Chromium's full default menu
- "Launch at Startup" toggle in the tray menu
- Global hotkey (default **Ctrl+Alt+M**) to show/hide from anywhere
- Screen sharing works end-to-end and picks the source you actually
  chose, even if the live screen/window list changes while the picker
  is open
- Microphone/camera requests get a native confirmation prompt, not
  just a silent origin check
- Navigation, popups, and browser permissions (mic/camera/screen
  share/notifications) are all restricted by origin — see Security
  notes below

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

The test file must live at `tests/test_pure_logic.py` (not the
project root) for discovery to find it — see the file table below.
Covers trust-policy classification, unread title parsing, and
startup-command generation — the exact pieces where real bugs were
found during review (see Changelog).

## Files

| File | Purpose |
|---|---|
| `messenger_app.py` | Main window, tray icon, navigation/permission/screen-share gating, context menu |
| `icon_assets.py` | Draws the app icon and unread-count badge icons |
| `windows_taskbar.py` | Windows taskbar overlay badge (`ITaskbarList3` via `comtypes`) |
| `startup_manager.py` | Launch-at-startup toggle (`HKCU\...\Run` registry key) |
| `global_hotkey.py` | System-wide show/hide hotkey (`RegisterHotKey` via `ctypes`) |
| `trusted_origins.py` | Navigation vs. permission trust policies, shared across the app |
| `tests/test_pure_logic.py` | Unit tests for the above pure-logic pieces |

## Security notes

This app embeds a full browser engine pointed at an authenticated
site, so the trust boundary matters — and it's deliberately **two
different policies**, not one:

- **Navigation policy** (`is_trusted_navigation_target`): which hosts
  are allowed to load as top-level content — just `messenger.com` and
  `facebook.com` (and their subdomains), over HTTPS only.
  `fbcdn.net`/`fbsbx.com` are deliberately *not* included: they're
  only ever needed as subresources (images, media), which
  `acceptNavigationRequest()` already allows unconditionally via its
  `is_main_frame` check, so they never needed a place in the
  top-level allowlist. `MessengerPage.acceptNavigationRequest()`
  enforces this; anything else opens externally (if it's a scheme
  worth handing to the OS at all — see below) instead of loading
  inside this Messenger-branded window. `target="_blank"` popups
  follow the same rule.
- **Permission policy** (`is_trusted_permission_origin`) — narrower
  still and origin-aware, not just hostname-based: only a real HTTPS
  `messenger.com`/`facebook.com` origin on the default port can be
  granted microphone, camera, screen-share, or notification access.
  `http://messenger.com` or `https://messenger.com:4443` are rejected
  too — matching the scheme and port, not just the host name, is what
  makes this origin-safe rather than hostname-only.
- **Microphone/camera get an extra native confirmation prompt**
  beyond the origin check (`_confirm_media_permission`) — the origin
  check alone would let any trusted-origin page turn the mic/camera
  on with zero human decision point. The choice is cached for the
  rest of the running session so it doesn't re-prompt on every call.
  Screen share has an equivalent gate built in already (you have to
  actively pick a source); notifications don't get one since they're
  lower-stakes.
- **Screen sharing** (`_handle_desktop_media_requested`): the
  screen/window list Qt provides is a live model that can change
  while the picker dialog is open. The picker uses
  `QPersistentModelIndex` per option (not a raw row number) so the
  source you actually clicked stays correctly identified even if
  another window opens or closes in the meantime, and it's
  revalidated immediately before `selectScreen()`/`selectWindow()` —
  if the chosen source disappeared while the dialog was open, the
  request is cancelled rather than guessing. Each option's label also
  includes its position (`Window 2: Chrome`) so two sources sharing
  an identical title stay individually selectable.
- **Notification lifecycle**: only one notification is kept active at
  a time — a new one closes the previous rather than silently
  replacing the reference and leaking it. Expiry (matching the ~5s
  tray balloon lifetime) and the `closed` signal are both tied to the
  specific notification object they belong to, so a late timer from
  an older notification can't close a newer one that replaced it in
  the meantime.
- **External scheme delegation**: any URL that fails the trust check
  only gets handed to the OS's default handler
  (`QDesktopServices.openUrl`) if it's `http`, `https`, or `mailto`
  (`is_externally_openable`). A hostile link using some other scheme
  is dropped rather than silently launched through whatever handler
  Windows has registered for it.

If you ever need to widen or narrow either policy, it's all in
`trusted_origins.py` — update it there and every consumer (nav,
popups, permissions) picks it up automatically.

## Customization

- **Hotkey**: change the modifiers/key in
  `MessengerWindow._setup_global_hotkey()` in `messenger_app.py` if
  Ctrl+Alt+M clashes with something else on your system.
- **Spellcheck language**: set in `MessengerWindow.__init__` via
  `profile.setSpellCheckLanguages([...])` — defaults to `en-US`.
- **Developer tools**: `MessengerView.DEV_MODE = True` keeps
  "Inspect Element" in the right-click menu. Set to `False` before
  producing a release build or handing this to a non-technical user.
- **App icon**: currently a drawn placeholder (blue rounded square,
  "M" mark). Replace the body of `build_app_icon()` in
  `icon_assets.py` with a real designed icon whenever you have one.
- **Trust policies**: see Security notes above.

## Packaging / making a release zip

If you're zipping this up on macOS to share or archive, use one of:

```
COPYFILE_DISABLE=1 zip -r messenger-app.zip . -x ".*" -x "__MACOSX/*"
```

or Finder's "Compress" after first clearing extended attributes with
`xattr -cr .` in the project folder. Plain `zip -r` on macOS embeds a
`__MACOSX/` tree of AppleDouble resource-fork files (`._*.py`) that
are pure metadata but will make naive tools (e.g. `compileall`) choke
on them even though every real project file compiles fine. Also set
`MessengerView.DEV_MODE = False` before packaging a build meant for
someone else.

## Known limitations

- The startup toggle registers the *current* Python interpreter and
  the app's entry-point script path in the registry. If this is later
  packaged as a standalone `.exe` (e.g. via PyInstaller),
  `startup_manager.py`'s `_launch_command()` will need updating to
  point at that instead — otherwise a moved/renamed dev folder will
  silently break the startup entry.
- Voice calls rely on QtWebEngine's Chromium build supporting
  whatever WebRTC features Messenger currently needs; not
  exhaustively tested end-to-end. Screen sharing's source-selection
  flow is implemented and tested manually, but only with a basic
  picker UI (a plain list dialog, not a live thumbnail preview like
  a browser's native picker).
- Console warnings about `Permissions-Policy` headers and `unload`
  listeners in the terminal are harmless — they come from Facebook's
  own page code hitting minor gaps in QtWebEngine's bundled Chromium
  version, not from this app.
- `requirements.txt` uses a `>=` floor rather than a fully pinned
  lockfile. For an app whose job is rendering an authenticated site,
  the WebEngine/Chromium version is effectively the security
  perimeter — generate and commit a real lockfile periodically with
  `pip freeze > requirements-lock.txt` and install from that for
  anything beyond local dev, rather than relying on the floor alone.

## Changelog

**v0.5** — Fixes from a third adversarial review:
- Fixed a release-blocking screen-share bug: the picker previously
  snapshotted each source as a plain row number and recreated the
  model index later, which could silently select the wrong
  screen/window if the live source list changed while the dialog was
  open. Now uses `QPersistentModelIndex` per option, revalidated
  immediately before `selectScreen()`/`selectWindow()`, with the
  request cancelled if the chosen source disappeared in the meantime
- Row-numbered picker labels so two sources with identical titles
  (e.g. two windows both named "Chrome") are still distinguishable
- Added a native Allow/Deny confirmation for microphone/camera
  requests on top of the origin check, so a trusted-origin page can't
  silently turn them on with zero human decision point; cached for
  the running session
- Removed `fbcdn.net`/`fbsbx.com` from the top-level navigation
  allowlist too (previously only removed from permissions) — they
  were never needed there since subresources already bypass the
  check entirely, and keeping them let a CDN URL replace the whole
  window as a top-level page
- Fixed the notification lifecycle: only one notification stays
  active at a time (a new one closes the previous instead of leaking
  the reference), and both the `closed` signal and the ~5s expiry
  timer are bound to the specific notification object they belong to,
  so a late timer from an old notification can't close a newer one
- Added tests for the picker's label-uniqueness rule and the narrowed
  navigation allowlist — 23 tests total, all passing
- This release is packaged as a zip built directly from the working
  tree (`tests/` included as an actual subdirectory, no `__MACOSX`/
  `._*` artifacts) rather than assembled from individually downloaded
  files, since that reassembly step was the likely source of the
  packaging mismatches flagged in the last two reviews

**v0.4** — Fixes from a second adversarial review: split the trust
check into separate navigation vs. permission policies, made the
permission policy origin-aware (scheme + port, not just hostname),
restricted external scheme delegation to http/https/mailto,
implemented `desktopMediaRequested` (first pass), completed the
notification click-through lifecycle (first pass).

**v0.3** — Fixes from the first adversarial review: startup command
bug, tray menu ownership, `target="_blank"` handling, taskbar HICON
leak, temp-dir cleanup, stale hardcoded UA, clipboard setting,
`MOD_NOREPEAT`, context-menu accumulation, initial trust boundary and
permission gating, native notification presenter (first pass), first
test suite.

**v0.2** — App icon, taskbar unread badge, trimmed context menu with
spellcheck, launch-at-startup toggle, global hotkey.

**v0.1** — Initial QWebEngineView wrapper with tray icon and
notification bridge.
