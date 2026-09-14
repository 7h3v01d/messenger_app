"""
Shared origin/host trust logic for navigation, popups, and browser
permission grants. Two deliberately different policies:

- Navigation policy: which hosts are allowed to load as the app's
  main-frame content (messenger.com, facebook.com, and the CDN/media
  hosts Messenger's own pages route through).
- Permission policy: which origins may be granted microphone/camera/
  screen-share/notification access. Much narrower than navigation —
  CDN/media hosts do NOT get privileged capability grants just
  because Messenger happens to serve content from them, and the
  check is origin-aware (scheme + host + port), not just hostname,
  so http://messenger.com or https://messenger.com:4443 don't
  silently inherit the same trust as https://messenger.com.

Kept Qt-free so it's testable without spinning up WebEngine.
"""

NAVIGATION_HOST_SUFFIXES = (
    "messenger.com",
    "facebook.com",
    "fbcdn.net",
    "fbsbx.com",
)

# Narrower than navigation: only real Messenger/Facebook origins get
# to ask for a microphone, camera, screen share, or to raise native
# notifications. CDN/media-serving hosts are excluded on purpose —
# they shouldn't inherit that authority just because they're allowed
# to appear as navigation targets.
PERMISSION_HOST_SUFFIXES = (
    "messenger.com",
    "facebook.com",
)

# Internal, non-network schemes that make up the app's own chrome —
# not "trusted" in a security sense, just not attacker-reachable.
INTERNAL_SCHEMES = ("about", "qrc")

# Schemes it's reasonable to hand off to the OS's default handler.
EXTERNALLY_OPENABLE_SCHEMES = ("http", "https", "mailto")


def _host_matches(host: str, suffixes) -> bool:
    host = (host or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def is_trusted_navigation_target(scheme: str, host: str) -> bool:
    """True if a top-level navigation to this scheme+host should stay
    inside the app. Internal schemes (about:blank etc.) are allowed;
    everything else must be HTTPS to a Messenger/Facebook-family host.
    Plain http, or a non-network scheme like data:, is rejected even
    if the host looks right — a spoofed/crafted URL with a convincing
    host is exactly the case this needs to catch."""
    scheme = (scheme or "").lower()
    host = (host or "").lower()
    if host == "":
        return scheme in INTERNAL_SCHEMES
    if scheme != "https":
        return False
    return _host_matches(host, NAVIGATION_HOST_SUFFIXES)


def is_trusted_permission_origin(scheme: str, host: str, port) -> bool:
    """True only for a real HTTPS Messenger/Facebook origin on the
    default port. Used to gate microphone/camera/screen-share/
    notification grants — deliberately fails closed on anything
    ambiguous: empty host, non-HTTPS, a non-default port, or a
    CDN/media host that's fine for navigation but shouldn't carry
    this level of privilege."""
    scheme = (scheme or "").lower()
    host = (host or "").lower()
    if not host or scheme != "https":
        return False
    # QUrl.port() returns -1 when no port was explicitly specified,
    # which for https means the default (443) applies.
    if port not in (-1, 443):
        return False
    return _host_matches(host, PERMISSION_HOST_SUFFIXES)


def is_externally_openable(scheme: str) -> bool:
    """True for schemes it's reasonable to hand to the OS's default
    handler (a real browser, a mail client). Rejects arbitrary custom
    schemes so a hostile chat link can't turn this app into an
    unprompted external-protocol launcher."""
    return (scheme or "").lower() in EXTERNALLY_OPENABLE_SCHEMES
