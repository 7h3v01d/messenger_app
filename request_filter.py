"""
Network-level request filter.

Facebook's page code fires a steady stream of background beacons: batched
logging, impression/telemetry pixels, error/QoS reporting. On the feed
that's constant; even on the messages surface it's a meaningful chunk of
the "why is this pinning my machine" background chatter. Blocking the
obvious analytics endpoints trims that without touching messaging.

IMPORTANT / HONEST CAVEAT
-------------------------
Messenger is heavily GraphQL + MQTT driven, and Facebook reuses generic
paths (/ajax/*) for both telemetry AND load-bearing traffic. Over-blocking
silently breaks chat. The default BLOCK list below is deliberately tiny and
limited to endpoints that are analytics-only as far as I can tell — but I
could NOT verify this against a live authenticated session, so:

  * Run with LOG_BLOCKED = True at first and watch the console.
  * If messaging misbehaves, set ENABLED = False (kills the filter
    entirely) or trim BLOCK_RULES, and confirm chat recovers. That's your
    revert path.
  * Only widen BLOCK_RULES for hosts+paths you've watched in the log and
    are confident are pure analytics.

Matching is a pure function (should_block) so it's unit-testable without
WebEngine.
"""

import sys

IS_QT = "PyQt6" in sys.modules or True  # import guarded below

# Master switch. Flip to False to disable the filter completely (your
# revert path if anything messaging-related breaks).
ENABLED = True

# Print each blocked request to the console. Leave True until you trust
# the rules against your own live session.
LOG_BLOCKED = True

# (host_suffix, path_prefix) pairs to BLOCK. Both must match. A host of ""
# matches any host. Kept intentionally minimal — see the module caveat.
#
# These target Facebook's batched-logging / pixel telemetry, not chat:
BLOCK_RULES = (
    ("facebook.com", "/ajax/bz"),        # batched client logging/telemetry
    ("facebook.com", "/tr/"),            # tracking pixel
    ("facebook.com", "/tr?"),            # tracking pixel (query form)
    ("facebook.com", "/ajax/bnzai"),     # analytics beacon
    ("facebook.com", "/privacy_sandbox"),# ads/measurement sandbox pings
)


def should_block(host: str, path: str, rules=BLOCK_RULES) -> bool:
    """True if (host, path) matches any (host_suffix, path_prefix) rule.

    host_suffix "" matches any host; otherwise host must equal it or be a
    subdomain of it. path_prefix is a plain string-prefix match on the
    path (query already folded into path by the caller when relevant).
    """
    host = (host or "").lower()
    path = path or "/"
    for host_suffix, path_prefix in rules:
        if host_suffix:
            hs = host_suffix.lower()
            if not (host == hs or host.endswith("." + hs)):
                continue
        if path.startswith(path_prefix):
            return True
    return False


try:
    from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor

    class TelemetryBlocker(QWebEngineUrlRequestInterceptor):
        """Drops requests matching BLOCK_RULES. Installed profile-wide via
        profile.setUrlRequestInterceptor(). interceptRequest runs on an
        I/O thread and must stay cheap and side-effect-free beyond the
        block decision — hence a pure matcher and, at most, a print."""

        def __init__(self, rules=BLOCK_RULES, enabled=ENABLED,
                     log_blocked=LOG_BLOCKED, parent=None):
            super().__init__(parent)
            self._rules = rules
            self._enabled = enabled
            self._log = log_blocked

        def interceptRequest(self, info):
            if not self._enabled:
                return
            url = info.requestUrl()
            # Fold the query onto the path so /tr? pixel forms match too.
            path = url.path()
            if url.hasQuery():
                path = path + "?" + url.query()
            if should_block(url.host(), path, self._rules):
                info.block(True)
                if self._log:
                    print(f"[filter] blocked {url.host()}{url.path()}")

except ImportError:  # pragma: no cover - lets the pure matcher be tested headless
    TelemetryBlocker = None
