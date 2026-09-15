# Messenger (standalone desktop app)

A standalone desktop client for Facebook Messenger, built to replace
the official app Facebook discontinued. It's a native window wrapped
around the real Messenger web app (via PyQt6 + QtWebEngine), not a
reverse-engineered API client — so it stays in sync with whatever
Facebook changes on their end, with no ToS-risky login spoofing.

**Message-only by design.** Meta is retiring the standalone
`messenger.com` web product (~April 2026): after login, desktop web
messaging is served from `facebook.com/messages`. That means the chat
now lives *on* facebook.com, right next to the feed, Watch, Reels and
Marketplace. This app deliberately fences itself onto the messaging
surface: `/messages` and the login/auth flows load in-app, the bare
`facebook.com/` feed is bounced straight to `/messages` so you never
see it, and everything else on facebook.com (a shared post, a profile,
a video, Marketplace) — plus every off-site link — opens in your real
browser instead of hijacking this window. See **Message-only
isolation** below.

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
| `config.json` | Single user-tunable config: facebook path allowlists, telemetry rules, chrome selectors |
| `app_config.py` | Fail-safe loader for `config.json` (falls back to built-in defaults) |
| `trusted_origins.py` | Navigation (4-way, path-aware), new-window, and permission trust policies, shared across the app |
| `request_filter.py` | Network-level telemetry/beacon blocker (`QWebEngineUrlRequestInterceptor`) |
| `chrome_filter.py` | Injected CSS/JS: hides residual Facebook chrome, pauses autoplay video |
| `tests/test_pure_logic.py` | Unit tests for the above pure-logic pieces |

## Security notes

This app embeds a full browser engine pointed at an authenticated
site, so the trust boundary matters — and it's deliberately **two
different policies**, not one:

- **Navigation policy** (`classify_navigation`): a top-level URL is
  sorted into one of four actions rather than a yes/no host check —
  `IN_APP`, `REWRITE_TO_MESSAGES`, `EXTERNAL`, or `DROP`. Hosts are an
  **explicit exact-host allowlist** (no subdomain wildcard):
  `messenger.com`/`www.messenger.com` are all-messaging and stay in-app;
  `facebook.com`/`www.facebook.com`/`web.facebook.com` are **mixed** —
  only the messaging paths (`/messages`, `/e2ee`, `/t/`) and auth paths
  (`/login`, `/checkpoint`, `/oauth`, …) stay in-app, the bare
  `facebook.com/` feed is rewritten to `/messages`, any other path opens
  externally. An unlisted subdomain (`evil.facebook.com`) is externalised,
  not trusted. Path matching is **segment-boundary** (so `/login` ≠
  `/login.evil`), and the configured prefixes are **validated** at load
  (`""`, `/`, non-absolute entries are rejected) so a config typo can't
  collapse the boundary — with a defence-in-depth check in the matcher
  too. The classifier is **port-aware** (`…:4443` is never IN_APP).
  `fbcdn.net`/`fbsbx.com` are subresources, allowed unconditionally via
  the `is_main_frame` check, never in the top-level allowlist.
- **External launch requires user intent.** Both popups and main-frame
  navigations only hand a URL to the system browser when the navigation
  carries a real user action: a `target="_blank"` popup checks
  `request.isUserInitiated()`, and a main-frame `EXTERNAL` navigation is
  launched only if it's itself user-driven (`LinkClicked`/`Typed`/
  `FormSubmitted`) or the redirect tail of a link clicked within the last
  few seconds (`external_launch_allowed`). A spontaneous page-driven
  redirect to an external site is suppressed, so a page can't auto-pop
  your browser — while facebook.com link shims (click → 302) still work.
  A `window.open()` `about:blank` popup is still dropped (can't blank the
  chat). With `dev_mode` on, every externalised or suppressed navigation
  is logged.
- **Permission policy** (`is_trusted_permission_origin`) — the narrowest
  boundary. An **explicit host allowlist** (exact match, no subdomain
  wildcard: `messenger.com`, `www.messenger.com`, `facebook.com`,
  `www.facebook.com`, `web.facebook.com`), HTTPS on the default port only.
  `http://…` and `https://…:4443` are rejected. Navigation tolerates
  facebook.com subdomains for login robustness, but capability grants
  deliberately do **not**, so a rogue facebook.com subdomain that somehow
  loaded in-app still can't obtain mic/camera/notifications. Because the
  profile is persistent, the app sets
  `PersistentPermissionsPolicy.AskEveryTime` so a stored grant (notably
  Notifications, a persistent permission) can't outlive and bypass this
  policy across restarts — the Python policy stays authoritative each
  session.
- **Microphone/camera get an extra native confirmation prompt**
  (`_confirm_media_permission`) beyond the origin check. The decision is
  cached **per `(origin, capability)`** — approving the mic on one origin
  does not silently approve the camera, or a different origin — and the
  prompt names the actual host and the actual capability requested
  (`www.facebook.com wants to use your microphone.`), not a fixed
  "microphone and camera". Screen share has an equivalent gate already
  (you actively pick a source); notifications don't get one (lower-stakes).
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

- **Hotkey**: set `hotkey` in `config.json` (`enabled`, `modifiers` of
  ctrl/alt/shift/win, `key`) if Ctrl+Alt+M clashes with something else on
  your system, or set `"enabled": false` to turn it off.
- **Spellcheck**: `config.json` → `spellcheck`. It's off unless matching
  `.bdic` dictionaries are present — drop a `qtwebengine_dictionaries`
  folder (e.g. an `en-US-*.bdic` from a Chromium/Hunspell dictionary set)
  next to `messenger_app.py`, or point `dictionaries_path` at one, then set
  `languages` to match.
- **Developer tools**: `dev_mode` in `config.json` (default `true`) keeps
  "Inspect Element" in the right-click menu and enables the `[nav]`/
  `[popup]` console logging used to tune the allowlists. Set it to `false`
  before handing this to a non-technical user.
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
`"dev_mode": false` in `config.json` before packaging a build meant for
someone else (the class default is already `False`; config is what
turns it on).

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

## Message-only isolation

The whole app is scoped to the messaging surface. Three subsystems do
this, and **all of them are tuned from a single file, `config.json`** —
you shouldn't need to edit any `.py` to adjust behaviour. The loader
(`app_config.py`) is fail-safe: a missing or malformed `config.json`, or
any field of the wrong type, falls back to built-in defaults with a
warning, so a config typo can't brick the app. Each section in
`config.json` has an inline `_note`. What's deliberately **not** in the
config — the trusted host list and allowed URL schemes — stays in
`trusted_origins.py`, so a stray config edit can't widen the security
perimeter.

- **Navigation** (`config.json` → `navigation`) keeps you on the
  facebook.com messaging + auth paths, rewrites the feed to `/messages`,
  and externalises everything else. If a login/auth step ever gets kicked
  out to the browser, the `dev_mode` console log prints the exact path
  (`[nav] external … <url>`) — add it to `facebook_auth_prefixes`.
- **Telemetry blocking** (`config.json` → `telemetry_filter`) drops
  Facebook's background beacon/logging traffic to cut the "handbrake"
  resource drain. It ships **disabled** (`"enabled": false`) — it's an
  explicitly-unverified network mutation, so the safe posture is off until
  you've confirmed login, messaging, attachments, and voice/video calls
  all still work, then flip it on deliberately. `log_blocked` prints each
  blocked request. Only widen `block_rules` for host/path pairs you've
  watched in the log and trust as analytics-only — over-blocking breaks
  chat.
- **Chrome hiding** (`config.json` → `chrome_filter`) lists CSS selectors
  hidden on the messages page (each applied as `display: none !important`)
  and toggles autoplay pausing. The injected script **self-gates to the
  messages surface** — it does nothing on login/checkpoint/settings pages,
  so it can't hide a security notice or control. Autoplay handling is
  **gesture-aware**: video that plays on its own is paused, but video you
  click to play is left alone. Facebook's class names are obfuscated and
  change often, so the seed selectors lean on stable `role`/`aria`
  attributes and are meant to be extended: with `dev_mode` on, Inspect
  Element on whatever you want gone and add a selector.

## Video playback ("Sorry, we're having trouble playing this video")

This is **not a bug in this app** — it's a QtWebEngine build limitation.
Qt WebEngine only decodes MP4/H.264/AAC (what most Facebook video uses)
when it's compiled with `-webengine-proprietary-codecs`, and the official
Qt binaries the PyQt6 wheels are built from ship with those codecs **off**
because H.264/AAC are patent-encumbered. There's no drop-in codec pack:
Qt WebEngine links FFmpeg statically at build time, so the codecs must be
present in the engine itself. WebM/VP8/VP9 play; H.264 doesn't.

You can confirm it on your own machine — with `dev_mode` on (default;
`config.json`), right-click → Inspect Element → Console, and run:

```
document.createElement('video').canPlayType('video/mp4; codecs="avc1.42E01E"')
```

`""` means H.264 is missing (that's the error); `"probably"`/`"maybe"`
means it's present.

Rather than chase a custom codec-enabled Qt build, this app leans into
the message-only design: Facebook video lives under facebook.com content
paths, which now open in your **real browser** (which has the codecs), and
in-app autoplay is paused so the codec error doesn't fire on clips you
weren't going to watch here anyway. If you genuinely need in-app H.264,
the only real fix is a Qt WebEngine built with proprietary codecs (some
Linux distros ship one; on Windows it means a custom build).

## Changelog

**v0.6.3** — Startup-console cleanup and quality-of-life config:
- **Spellcheck is now graceful.** It's enabled only when matching `.bdic`
  dictionaries are actually found (the PyQt6 wheels don't ship them), so
  the "could not find dictionaries / Spellchecking can not be enabled"
  warning is gone — it's either working or silently off. `config.json` →
  `spellcheck` sets the languages and an optional `dictionaries_path`; drop
  a `qtwebengine_dictionaries` folder next to the app (or point at one) to
  turn it on.
- **The global hotkey is now configurable** via `config.json` → `hotkey`
  (`enabled`, `modifiers` of ctrl/alt/shift/win, and `key`). If the default
  Ctrl+Alt+M is already taken by another app (the "could not register
  global hotkey" warning), change it here or disable it. Unknown modifiers
  and malformed keys fall back safely.
- The `Permissions-Policy: Unrecognized feature` and `unload is not
  allowed` console lines are emitted by Facebook's own pages against Qt's
  Chromium and are harmless — not changed.
- Tests: dictionary detection, and hotkey parsing (defaults, custom
  modifiers, unknown-modifier skip, bad-key fallback, disabled) — 71 tests
  total, all passing.

**v0.6.2** — Fixes from a fifth adversarial review; closes the three
freeze-gate items, all on the "what is a trusted navigation" boundary:
- **Navigation hosts are now an explicit exact-host allowlist**, replacing
  subdomain suffix matching. `evil.facebook.com` / `attacker.messenger.com`
  no longer inherit in-app trust; add an exact host (found via the
  `dev_mode` `[nav]` log) only when a real login trace needs it.
- **External browser launch now requires user intent**, for main-frame
  navigations as well as popups. A user-driven navigation, or a redirect
  within a few seconds of one (a link shim), externalises; a spontaneous
  page-driven redirect is suppressed and logged. `external_launch_allowed`
  encodes the rule and is unit-tested.
- **Configured path prefixes are validated** at load: `""`, `/`,
  non-absolute, and malformed entries are rejected with a loud warning and
  fall back to defaults, and custom prefixes are flagged — so a config
  typo can't silently turn the appliance back into a full Facebook
  browser. A defence-in-depth check in the path matcher ignores `""`/`/`
  even if passed directly.
- Fixed the second README DEV_MODE reference the reviewer caught; `dev_mode`
  is fully config-driven now.
- Tests: exact-host regressions, the intent-launch matrix, prefix
  validation, and the matcher-hardening guard — all revert-proven. 62
  tests (plus Qt-guarded media tests), all passing.

Still deliberate (personal-build / distribution, not code): `dev_mode`
ships on for the tuning workflow (flip off in `config.json` before sharing
the app); the dependency lock should be generated on the Windows target;
the PyQt GPL/commercial licensing posture is a distribution-time decision.

**v0.6.1** — Fixes from a fourth adversarial review (privacy/trust boundary):
- **Media consent is now origin + capability specific.** The old single
  cached boolean meant approving the mic auto-approved the camera and every
  other origin; the decision is now keyed to `(scheme, host, port,
  permission_type)` and the prompt names the actual host and capability.
- **Tightened the trust boundary.** Auth paths are matched on a segment
  boundary now (so `/login.evil`, `/helpful`, `/settings-malicious` no
  longer count as in-app); `/help` was dropped from the in-app set. The
  **permission** boundary is now an explicit exact-host allowlist (no
  subdomain wildcard), so a rogue facebook.com subdomain can't obtain
  mic/camera/notifications even though navigation still tolerates
  subdomains for login robustness.
- **External popups require a user gesture.** `newWindowRequested` now
  checks `isUserInitiated()` before launching the system browser, so a
  script can't auto-pop your real browser; automatic in-app opens are
  still allowed.
- **Navigation is port-aware.** `classify_navigation`/`classify_new_window`
  take the port and refuse a non-default HTTPS port for in-app, matching
  the permission policy (one definition of "trusted origin").
- **Persistent permissions can't outlive the policy.** The persistent
  profile now sets `PersistentPermissionsPolicy.AskEveryTime`, so a stored
  Notifications grant can't bypass the Python policy across restarts.
- **Telemetry filter now ships disabled** (`"enabled": false`) — the safe
  posture for an unverified network mutation until the messaging matrix is
  exercised.
- **Cosmetic injection is messages-only and gesture-aware.** The script
  self-gates to facebook.com messaging paths (never touches login/
  checkpoint/settings), and autoplay pausing no longer blocks
  user-initiated playback.
- **Implemented the navigation logging the README promised** — with
  `dev_mode` on, every externalised navigation and dropped popup is logged;
  `dev_mode` itself is now a `config.json` setting.
- Tests: reviewer's loose-auth probes, port matrix, explicit-host
  permission allowlist, media-key keying, injection gate, and config
  fallbacks — all revert-proven. 53 tests total (plus Qt-guarded media
  tests), all passing.

Not changed, with rationale (see the review response): navigation still
tolerates facebook.com subdomains (an explicit nav host list needs a live
login trace to avoid breaking sign-in; the *permission* boundary is the
one that was tightened); `acceptNavigationRequest` still externalises
main-frame departures including redirects (blocking those breaks
facebook.com link shims); `dev_mode` ships **on** because this is a
personal build and the tuning workflow depends on it (flip it off in
`config.json` before handing the app to anyone else); the dependency lock
and the PyQt GPL/commercial licensing posture are distribution-time items,
not code changes — a lock generated in CI here would pin the wrong
(Linux) wheels for a Windows target.

**v0.6** — Re-scoped to a message-only appliance after Meta began folding
web messaging back into `facebook.com/messages` (messenger.com being
retired ~April 2026):
- Replaced the yes/no host trust check with a 4-way, path-aware
  `classify_navigation` (`IN_APP` / `REWRITE_TO_MESSAGES` / `EXTERNAL` /
  `DROP`). facebook.com is now a mixed host: only messaging + auth paths
  stay in-app, the bare feed is bounced to `/messages`, all other
  facebook.com content and every off-site link opens in the real browser
- Fixed the `about:blank` new-window seam flagged in review: popups now go
  through `classify_new_window`, which drops internal-scheme/empty-host
  popups instead of routing them into (and blanking) the main page
- Added a network telemetry/beacon blocker (`request_filter.py`) to cut
  Facebook's background resource drain — conservative, logged, and fully
  revertible
- Added injected CSS/JS (`chrome_filter.py`) to hide residual Facebook
  chrome and pause autoplay video
- Documented the QtWebEngine H.264 codec limitation as the real cause of
  the "trouble playing this video" error, with the message-only design
  (video opens in the real browser) as the fix
- Consolidated every user-tunable knob (facebook path allowlists,
  telemetry block rules, chrome selectors + autoplay toggle) into a single
  `config.json` with a fail-safe loader (`app_config.py`) that falls back
  to built-in defaults on any missing/invalid field — no more editing
  `.py` files to tune behaviour; the security perimeter stays in code
- Tests: navigation-policy matrix, new-window about:blank regression, the
  telemetry matcher, and the config loader (missing/malformed/bad-type
  fallbacks), all revert-proven — 41 tests total, all passing

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
