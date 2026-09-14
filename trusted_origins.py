"""
Shared origin/host trust logic for navigation, popups, and browser
permission grants. Two deliberately different policies:

- Navigation policy: which hosts are allowed to load as the app's
  main-frame (top-level) content. Deliberately narrow — just
  messenger.com and facebook.com. CDN/media hosts (fbcdn.net,
  fbsbx.com) are NOT included here: acceptNavigationRequest() already
  allows all non-main-frame traffic unconditionally, so Messenger's
  own images/scripts/media never needed these domains in the
  top-level allowlist in the first place. Keeping them out means a
  CDN URL can never replace the entire contents of this
  Messenger-branded native window.
- Permission policy: which origins may be granted microphone/camera/
  screen-share/notification access. Even narrower than navigation,
  and origin-aware (scheme + host + port), not just hostname, so
  http://messenger.com or https://messenger.com:4443 don't silently
  inherit the same trust as https://messenger.com.

Kept Qt-free so it's testable without spinning up WebEngine.
"""

# Deliberately narrow: only real top-level Messenger/Facebook pages.
# CDN/media hosts are excluded — see module docstring.
NAVIGATION_HOST_SUFFIXES = (
    "messenger.com",
    "facebook.com",
)

# Narrower still: only real Messenger/Facebook origins get to ask for
# a microphone, camera, screen share, or to raise native
# notifications.
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
    everything else must be HTTPS to messenger.com/facebook.com (or a
    subdomain). Plain http, or a non-network scheme like data:, is
    rejected even if the host looks right — a spoofed/crafted URL
    with a convincing host is exactly the case this needs to catch."""
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
    ambiguous: empty host, non-HTTPS, or a non-default port."""
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
