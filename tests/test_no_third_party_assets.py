"""
tests/test_no_third_party_assets.py

No front end may fetch anything from a third-party host at page load.

WHY
---
Two of the seven risks in the 2026-10-08 compliance audit are "a page loads
something from someone else's server":

  * fonts — a Munich court made a site pay EUR 100 to a SINGLE visitor for
    leaking their IP address to Google Fonts. The exposure is per visitor, so
    it grows with traffic while the cost of self-hosting does not.
  * analytics and session replay — a recorder switched on by default captures
    keystrokes, which in California is argued as wiretapping at $5,000 per
    session.

`frontend/src/index.css` carried a `@import` of a Google-hosted stylesheet until
2026-10-08, so every admin page view went to Google first. The landing page and
the investor app were already clean. Session replay was never present anywhere,
and this test is what keeps both of those statements true, because
`landing/privacy.html` asserts them to investors:

    "We do not use advertising trackers or sell your information. Our website
     does not set cookies for advertising or analytics."

A policy promise the code can break silently is worse than no promise.

SCOPE
-----
Source only. A test that fetched the built bundle would pass while the source
was wrong, and fail on a machine that had not built.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Every front end that is served to a browser.
ROOTS = [
    Path("frontend/src"),
    Path("frontend/index.html"),
    Path("investor-web/src"),
    Path("investor-web/index.html"),
    Path("landing"),
    Path("mobile/app.json"),
]

SUFFIXES = {".css", ".js", ".jsx", ".ts", ".tsx", ".html", ".json", ".mjs"}

# Hosts a page must never reach at load time. Lower-case; matched as substrings
# against the source, so a commented-out reference fails too — a comment is one
# edit away from being live again.
FORBIDDEN_HOSTS = [
    # fonts
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "use.typekit.net",
    "fast.fonts.net",
    # analytics, tag managers, session replay
    "googletagmanager.com",
    "google-analytics.com",
    "static.hotjar.com",
    "fullstory.com",
    "posthog.com",
    "clarity.ms",
    "cdn.mxpnl.com",
    "cdn.segment.com",
    "cdn.logrocket.io",
    "smartlook.com",
    "browser.sentry-cdn.com",
    # generic script CDNs: fine in a prototype, a third-party request in production
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
]

# Names that mean "a recorder is running", independent of the host it came from.
FORBIDDEN_APIS = [
    "hj(",              # Hotjar
    "FS.identify",      # FullStory
    "posthog.init",
    "mixpanel.init",
    "analytics.load",   # Segment
    "LogRocket.init",
    "gtag(",
    "dataLayer.push",
]


def _sources() -> list[Path]:
    out: list[Path] = []
    for root in ROOTS:
        if not root.exists():
            continue
        if root.is_file():
            out.append(root)
            continue
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUFFIXES and "node_modules" not in p.parts:
                out.append(p)
    return out


def test_there_is_something_to_check():
    """A glob that matches nothing passes every assertion below."""
    files = _sources()
    assert len(files) > 50, f"only found {len(files)} front-end source files — the globs are wrong"
    assert any(p.name == "index.css" for p in files), "frontend/src/index.css must be in scope"


@pytest.mark.parametrize("host", FORBIDDEN_HOSTS)
def test_no_front_end_loads_from(host):
    hits = []
    for p in _sources():
        text = p.read_text(encoding="utf-8", errors="replace").lower()
        if host in text:
            line = next((i for i, ln in enumerate(text.splitlines(), 1) if host in ln), "?")
            hits.append(f"{p.as_posix()}:{line}")
    assert not hits, (
        f"{host} is referenced in {hits}. Every asset must be served from our own "
        f"origin: a third-party fetch leaks the visitor's IP address, and "
        f"landing/privacy.html tells investors we do not do this."
    )


@pytest.mark.parametrize("api", FORBIDDEN_APIS)
def test_no_front_end_calls(api):
    hits = [p.as_posix() for p in _sources()
            if api in p.read_text(encoding="utf-8", errors="replace")]
    assert not hits, (
        f"{api} appears in {hits} — an analytics or session-replay SDK. Session "
        f"replay records keystrokes, which is argued as wiretapping per session."
    )


def test_the_admin_fonts_are_declared_locally():
    """The replacement, pinned — not just the absence of the old import."""
    css = Path("frontend/src/index.css").read_text(encoding="utf-8")
    assert "@font-face" in css, "the fonts must be declared, not imported from elsewhere"
    assert "./assets/fonts/" in css, "the webfont source must be a path on our own origin"
    assert "local('Inter')" in css, (
        "local() first, so an installed copy is used before any file is fetched"
    )
    # and the fallback must survive the woff2 files being absent
    body = css.split("body", 1)[1][:400] if "body" in css else css
    assert "system-ui" in css, (
        "the stack must fall through to a system font, so a missing woff2 file "
        "degrades the typography rather than the layout"
    )
