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
  granted microphone/camera/screen-share/notification access. An EXPLICIT
  host allowlist (exact match, no subdomain wildcard), https + default
  port only. Navigation uses its own exact-host allowlists too (see
  below); this one is the tightest — it also gates the capability grants.

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
#
# Navigation now uses EXPLICIT exact-host allowlists, not subdomain suffix
# matching. A compromised or abandoned Meta subdomain must not inherit the
# app's "this is Messenger" trust just because it ends in facebook.com.
# If a real login trace later shows another host is genuinely needed, add
# that exact host here (the dev_mode [nav] log prints the host that got
# externalised, so you can see what to add).

# messenger.com hosts are all-messaging (login + chat) -> IN_APP wholesale.
NAVIGATION_MESSENGER_HOSTS = frozenset({
    "messenger.com",
    "www.messenger.com",
})

# facebook.com hosts are MIXED: the chat surface lives here alongside the
# feed/Watch/etc, so only the messaging/auth PATHS (below) stay in-app.
NAVIGATION_FACEBOOK_HOSTS = frozenset({
    "facebook.com",
    "www.facebook.com",
    "web.facebook.com",
})

# Permission grants (mic/camera/screen-share/notifications) are the sharp
# end of the trust boundary — an EXPLICIT host allowlist, exact match. A
# rogue facebook.com subdomain that somehow loaded in-app still cannot
# obtain a capability grant. Add a host here only when a real login/call
# trace proves it's needed.
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


def _normalise_path(path) -> str:
    p = path or "/"
    if not p.startswith("/"):
        p = "/" + p
    return p


def _path_has_prefix_boundary(path, prefixes) -> bool:
    """Prefix match on a path SEGMENT boundary: '/messages' matches
    '/messages' and '/messages/t/1' but not '/messagesX'.

    Defence in depth: a prefix of "" or "/" is IGNORED, never matched.
    Those would make every path match (turning the appliance back into a
    full Facebook browser); app_config already rejects them at load time,
    and this second check means even a prefix passed directly to the
    classifier can't collapse the boundary.
    """
    p = _normalise_path(path)
    for pre in prefixes:
        if pre in ("", "/"):
            continue
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

    if _is_default_https_port(port) is False:
        # https on a non-default port (e.g. :4443) is never an in-app
        # origin — matching the permission policy so there's one
        # definition of "trusted origin". Hand it to the browser.
        return NAV_EXTERNAL

    if host in NAVIGATION_MESSENGER_HOSTS:
        return NAV_IN_APP  # messenger.com hosts are all-messaging

    if host in NAVIGATION_FACEBOOK_HOSTS:
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

    # Any other host — including an unlisted facebook.com/messenger.com
    # subdomain — is not trusted for in-app navigation. Hand it to the
    # browser (dev_mode logs the host so you can add it if login needs it).
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


# How long a genuine user navigation "authorises" a following external
# launch. A user clicks a link -> facebook.com issues a shim/302 redirect
# to the real URL a moment later; that redirect should externalise. A
# spontaneous page-driven redirect with no recent user action should not.
EXTERNAL_LAUNCH_WINDOW_MS = 3000


def external_launch_allowed(is_user_nav: bool, ms_since_user_nav,
                            window_ms: int = EXTERNAL_LAUNCH_WINDOW_MS) -> bool:
    """Decide whether an EXTERNAL-classified main-frame navigation may
    launch the system browser.

    Preserves user intent across a navigation chain rather than choosing
    between "externalise every redirect" (a page can auto-launch your
    browser) and "externalise nothing" (breaks facebook.com link shims):

      - a navigation that is itself user-driven (a link click, form submit,
        typed URL) -> allowed;
      - a redirect / other page-driven navigation -> allowed ONLY if a
        user-driven navigation happened within window_ms (i.e. it's the
        tail of a link the user just clicked).

    ms_since_user_nav is None when no user navigation has happened yet.
    """
    if is_user_nav:
        return True
    if ms_since_user_nav is None:
        return False
    return ms_since_user_nav <= window_ms
