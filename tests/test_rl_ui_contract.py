"""
tests/test_rl_ui_contract.py

The screen can only create what the request model can express.

THE BUG THIS EXISTS FOR
-----------------------
`AgentSpec` grew `base_strategy`, `director`, `risk_head`, `exit_timeframe`,
`stop_atr_choices` and `rr_choices`. `SpecIn` - the Pydantic model the create
endpoint accepts - did not. Pydantic silently drops unknown keys, so the UI
could send them and the server would build a plain mode-A agent and report
success. Nothing failed. There was simply no way to create a mode-B or a
supervised agent through the product, while every page of the design document
said there was.

This is the second time this project has lost settings this way (a hand-kept
field list in the config UI dropped 18 live risk settings), so the check is
DERIVED: every tunable field on the dataclass must be reachable through the
request model, and adding a field to one without the other fails here rather
than becoming a feature nobody can use.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import fields as dataclass_fields

import pytest

from backend.api.routes import rl as api
from backend.rl.spec import AgentSpec

# Fields that deliberately are not set by the admin on creation.
#
#   agent_id / created_at   assigned by the server
#   attempted_symbols       mirrored from `symbols` by the create screen
#   supervises / supervise_symbols
#                           set on the supervisor screen, not this one
NOT_FROM_THE_FORM = {"agent_id", "created_at"}


def test_every_tunable_spec_field_can_be_set_through_the_api():
    """The derived check. A new AgentSpec field must land in SpecIn too."""
    spec_fields = {f.name for f in dataclass_fields(AgentSpec)} - NOT_FROM_THE_FORM
    api_fields = set(api.SpecIn.model_fields)
    missing = sorted(spec_fields - api_fields)
    assert not missing, (
        "these AgentSpec fields cannot be set through POST /api/rl/agents, so "
        f"no screen can ever configure them: {missing}. Pydantic drops unknown "
        "keys silently, so sending them anyway returns 200 and builds a "
        "different agent than the one asked for.")


def test_the_api_does_not_accept_fields_the_spec_will_drop():
    """And the reverse: a key SpecIn takes that AgentSpec has no home for.

    That direction is just as quiet - `to_spec()` filters, so the value is
    accepted, acknowledged and thrown away.
    """
    spec_fields = {f.name for f in dataclass_fields(AgentSpec)}
    extra = sorted(set(api.SpecIn.model_fields) - spec_fields)
    assert not extra, (
        f"SpecIn accepts {extra}, which AgentSpec has no field for - the value "
        "is dropped by to_spec() after the request has already succeeded.")


def test_tuple_fields_survive_the_json_round_trip():
    """A list where the spec wants a tuple breaks the content hash.

    The spec is hashed to freeze the pre-registration, and `[1, 2]` and
    `(1, 2)` do not hash alike - so a run created through the API would not
    match the same run created in a script, and the Backtester's "is this
    window inside the training range" check keys off that hash.
    """
    spec = api.SpecIn(
        name="t", symbols=["Crash 1000 Index"], seeds=[1, 2, 3, 4, 5],
        stop_atr_choices=[1.0, 2.0], rr_choices=[2.0, 3.0],
        features=["atr_pct", "ret_1"],
    ).to_spec()
    for name in ("symbols", "seeds", "stop_atr_choices", "rr_choices", "features"):
        assert isinstance(getattr(spec, name), tuple), f"{name} is not a tuple"


# -- what the screen tells the admin ----------------------------------------


def _spec(**kw) -> AgentSpec:
    base = {"name": "t", "symbols": ["Crash 1000 Index"]}
    return api.SpecIn(**(base | kw)).to_spec()


def test_mode_a_says_it_finds_its_own_entries():
    d = api.describe_learning(_spec())
    assert d["mode"] == "A"
    assert d["base_strategy"] == ""
    assert any("its own entries" in s for s in d["sentences"])


def test_mode_b_names_the_strategy_it_is_built_on():
    """The question the old screen could not answer: against what?"""
    from backend.strategies.registry import get_all_strategies

    sid = next(s for s in sorted(get_all_strategies()) if not s.startswith("RL::"))
    d = api.describe_learning(_spec(base_strategy=sid))
    assert d["mode"] == "B"
    assert d["base_strategy"] == sid
    assert sid in d["mode_label"]
    assert any(sid in s for s in d["sentences"]), (
        "a mode-B agent's description must name the strategy finding its setups")


def test_it_distinguishes_supervised_from_reinforcement():
    """Both are on when a director is configured, and the words say so.

    The admin asked which of the two this was and the screen could not say.
    Reinforcement is always on - it is the thing being trained - and the
    supervised model is an addition, not an alternative.
    """
    plain = api.describe_learning(_spec())
    assert plain["reinforcement"] is True
    assert plain["supervised"] is False
    assert any("Reinforcement learning only" in s for s in plain["sentences"])

    both = api.describe_learning(_spec(director=True, director_horizon=20))
    assert both["reinforcement"] is True and both["supervised"] is True
    assert any("SUPERVISED" in s and "frozen" in s for s in both["sentences"])
    assert any("20 bars" in s for s in both["sentences"]), (
        "the horizon is what the supervised model is asked to call; it belongs "
        "in the description")


def test_the_risk_head_description_lists_the_actual_menus():
    d = api.describe_learning(_spec(stop_atr_choices=[1.0, 2.5],
                                    rr_choices=[2.0, 4.0]))
    joined = " ".join(d["sentences"])
    assert "1x" in joined and "2.5x" in joined
    assert "1:2" in joined and "1:4" in joined


def test_without_a_risk_head_it_says_the_slot_decides():
    d = api.describe_learning(_spec(risk_head=False))
    assert any("does not choose them" in s for s in d["sentences"])


def test_the_never_list_covers_the_money_controls():
    """The boundary of the agent's authority, stated on screen.

    Not decoration: these four are the things a reader of the Agents page
    reasonably worries about, and the reason they are safe is that none of
    them is in the observation at all.
    """
    never = " ".join(api.describe_learning(_spec())["never"])
    for phrase in ("size a position", "risk percentage", "widen a stop",
                   "circuit-breaker"):
        assert phrase in never


@pytest.mark.parametrize("tf", ["", "M1", "M5"])
def test_the_exit_head_description_names_its_timeframe(tf):
    d = api.describe_learning(_spec(exit_head=True, exit_timeframe=tf))
    sentence = next(s for s in d["sentences"] if "running trade" in s)
    if tf:
        assert tf in sentence
    else:
        assert "closed bar" in sentence


# -- the cost estimate ------------------------------------------------------


def test_the_job_count_is_seeds_times_paths_times_symbols():
    """The number that surprises people, and it has to be right.

    It is shown before the run starts, so it is the only warning that a
    casual "5 seeds, 2 symbols" is a hundred training runs.
    """
    spec = _spec(symbols=["A", "B", "C"], seeds=[1, 2, 3, 4, 5],
                 cpcv_groups=8, cpcv_test_groups=2, max_cpcv_paths=10)
    est = api.estimate_cost(spec)
    assert est["paths"] == 10               # C(8,2)=28, capped at 10
    assert est["jobs"] == 5 * 10 * 3
    assert "150" in est["note"]


def test_the_path_count_falls_back_to_the_combinatorial_limit():
    """Asking for more paths than the split can produce must not inflate it."""
    spec = _spec(cpcv_groups=4, cpcv_test_groups=2, max_cpcv_paths=50)
    assert api.estimate_cost(spec)["paths"] == math.comb(4, 2)


def test_the_estimate_needs_no_bars():
    """It is shown on a form, before any data is attached.

    If it ever starts needing a bar count it stops being displayable at the
    moment it is useful, which is before the admin commits.
    """
    spec = _spec(symbols=["Nothing Attached Yet"])
    est = api.estimate_cost(spec)
    assert est["jobs"] > 0 and est["symbols"] == 1


# -- and the other direction: the screen must expose what the API accepts ---

# Settable through the API but deliberately not on the create screen.
#
#   name / symbols        are on it, under those names
#   attempted_symbols     mirrored from `symbols` by the screen itself
#   supervises / supervise_symbols
#                         belong to the supervisor screen, not this one
#   population            CEM-only, and shown
#   min_confidence        a live-execution threshold, set on the slot
NOT_ON_THE_CREATE_SCREEN = {
    "attempted_symbols", "supervises", "supervise_symbols",
    "eval_windows", "eval_window_bars", "exit_tries",
}

AGENTS_PAGE = "frontend/src/pages/Agents.jsx"


def test_the_create_screen_exposes_every_field_the_api_accepts():
    """The complement of the first test, and the same failure in reverse.

    A field the API accepts and no screen sets is a setting that runs on its
    dataclass default forever - which is how the Strategy Lab ran defaults for
    its entire life, and how the config UI silently dropped 18 risk settings.
    Derived from the model rather than kept by hand, for exactly that reason.
    """
    page = pathlib.Path(AGENTS_PAGE).read_text(encoding="utf-8")
    missing = sorted(
        f for f in api.SpecIn.model_fields
        if f not in NOT_ON_THE_CREATE_SCREEN and f"{f}:" not in page
        and f"'{f}'" not in page)
    assert not missing, (
        f"{AGENTS_PAGE} never mentions {missing}, so the create screen cannot "
        "set them and every agent will use the dataclass default. Either add a "
        "control or add the field to NOT_ON_THE_CREATE_SCREEN with the reason.")


def test_the_page_disables_itself_while_training():
    """The complaint was clicking things mid-run; this is that, pinned down.

    Checked as text rather than by rendering because the project has no JS test
    runner - crude, but it does catch the whole guard being deleted, which is
    the realistic regression.
    """
    page = pathlib.Path(AGENTS_PAGE).read_text(encoding="utf-8")
    assert "=== 'TRAINING'" in page, "nothing derives a busy flag from the status"
    assert page.count("disabled={busy") + page.count("disabled={!ready || busy") >= 3, (
        "the busy flag reaches fewer than three controls; a run can still be "
        "raced by a second one")


# -- the endpoints the screen calls, over HTTP -----------------------------


@pytest.mark.asyncio
async def test_describe_runs_on_a_spec_that_was_never_saved():
    """The create form reads itself back before the agent exists.

    Over HTTP rather than by calling the function, because the thing that
    actually broke was the request MODEL - `SpecIn` dropping keys - and calling
    `describe_learning` directly would have passed the whole time.
    """
    import httpx
    from fastapi import FastAPI

    from backend.api.deps import require_admin

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "role": "admin"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        strategies = (await client.get("/api/rl/strategies")).json()["strategies"]
        assert strategies, "no base strategies offered, so mode B is unusable"
        assert not any(s.startswith("RL::") for s in strategies), (
            "an agent must not be offered as another agent's base strategy")

        r = await client.post("/api/rl/describe", json={
            "name": "probe", "symbols": ["Crash 1000 Index"],
            "base_strategy": strategies[0], "director": True,
            "seeds": [1, 2, 3, 4, 5],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["mode"] == "B", (
            "the request model dropped base_strategy again - this is the exact "
            "failure that made mode B unreachable from the UI")
        assert body["learning"]["supervised"] is True
        assert strategies[0] in " ".join(body["learning"]["sentences"])
        assert body["estimate"]["jobs"] == 5 * body["estimate"]["paths"]


@pytest.mark.asyncio
async def test_describe_reports_a_bad_spec_instead_of_failing():
    """A half-filled form must not make the read-back panel throw.

    It is called on every keystroke, so a 500 there would paint the create
    screen red while the admin is still typing.
    """
    import httpx
    from fastapi import FastAPI

    from backend.api.deps import require_admin

    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "role": "admin"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        r = await client.post("/api/rl/describe", json={
            "name": "probe", "symbols": ["Crash 1000 Index"],
            "base_strategy": "NoSuchStrategy_v9",
        })
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "not registered" in r.json()["error"]
