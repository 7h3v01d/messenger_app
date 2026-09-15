"""
Cosmetic filtering: hide the residual Facebook chrome on the MESSAGES page,
and stop feed-style autoplay video — without breaking user-initiated
playback and without touching login/checkpoint/settings pages.

Honest limitations:

1. Facebook's DOM uses obfuscated, frequently-changing class names, so any
   CSS that targets them will rot. The seed selectors lean on ROLE / ARIA
   attributes instead, which are far more stable, but "hide all ads
   forever" is not a promise anyone can keep against Facebook's markup.
   Extend it: run with dev_mode on, Inspect Element on the junk you want
   gone, find a stable attribute, and add a selector to config.json's
   chrome_filter.hide_selectors.

2. The injected script SELF-GATES to the messages surface (see the guard
   built in build_injection_js): it does nothing unless the current page
   is facebook.com on a messaging path. So it never hides chrome or fiddles
   with video on a login/security/recovery page, where hiding a notice or
   control could be harmful.

Autoplay handling is GESTURE-AWARE: it neutralises the autoplay attribute
and pauses video that starts on its own, but leaves video the user
actually clicked to play alone (tracked via a recent-gesture timestamp).
"""

# --- pure helpers (unit-testable) --------------------------------------

def css_from_selectors(selectors) -> str:
    """Build a hide-CSS blob from a list of selectors (config.json's
    chrome_filter.hide_selectors). Each becomes
    `selector { display: none !important; }`."""
    return "\n".join(
        f"{s} {{ display: none !important; }}" for s in selectors
    )


def _js_string_literal(text: str) -> str:
    """Encode a Python string as a safe JS double-quoted string literal."""
    escaped = (
        text.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
    )
    return '"' + escaped + '"'


def _js_prefix_array(prefixes) -> str:
    return "[" + ",".join(_js_string_literal(p) for p in prefixes) + "]"


# Default messaging prefixes used for the surface gate if the caller
# doesn't pass its own (kept in sync with trusted_origins).
DEFAULT_MESSAGING_PREFIXES = ("/messages", "/e2ee", "/t/")


def build_injection_js(hide_css: str,
                       pause_autoplay: bool = True,
                       messaging_prefixes=DEFAULT_MESSAGING_PREFIXES) -> str:
    """Return the JS injected at document-ready. It:
      1. bails immediately unless the page is a facebook.com messaging
         surface (so login/checkpoint/settings are never touched);
      2. injects the hide-CSS stylesheet;
      3. optionally installs the gesture-aware autoplay tamer.
    """
    css_literal = _js_string_literal(hide_css)
    prefixes_literal = _js_prefix_array(messaging_prefixes)

    autoplay_block = _AUTOPLAY_TAMER_JS if pause_autoplay else ""

    return (
        "(function(){\n"
        "  // --- surface gate: messages-only ---\n"
        "  var host = (location.hostname || '').toLowerCase();\n"
        "  var path = location.pathname || '/';\n"
        "  var onFacebook = host === 'facebook.com' || host.endsWith('.facebook.com');\n"
        f" var PREFIXES = {prefixes_literal};\n"
        "  function onMessages(){\n"
        "    if (!onFacebook) return false;\n"
        "    for (var i=0;i<PREFIXES.length;i++){\n"
        "      var pre = PREFIXES[i];\n"
        "      if (path === pre) return true;\n"
        "      if (path.indexOf(pre + '/') === 0) return true;\n"
        "      if (pre.charAt(pre.length-1) === '/' && path.indexOf(pre) === 0) return true;\n"
        "    }\n"
        "    return false;\n"
        "  }\n"
        "  if (!onMessages()) return;\n"
        "\n"
        "  // --- hide chrome ---\n"
        "  var s = document.createElement('style');\n"
        "  s.type = 'text/css';\n"
        f" s.appendChild(document.createTextNode({css_literal}));\n"
        "  (document.head || document.documentElement).appendChild(s);\n"
        f"{autoplay_block}"
        "})();\n"
    )


# Gesture-aware autoplay tamer. Pauses video that plays WITHOUT a recent
# user gesture (i.e. autoplay); leaves user-clicked playback running.
_AUTOPLAY_TAMER_JS = """
  var lastGesture = 0;
  ['pointerdown','keydown','click','touchstart'].forEach(function(evt){
    document.addEventListener(evt, function(){ lastGesture = Date.now(); }, true);
  });
  function tame(v){
    try {
      v.autoplay = false;
      v.removeAttribute('autoplay');
      // Only pause if it started on its own — a click within the last
      // second means the user asked for it, so leave it alone.
      if (!v.paused && (Date.now() - lastGesture) > 1000) v.pause();
    } catch (e) {}
  }
  document.querySelectorAll('video').forEach(function(v){
    v.autoplay = false; v.removeAttribute('autoplay');
    if (!v.paused && (Date.now() - lastGesture) > 1000) { try { v.pause(); } catch(e){} }
  });
  document.addEventListener('play', function (e) {
    if (e.target && e.target.tagName === 'VIDEO') tame(e.target);
  }, true);
  new MutationObserver(function (muts) {
    for (var mi=0; mi<muts.length; mi++) {
      var added = muts[mi].addedNodes;
      for (var ni=0; ni<added.length; ni++) {
        var n = added[ni];
        if (n.tagName === 'VIDEO') { n.autoplay = false; n.removeAttribute('autoplay'); }
        else if (n.querySelectorAll) {
          n.querySelectorAll('video').forEach(function(v){ v.autoplay=false; v.removeAttribute('autoplay'); });
        }
      }
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
"""
