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
