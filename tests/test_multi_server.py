"""
tests/test_multi_server.py

Several backends, one front end — and the ways that can go quietly wrong.

THE RISK THIS FEATURE CREATES
-----------------------------
One screen that can talk to a demo box and a live box is one screen on which
you can do something to the wrong account. Three failure modes follow, and
each has a test here:

  1. CACHE BLEED. React Query caches by key. A key without the server id is
     shared between servers, so switching shows the previous server's numbers
     under the new server's name — a confident, silent lie about a live
     account. Every key must be namespaced.

  2. CROSS-SERVER TOKENS. Each backend signs with its own `JWT_SECRET_KEY`, so
     a live token is meaningless on demo. Sharing a secret so they interoperate
     would make a demo compromise a live compromise, so tokens are per server
     and the refresh path must follow the server that issued them.

  3. MISLABELLING. A registry row is a list someone typed. A row labelled
     "demo" pointing at live is a mistake the client cannot prevent — but the
     SERVER can state its own identity and be checked against it.

The front end has no JS test runner in this project, so these are
source-contract tests, the same approach `test_slot_risk_parity.py` already
uses for the slot editor. They are not a substitute for the behaviour being
right; they are a guard against it being silently undone.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

API = Path("frontend/src/services/api.js")
SERVERS = Path("frontend/src/services/servers.js")
SWITCHER = Path("frontend/src/components/ServerSwitcher.jsx")
APP = Path("frontend/src/App.jsx")
CADDY = Path("deploy/Caddyfile.multi")


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ── the identity endpoint ───────────────────────────────────────────────────

def test_the_server_states_its_own_identity():
    """The front end must not be the only source of truth for which box is which."""
    from backend.main import app

    paths = app.openapi()["paths"]
    assert "/api/health/identity" in paths


@pytest.mark.parametrize("field", [
    "label", "role", "commit", "mt5_account", "broker", "bot_running",
])
def test_identity_reports_what_tells_two_servers_apart(field):
    src = read(Path("backend/main.py"))
    body = src.split("async def health_identity", 1)[1].split("# ── WebSocket", 1)[0]
    assert f'"{field}"' in body, f"identity must report {field}"


def test_identity_needs_no_sign_in():
    """It is polled before sign-in — every server must answer it, including
    ones you have no token for yet."""
    src = read(Path("backend/main.py"))
    body = src.split("async def health_identity", 1)[1].split("# ── WebSocket", 1)[0]
    assert "Depends(get_current_user)" not in body
    assert "require_admin" not in body


def test_identity_survives_a_detached_terminal():
    """A server with no MT5 attached is a state, not an error: it still has to
    answer, or the picker reports it as down when it is merely idle."""
    src = read(Path("backend/main.py"))
    body = src.split("async def health_identity", 1)[1].split("# ── WebSocket", 1)[0]
    assert "except Exception" in body
    assert "account = broker = server_name = None" in body


# ── cache bleed ─────────────────────────────────────────────────────────────

def test_query_keys_can_be_namespaced_by_server():
    src = read(SERVERS)
    assert "export function qk(" in src
    assert "'srv', activeServerId()" in src, (
        "the key prefix must include the server id, or the cache is shared")


def test_switching_servers_drops_the_cache():
    """Namespacing is the design; clearing is the belt. A key that slipped
    through un-namespaced must not survive a switch."""
    src = read(SWITCHER)
    switch = src.split("const switchTo", 1)[1].split("};", 1)[0]
    assert "qc.clear()" in switch, (
        "switching servers must drop cached data — a stale live number under a "
        "demo heading is the failure this feature has to avoid")


# ── per-server credentials ──────────────────────────────────────────────────

def test_tokens_are_stored_per_server():
    src = read(SERVERS)
    assert "algoedge_${kind}_${id}" in src, "token storage must be namespaced by server"
    for fn in ("getServerToken", "storeServerAuth", "clearServerAuth", "isSignedIn"):
        assert f"export const {fn}" in src or f"export function {fn}" in src


def test_a_request_carries_the_token_of_the_server_it_is_going_to():
    src = read(API)
    interceptor = src.split("api.interceptors.request.use", 1)[1].split("});", 1)[0]
    assert "config.serverId || activeServerId()" in interceptor
    assert "getServerToken(serverId)" in interceptor, (
        "a live token sent to demo is meaningless at best")
    assert "config.baseURL" in interceptor, (
        "the server must be resolved per request, not stored on the instance")


def test_the_base_url_is_not_mutated_on_a_shared_instance():
    """Mutating `baseURL` breaks the moment two requests are in flight, which
    is the normal state: the dashboard polls live while a backtest streams
    from demo."""
    src = read(API)
    assert "api.defaults.baseURL = " not in src, (
        "resolve the server per request instead of mutating the instance")


def test_a_refresh_goes_back_to_the_server_that_issued_the_token():
    src = read(API)
    body = src.split("export const performTokenRefresh", 1)[1].split("\n};", 1)[0]
    assert "serverId" in body
    assert "getServerRefresh(id)" in body
    inner = src.split("if (err.response?.status === 401", 1)[1][:900]
    assert "originalRequest?.serverId" in inner, (
        "a 401 from demo must not refresh the live session")


# ── mislabelling ────────────────────────────────────────────────────────────

def test_the_registry_is_checked_against_what_the_server_says():
    src = read(SWITCHER)
    assert "function mismatch(" in src
    assert "identity.role !== server.role" in src
    assert "identity.mt5_account" in src


def test_the_active_server_is_visible_on_every_screen():
    """A control you have to go and find is one you forget to check."""
    app = read(APP)
    assert "<ServerSwitcher />" in app
    assert "useServerColour()" in app
    assert "position: 'fixed', top: 0" in app, (
        "the live/demo accent must be unmissable and not scroll away")


def test_live_and_demo_are_different_colours():
    src = read(SERVERS)
    block = src.split("ROLE_COLOUR = {", 1)[1].split("};", 1)[0]
    colours = dict(re.findall(r"\[?(\w+)\]?:\s*'(#[0-9a-fA-F]{6})'", block))
    assert len(set(colours.values())) >= 2, colours


# ── the transport ───────────────────────────────────────────────────────────

def test_the_proxy_keeps_every_existing_url_working():
    """The change has to be additive: unprefixed traffic still reaches live,
    so bookmarks, the investor app and the landing page carry on."""
    conf = read(CADDY)
    assert "handle_path /s/demo/*" in conf
    assert "handle_path /s/live/*" in conf
    assert re.search(r"handle \{\s*\n\s*reverse_proxy localhost:8000", conf), (
        "unprefixed traffic must still go to live")


def test_the_proxy_allows_for_a_long_backtest():
    conf = read(CADDY)
    assert "read_timeout 10m" in conf, (
        "a large backtest can take minutes before its first byte")


def test_the_mixed_content_constraint_is_written_down_where_it_bites():
    """The single fact that decides this whole design. If it is not recorded,
    the next person tries an http:// address and spends a day on it."""
    for path in (CADDY, SWITCHER, SERVERS):
        src = read(path).lower()
        assert "mixed content" in src or "cannot call" in src or "cannot make requests" in src, (
            f"{path} should say why an http:// backend will not work")


def test_the_investor_app_has_no_server_picker():
    """Investor money must never be served from a demo backend, and the way to
    guarantee that is to give the app no way to ask one."""
    for p in Path("investor-web/src").rglob("*.jsx"):
        text = read(p)
        assert "ServerSwitcher" not in text, p
        assert "servers.js" not in text, p
    assert not Path("investor-web/src/services/servers.js").exists()


# ── acting on the wrong box ────────────────────────────────────────────────

def test_a_live_action_asks_first_and_names_the_server():
    """Not a generic "are you sure". The mistake is not pressing the button —
    it is not realising which server you were looking at."""
    src = read(SERVERS)
    assert "export function confirmOnLive" in src
    body = src.split("export function confirmOnLive", 1)[1]
    assert "s.role !== LIVE" in body, "demo must not be interrupted"
    assert "s.label" in body and "s.baseUrl" in body, "the prompt must name the box"


@pytest.mark.parametrize("action", ["startBot", "stopBot"])
def test_starting_and_stopping_the_bot_is_guarded(action):
    src = read(Path("frontend/src/pages/Dashboard.jsx"))
    assert "confirmOnLive" in src
    block = src.split(f"return {action}(", 1)[0][-400:]
    assert "confirmOnLive" in block, f"{action} must be guarded on a live server"


def test_the_websocket_follows_the_active_server():
    """A socket on a stale URL streams one box's trades into a page showing
    another's — worse than no socket, because it looks live."""
    src = read(Path("frontend/src/hooks/useBackendConnection.js"))
    assert "activeServer()" in src
    assert "getServerToken(activeServerId())" in src


def test_in_flight_requests_are_cancelled_before_the_cache_is_cleared():
    """A request already on its way to the old server resolves into the cache
    AFTER the clear, and then sits under the new server's name."""
    src = read(SWITCHER)
    switch = src.split("const switchTo", 1)[1].split("};", 1)[0]
    assert switch.index("qc.cancelQueries()") < switch.index("qc.clear()")


def test_a_backtest_can_be_aimed_at_a_named_server():
    """You asked to choose the box per run: the broker decides the spread, the
    swap and the fills, and 8 of 15 paired cells flipped SIGN between the two."""
    api = read(API)
    assert "export const runBacktest = (data, serverId = null)" in api
    page = read(Path("frontend/src/pages/Backtester.jsx"))
    # Built by the shared `buildPayload` (single run and Compare legs alike).
    assert "runBacktest(buildPayload(form.strategy_id), runServer)" in page
    assert "setRunServer" in page
    assert "runServer !== activeServerId()" in page, (
        "running somewhere other than the screen you are on must be visible")
