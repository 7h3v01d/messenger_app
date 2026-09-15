"""
Loads the single user-facing config (config.json) into typed values the
rest of the app consumes.

Design rules:
- Fail-safe, never fatal. A missing or malformed config.json — or any
  individual field of the wrong type — falls back to the built-in DEFAULTS
  (which mirror the shipped config.json) with a warning on stderr. The app
  always starts; a typo in the config can't brick messaging.
- Only BEHAVIOUR knobs live here. The security perimeter (trusted HOSTS,
  allowed URL schemes) stays in trusted_origins.py, deliberately not
  loadable from a file a stray edit could widen.
- No third-party deps; stdlib json only.
"""

import copy
import json
import re
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Built-in defaults == fail-safe fallback. Keep in sync with config.json.
DEFAULTS = {
    "dev_mode": True,
    "navigation": {
        "facebook_messaging_prefixes": ["/messages", "/e2ee", "/t/"],
        "facebook_auth_prefixes": [
            "/login", "/checkpoint", "/recover", "/two_factor", "/2fa",
            "/authentication", "/oauth", "/dialog", "/privacy", "/policies",
            "/cookie", "/consent", "/settings",
        ],
    },
    "telemetry_filter": {
        # Ships OFF: this is an explicitly-unverified network mutation, so
        # the safe release posture is disabled-by-default until the user has
        # exercised login/messaging/attachments/calls/reconnect and turned
        # it on deliberately. Set "enabled": true after that.
        "enabled": False,
        "log_blocked": True,
        "block_rules": [
            {"host": "facebook.com", "path": "/ajax/bz"},
            {"host": "facebook.com", "path": "/tr/"},
            {"host": "facebook.com", "path": "/tr?"},
            {"host": "facebook.com", "path": "/ajax/bnzai"},
            {"host": "facebook.com", "path": "/privacy_sandbox"},
        ],
    },
    "chrome_filter": {
        "hide_selectors": [
            '[role="banner"]',
            '[aria-label="Facebook"][role="navigation"]',
        ],
        "pause_autoplay": True,
    },
    "spellcheck": {
        # Enabled only if matching .bdic dictionaries are actually present
        # (the PyQt6 wheels don't ship them), so it's either working or
        # silent — never noisy-and-broken.
        "languages": ["en-US"],
        "dictionaries_path": None,
    },
    "hotkey": {
        # Global show/hide combo. Change these if the default clashes with
        # another app (the symptom is a "could not register global hotkey"
        # warning at startup). modifiers: any of ctrl/alt/shift/win.
        "enabled": True,
        "modifiers": ["ctrl", "alt"],
        "key": "M",
    },
}


def _warn(msg: str) -> None:
    print(f"[config] {msg}", file=sys.stderr)


def load_config(path=CONFIG_PATH) -> dict:
    """Return the merged config dict, falling back to DEFAULTS on any
    problem. Never raises."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        _warn(f"{path} not found; using built-in defaults")
        return cfg
    except (OSError, ValueError) as e:
        _warn(f"could not read {path} ({e}); using built-in defaults")
        return cfg

    if not isinstance(raw, dict):
        _warn("top-level config is not an object; using built-in defaults")
        return cfg

    for key, default in DEFAULTS.items():
        value = raw.get(key, default)
        if not isinstance(default, dict):
            # Scalar top-level key (e.g. dev_mode): adopt only if the type
            # matches the default's, else keep the default.
            if isinstance(value, type(default)):
                cfg[key] = value
            elif key in raw:
                _warn(f"'{key}' has wrong type; using default")
            continue
        if not isinstance(value, dict):
            _warn(f"section '{key}' is not an object; using its defaults")
            continue
        merged = dict(default)
        # Only adopt keys we know about (ignores _note / _README etc.).
        for k in default:
            if k in value:
                merged[k] = value[k]
        cfg[key] = merged
    return cfg


# --- typed accessors: each coerces and falls back per-field -------------

def _as_str_list(value, fallback):
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value)
    return list(fallback)


# A path prefix must be absolute and have at least one character after the
# leading slash, using only characters legal in a URL path. This rejects
# the dangerous entries "", "/", " ", and non-absolute strings — any of
# which would match every path and collapse the message-only boundary back
# into a full Facebook browser.
_PREFIX_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+$")


def _valid_prefixes(value, fallback, field_name):
    """Coerce a config prefix list to str, drop any entry that fails the
    safety format (with a loud warning), and fall back to defaults if
    nothing valid remains. Also flags entries not in the shipped defaults
    so custom overrides are visible."""
    raw = _as_str_list(value, fallback)
    valid = []
    for entry in raw:
        if _PREFIX_RE.match(entry):
            valid.append(entry)
            if entry not in fallback:
                _warn(f"{field_name}: using custom prefix {entry!r} "
                      f"(not in shipped defaults)")
        else:
            _warn(f"{field_name}: rejecting unsafe prefix {entry!r} — a "
                  f"prefix must be absolute and non-empty (not \"\" or \"/\")")
    if not valid:
        _warn(f"{field_name}: no valid prefixes; using defaults")
        return list(fallback)
    return valid


def dev_mode_enabled(cfg: dict) -> bool:
    """Whether developer features are on: right-click Inspect Element, plus
    console logging of externalised navigations and dropped popups (the
    facility for discovering login paths that need adding to config)."""
    return bool(cfg.get("dev_mode", DEFAULTS["dev_mode"]))


def navigation_prefixes(cfg: dict):
    """(messaging_prefixes, auth_prefixes) as tuples of str, each validated
    so a dangerous entry (\"\", \"/\", non-absolute) can't widen the trust
    boundary."""
    nav = cfg.get("navigation", {})
    d = DEFAULTS["navigation"]
    msg = _valid_prefixes(nav.get("facebook_messaging_prefixes"),
                          d["facebook_messaging_prefixes"],
                          "facebook_messaging_prefixes")
    auth = _valid_prefixes(nav.get("facebook_auth_prefixes"),
                           d["facebook_auth_prefixes"],
                           "facebook_auth_prefixes")
    return tuple(msg), tuple(auth)


def telemetry_settings(cfg: dict):
    """(enabled: bool, log_blocked: bool, rules: tuple[(host, path), ...])."""
    t = cfg.get("telemetry_filter", {})
    enabled = bool(t.get("enabled", True))
    log_blocked = bool(t.get("log_blocked", True))
    rules = []
    for r in (t.get("block_rules") or []):
        if isinstance(r, dict) and isinstance(r.get("path"), str):
            rules.append((str(r.get("host", "")), r["path"]))
    if not rules:
        # Empty or all-invalid -> fall back to defaults rather than a
        # silently do-nothing filter.
        rules = [(r["host"], r["path"])
                 for r in DEFAULTS["telemetry_filter"]["block_rules"]]
    return enabled, log_blocked, tuple(rules)


def chrome_settings(cfg: dict):
    """(hide_selectors: tuple[str], pause_autoplay: bool)."""
    c = cfg.get("chrome_filter", {})
    selectors = _as_str_list(c.get("hide_selectors"),
                             DEFAULTS["chrome_filter"]["hide_selectors"])
    pause = bool(c.get("pause_autoplay", True))
    return tuple(selectors), pause


def spellcheck_settings(cfg: dict):
    """(languages: tuple[str], dictionaries_path: str|None)."""
    s = cfg.get("spellcheck", {})
    langs = _as_str_list(s.get("languages"),
                         DEFAULTS["spellcheck"]["languages"])
    path = s.get("dictionaries_path")
    if path is not None and not isinstance(path, str):
        _warn("spellcheck.dictionaries_path must be a string or null; ignoring")
        path = None
    return tuple(langs), (path or None)


def available_spellcheck_languages(languages, search_dirs):
    """Return the subset of `languages` that have a matching `<lang>*.bdic`
    dictionary in any of search_dirs. Used to enable spellcheck only when
    it can actually work (Qt WebEngine needs the .bdic files, which the
    PyQt6 wheels don't ship). Filesystem-based but easily testable with
    temp dirs."""
    found = []
    dirs = [Path(d) for d in search_dirs if d]
    for lang in languages:
        for d in dirs:
            try:
                if d.is_dir() and any(d.glob(f"{lang}*.bdic")):
                    found.append(lang)
                    break
            except OSError:
                continue
    return found


# Modifier name -> Win32 bit. Mirrors the constants in global_hotkey.py;
# duplicated here so this module stays Qt/Windows-free and unit-testable.
_MODIFIER_BITS = {
    "alt": 0x0001,
    "ctrl": 0x0002, "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008, "super": 0x0008, "meta": 0x0008, "cmd": 0x0008,
}
_MOD_NOREPEAT = 0x4000
_DEFAULT_MODS = _MODIFIER_BITS["ctrl"] | _MODIFIER_BITS["alt"]


def hotkey_settings(cfg: dict):
    """(enabled: bool, modifiers_mask: int, key: str) for the global
    show/hide hotkey. Unknown modifiers are warned-and-skipped; an empty
    or all-invalid modifier set falls back to Ctrl+Alt; a non-single-char
    key falls back to the default. NOREPEAT is always OR'd in."""
    h = cfg.get("hotkey", {})
    d = DEFAULTS["hotkey"]
    enabled = bool(h.get("enabled", d["enabled"]))

    names = h.get("modifiers", d["modifiers"])
    if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
        _warn("hotkey.modifiers must be a list of strings; using default")
        names = d["modifiers"]

    key = h.get("key", d["key"])
    if not (isinstance(key, str) and len(key) == 1 and key.isascii() and key.isalnum()):
        # Must be an ASCII A-Z/0-9 char: those map directly to Win32
        # virtual-key codes via ord(key.upper()). A non-ASCII char like
        # "ß" (upper() -> "SS") or a full-width "Ａ" would mis-register or
        # crash RegisterHotKey, so fall back to the default.
        _warn("hotkey.key must be a single ASCII letter or digit; using default")
        key = d["key"]

    mods = 0
    for n in names:
        bit = _MODIFIER_BITS.get(n.strip().lower())
        if bit:
            mods |= bit
        else:
            _warn(f"hotkey: unknown modifier {n!r}, ignoring")
    if mods == 0:
        _warn("hotkey: no valid modifiers; using Ctrl+Alt")
        mods = _DEFAULT_MODS

    return enabled, mods | _MOD_NOREPEAT, key.upper()
