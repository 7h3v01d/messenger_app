"""
Shared origin/host/path trust logic for navigation, popups, and browser
permission grants.

This app used to point at messenger.com, a host whose only surfaces were
the login page and the chat UI — so a simple host allowlist was enough.
That is no longer true. Meta is retiring the standalone Messenger web
product (~April 2026): after login, desktop web messaging is served from
facebook.com/messages. So the chat now lives on facebook.com — right
next to the feed, Watch, Reels, Marketplace and everything else this app
exists to stay out of.

That forces THREE deliberately different policies:

- Navigation policy (classify_navigation): a top-level URL is sorted
  into one of four actions rather than a yes/no trust check —
    * IN_APP              stay inside this window
    * REWRITE_TO_MESSAGES bare facebook.com/ (the feed) is bounced to
                          the messages surface instead of loading, so
                          you never see the feed even as a login landing
    * EXTERNAL            hand to the OS default browser (which also, as
                          a side effect, is where Facebook video actually
                          plays — QtWebEngine ships without the H.264/AAC
                          codecs Facebook uses)
    * DROP               silently ignore (unsafe scheme, hostile popup)
  messenger.com stays in-app wholesale (all-messaging host). facebook.com
  is a MIXED host: only the messaging + auth PATHS stay in-app; all other
  facebook.com paths go external.

- Permission policy (is_trusted_permission_origin): which origins may be
  granted microphone/camera/screen-share/notification access. The
  narrowest boundary — an EXPLICIT host allowlist (exact match, no
  subdomain wildcard), https + default port only. Navigation tolerates
  facebook.com subdomains for login robustness; capability grants do not.

- External-scheme policy (is_externally_openable): which schemes are
  reasonable to hand to the OS at all.

Kept Qt-free so it's testable without spinning up WebEngine.
"""

# --- Navigation actions ------------------------------------------------

NAV_IN_APP = "in_app"
NAV_REWRITE_TO_MESSAGES = "rewrite_to_messages"
NAV_EXTERNAL = "external"
NAV_DROP = "drop"

# --- Host policy -------------------------------------------------------

# All-messaging host: every messenger.com surface (login + chat) may stay
# in the app.
MESSAGING_HOST_SUFFIXES = (
    "messenger.com",
)

# Mixed host: the chat surface now lives at facebook.com/messages, but so
# does everything this app avoids. NOT trusted wholesale — only specific
# path prefixes (below) stay in-app.
MIXED_HOST_SUFFIXES = (
    "facebook.com",
)

# Permission grants (mic/camera/screen-share/notifications) are the sharp
# end of the trust boundary, so — unlike navigation, which tolerates
# subdomains for login robustness — these are gated to an EXPLICIT host
# allowlist, exact match only. A rogue facebook.com subdomain that somehow
# loaded in-app therefore still cannot obtain a capability grant. Add a
# host here only when a real login/call trace proves it's needed.
PERMISSION_ALLOWED_HOSTS = frozenset({
    "messenger.com",
    "www.messenger.com",
    "facebook.com",
    "www.facebook.com",
    "web.facebook.com",
})

# Internal, non-network schemes that make up the app's own chrome — not
# "trusted" in a security sense, just not attacker-reachable.
INTERNAL_SCHEMES = ("about", "qrc")

# Schemes it's reasonable to hand off to the OS's default handler.
EXTERNALLY_OPENABLE_SCHEMES = ("http", "https", "mailto")

# Default HTTPS port sentinels: QUrl.port() returns -1 when unspecified,
# which for https means 443. Any other port is NOT the default and is
# never treated as an in-app / permission-trusted origin.
DEFAULT_HTTPS_PORTS = (-1, 443)

# --- facebook.com path policy -----------------------------------------
#
# TUNE THESE to your own login flow. messenger_app logs every externalised
# navigation when dev_mode is on, so if a login/auth step gets kicked out
# to the browser you can see its exact path and add it here.
#
# Because facebook.com is a mixed host, the split is:
#   messaging paths -> IN_APP   (segment-boundary match: /messages matches
#                                /messages and /messages/t/1, NOT /messagesX)
#   auth paths      -> IN_APP   (ALSO segment-boundary now — /login matches
#                                /login and /login/..., but NOT /login.evil,
#                                /helpful, /settings-malicious, etc. If your
#                                login uses a form like /login.php that gets
#                                externalised on first run, the dev log will
#                                show it and you add that exact path here.)
#   bare "/" feed   -> REWRITE_TO_MESSAGES
#   anything else   -> EXTERNAL

FACEBOOK_MESSAGING_PREFIXES = (
    "/messages",   # facebook.com/messages, /messages/t/<id>, /messages/e2ee/...
    "/e2ee",       # encrypted-thread routes
    "/t/",         # legacy thread path, in case it still resolves
)

FACEBOOK_AUTH_PREFIXES = (
    "/login",
    "/checkpoint",
    "/recover",
    "/two_factor",
    "/2fa",
    "/authentication",
    "/oauth",
    "/dialog",          # OAuth/login dialogs
    # Account / consent surfaces reached during login. NOT auth endpoints
    # in the strict sense, but needed in-app so a consent/cookie/2FA step
    # isn't flung to a cookieless external browser. /help was removed — it
    # isn't part of any auth flow and can open externally like other links.
    "/privacy",
    "/policies",
    "/cookie",
    "/consent",
    "/settings",        # e.g. Accounts Center / account linking
)


def _host_matches(host, suffixes) -> bool:
    host = (host or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in suffixes)


def _normalise_path(path) -> str:
    p = path or "/"
    if not p.startswith("/"):
        p = "/" + p
    return p


def _path_has_prefix_boundary(path, prefixes) -> bool:
    """Prefix match on a path SEGMENT boundary: '/messages' matches
    '/messages' and '/messages/t/1' but not '/messagesX'."""
    p = _normalise_path(path)
    for pre in prefixes:
        if pre.endswith("/"):
            if p == pre.rstrip("/") or p.startswith(pre):
                return True
        else:
            if p == pre or p.startswith(pre + "/"):
                return True
    return False


def _is_default_https_port(port) -> bool:
    # None == "caller didn't supply a port" (e.g. a unit test); treat as
    # default so pure-logic callers stay simple. A real URL passes
    # QUrl.port(), which is -1 (unspecified -> 443) or an explicit number.
    return port is None or port in DEFAULT_HTTPS_PORTS


def classify_navigation(scheme: str, host: str, path: str, port=None,
                        messaging_prefixes=FACEBOOK_MESSAGING_PREFIXES,
                        auth_prefixes=FACEBOOK_AUTH_PREFIXES) -> str:
    """Sort a *top-level* navigation into one of the NAV_* actions.

    The facebook.com messaging/auth path allowlists are passed in so they
    can be tuned via config.json (see app_config); they default to the
    module constants, keeping the function pure and testable.

    Used by MessengerPage.acceptNavigationRequest for main-frame loads.
    Fails closed: an empty host with a non-internal scheme, any non-https
    network scheme that isn't OS-openable, and any non-default HTTPS port
    are kept out of the app rather than loaded in this Messenger-branded
    window.
    """
    scheme = (scheme or "").lower()
    host = (host or "").lower()

    if host == "":
        # about:blank / qrc: legitimate as a real top-level load (initial
        # blank page, internal transitions). Allowed here; the new-window
        # path (classify_new_window) refuses these so a popup can't use
        # about:blank to blank out the main view.
        return NAV_IN_APP if scheme in INTERNAL_SCHEMES else NAV_DROP

    if scheme != "https":
        # Never load plain http or an exotic scheme as top-level content.
        return NAV_EXTERNAL if scheme in EXTERNALLY_OPENABLE_SCHEMES else NAV_DROP

    if not _is_default_https_port(port):
        # https on a non-default port (e.g. :4443) is never an in-app
        # origin — matching the permission policy so there's one
        # definition of "trusted origin". Hand it to the browser.
        return NAV_EXTERNAL

    if _host_matches(host, MESSAGING_HOST_SUFFIXES):
        return NAV_IN_APP  # messenger.com is all-messaging

    if _host_matches(host, MIXED_HOST_SUFFIXES):
        if _path_has_prefix_boundary(path, messaging_prefixes):
            return NAV_IN_APP
        if _path_has_prefix_boundary(path, auth_prefixes):
            return NAV_IN_APP
        if _normalise_path(path) == "/":
            # Bare facebook.com — the feed. Don't show it; bounce to the
            # messages surface (this is a common post-login landing).
            return NAV_REWRITE_TO_MESSAGES
        # Any other facebook.com content: Watch, Reels, a shared post, a
        # profile, Marketplace, a video -> real browser.
        return NAV_EXTERNAL

    # Some other https host (a link someone sent in chat) -> real browser.
    return NAV_EXTERNAL


def classify_new_window(scheme: str, host: str, path: str, port=None,
                        messaging_prefixes=FACEBOOK_MESSAGING_PREFIXES,
                        auth_prefixes=FACEBOOK_AUTH_PREFIXES) -> str:
    """Like classify_navigation, but for target=_blank / window.open
    popups. An internal-scheme, empty-host popup (e.g. window.open()'s
    about:blank) must NEVER be routed into the main page — that would
    blank out the live chat. Everything else follows the same rules.

    NOTE: this decides *where* a popup would go; the caller
    (messenger_app) additionally requires a genuine user gesture before it
    will EXTERNAL-launch the system browser, so a script can't auto-pop
    your real browser.
    """
    if (host or "") == "" and (scheme or "").lower() in INTERNAL_SCHEMES:
        return NAV_DROP
    return classify_navigation(scheme, host, path, port,
                               messaging_prefixes, auth_prefixes)


def is_trusted_permission_origin(scheme: str, host: str, port) -> bool:
    """True only for a real HTTPS origin on an EXPLICIT host allowlist
    (PERMISSION_ALLOWED_HOSTS), on the default port. Gates microphone/
    camera/screen-share/notification grants — the narrowest boundary in
    the app, and exact-host (no subdomain wildcard) on purpose, so a rogue
    facebook.com subdomain can't obtain a capability grant. Fails closed
    on empty host, non-HTTPS, or a non-default port.
    """
    scheme = (scheme or "").lower()
    host = (host or "").lower()
    if not host or scheme != "https":
        return False
    if port not in DEFAULT_HTTPS_PORTS:
        return False
    return host in PERMISSION_ALLOWED_HOSTS


def is_externally_openable(scheme: str) -> bool:
    """True for schemes it's reasonable to hand to the OS's default
    handler (a real browser, a mail client). Rejects arbitrary custom
    schemes so a hostile chat link can't turn this app into an unprompted
    external-protocol launcher."""
    return (scheme or "").lower() in EXTERNALLY_OPENABLE_SCHEMES
