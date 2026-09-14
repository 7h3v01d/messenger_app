"""
Shared "is this a Messenger/Facebook origin" check, used to gate
top-level navigation, popup windows, and browser permission grants.
Kept as a small pure-logic module so it's independently testable
without spinning up Qt/WebEngine.
"""

TRUSTED_HOST_SUFFIXES = (
    "messenger.com",
    "facebook.com",
    "fbcdn.net",
    "fbsbx.com",
)


def is_trusted_host(host: str) -> bool:
    """True for messenger.com/facebook.com/etc. and their subdomains,
    and for empty hosts (about:blank, data:, qrc: — internal, not
    attacker-controlled). Everything else is untrusted."""
    host = (host or "").lower()
    if host == "":
        return True
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in TRUSTED_HOST_SUFFIXES
    )
