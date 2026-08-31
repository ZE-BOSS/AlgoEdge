"""
backend/api/routes/strategy_factory.py

[Phase 14 Stream 3] Strategy Factory API
=========================================
Provides CRUD + Git/PR automation endpoints for the strategy factory workflow:

  GET  /strategy-factory/strategies    — list all registered strategies + status
  POST /strategy-factory/generate      — create a new strategy scaffold from a spec
  POST /strategy-factory/activate/:id  — mark active, commit to dev branch, open PR
  DELETE /strategy-factory/:id         — remove a generated (non-live) strategy

The Git workflow (Stream 5) is embedded in the activate endpoint:
  1. Stages all modified files under backend/strategies/<strategy_id>/ and
     frontend/src/pages/StrategyLab.jsx (param schema update).
  2. Commits to the current branch (always dev per user rules).
  3. Opens a GitHub PR via the GH REST API if GITHUB_TOKEN is set in the
     environment. If not set, the activate still succeeds — the PR step is
     advisory, not blocking.

Authentication: all endpoints require a valid JWT (get_current_user).
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.api.routes.auth import get_current_user
from backend.data.models import User
from backend.strategies.registry import get_all_strategies
from backend.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/api/strategy-factory", tags=["strategy-factory"])

# ── Repo root (two levels up from this file) ──────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


# ══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _run_git(*args: str, cwd: Path = _REPO_ROOT) -> tuple[int, str, str]:
    """Run a git command, returning (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True, text=True, cwd=str(cwd),
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _open_github_pr(branch: str, title: str, body: str) -> str | None:
    """
    Open a GitHub PR via the REST API.
    Returns the PR URL on success, None if GITHUB_TOKEN is absent or the call
    fails (callers treat this as advisory-only).
    """
    token = os.getenv("GITHUB_TOKEN")
    repo  = os.getenv("GITHUB_REPO")  # e.g. "org/AlgoEdge"
    base  = os.getenv("GITHUB_BASE_BRANCH", "dev")  # per user rules: always dev
    if not token or not repo:
        logger.info("[StrategyFactory] GITHUB_TOKEN/REPO not set — skipping PR creation")
        return None
    try:
        import httpx  # optional dep; standard library not enough for async-free POST here
        r = httpx.post(
            f"https://api.github.com/repos/{repo}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={"title": title, "body": body, "head": branch, "base": base},
            timeout=15,
        )
        data = r.json()
        if r.status_code in (200, 201):
            url = data.get("html_url", "")
            logger.info(f"[StrategyFactory] PR opened: {url}")
            return url
        logger.warning(f"[StrategyFactory] GitHub PR failed {r.status_code}: {data.get('message')}")
    except Exception as e:
        logger.warning(f"[StrategyFactory] PR creation error (non-fatal): {e}")
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Request / response models
# ══════════════════════════════════════════════════════════════════════════════

class GenerateRequest(BaseModel):
    strategy_id: str           # e.g. "MyAlpha_v1"
    display_name: str          # Human label for the UI
    description: str = ""
    timeframes: list[str] = ["H1", "M15"]
    author: str = ""
    params: dict[str, Any] = {}   # seed params written into the params.py scaffold


class ActivateRequest(BaseModel):
    strategy_id: str
    commit_message: str = ""
    pr_title: str = ""
    pr_body: str = ""


# ══════════════════════════════════════════════════════════════════════════════
#  Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/strategies")
async def list_strategies(current_user: User = Depends(get_current_user)):
    """
    List every registered strategy plus its scaffold status (generated /
    active / built-in).
    """
    try:
        registered = get_all_strategies()  # {id: class}
    except Exception as e:
        raise HTTPException(500, f"Could not load strategy registry: {e}")

    strategies_dir = _REPO_ROOT / "backend" / "strategies"
    items = []
    for sid, cls in registered.items():
        scaffold_dir = _identify_strategy_dir(sid)
        items.append({
            "strategy_id": sid,
            "display_name": getattr(cls, "display_name", sid),
            "description": (cls.__doc__ or "").strip().split("\n")[0][:120],
            "status": "active",          # all registered = active
            "scaffold_path": str(scaffold_dir.relative_to(_REPO_ROOT)) if scaffold_dir else None,
            "has_params": (scaffold_dir / "params.py").exists() if scaffold_dir else False,
        })

    # Also include any scaffold dirs that exist but aren't yet registered
    for d in sorted(strategies_dir.iterdir()):
        if not d.is_dir() or not (d / "engine.py").exists():
            continue
        # Derive an ID by reading @register_strategy decorator or dir name
        sid = _read_strategy_id_from_dir(d)
        if sid and sid not in registered:
            items.append({
                "strategy_id": sid,
                "display_name": sid,
                "description": "Generated scaffold — not yet activated",
                "status": "generated",
                "scaffold_path": str(d.relative_to(_REPO_ROOT)),
                "has_params": (d / "params.py").exists(),
            })

    return {"strategies": items, "total": len(items)}


@router.get("/strategy-defaults")
async def get_all_strategy_defaults(current_user: User = Depends(get_current_user)):
    """
    Per-strategy exit/session defaults, with the measurement behind each.

    Trailing, break-even and session gating used to be one global setting for
    all seven strategies. The Phase 3 study showed that cannot be right — the
    trailing sweep improved 10 of 15 cells and made 5 worse, and the session
    ablation ranged from -0.170 to +0.126 depending on strategy. The frontend
    reads this so the parameter panel can show the measured-best values for
    whichever strategy is selected, per strategy rather than per account.
    """
    from backend.strategies.strategy_defaults import STRATEGY_DEFAULTS, OVERRIDABLE

    out = {}
    for sid, cfg in STRATEGY_DEFAULTS.items():
        out[sid] = {
            "defaults": {k: v for k, v in cfg.items() if k != "evidence"},
            "evidence": cfg.get("evidence", ""),
        }
    return {
        "strategy_defaults": out,
        "overridable_fields": sorted(OVERRIDABLE),
        "note": (
            "These are DEFAULTS, not constraints. An explicit value in the request "
            "always wins. Fields outside `overridable_fields` (position sizing, "
            "drawdown caps, concurrency) remain account-level."
        ),
    }


@router.post("/generate")
async def generate_strategy(
    req: GenerateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Scaffold a new strategy directory from a template.

    Creates:
      backend/strategies/<strategy_id>/
        __init__.py
        engine.py     — BaseStrategy subclass with on_bar stub
        params.py     — Pydantic dataclass with any seed params

    Does NOT register the strategy (that happens on /activate).
    """
    sid = req.strategy_id.strip()
    if not sid or not sid.replace("_", "").isalnum():
        raise HTTPException(400, "strategy_id must be alphanumeric (underscores allowed)")

    dir_name = _slug_dir(sid)
    target = _REPO_ROOT / "backend" / "strategies" / dir_name
    if target.exists():
        raise HTTPException(409, f"Directory already exists: backend/strategies/{dir_name}")

    try:
        target.mkdir(parents=True)
        _write_scaffold(target, req)
    except Exception as e:
        # Clean up on failure
        import shutil
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        logger.error(f"[StrategyFactory] scaffold failed: {e}")
        raise HTTPException(500, f"Scaffold creation failed: {e}")

    logger.info(f"[StrategyFactory] Scaffolded {sid} → {target}")
    return {
        "status": "generated",
        "strategy_id": sid,
        "path": str(target.relative_to(_REPO_ROOT)),
        "files": [str(f.relative_to(target)) for f in target.rglob("*.py")],
    }


@router.post("/activate/{strategy_id}")
async def activate_strategy(
    strategy_id: str,
    req: ActivateRequest,
    current_user: User = Depends(get_current_user),
):
    """
    [Stream 5 — Git/PR automation]

    Mark a generated strategy as active, commit it to the dev branch, and
    optionally open a GitHub PR.

    Steps:
      1. Verify the scaffold directory exists.
      2. git add <scaffold_dir>
      3. git commit -m "<message>"
      4. Open GitHub PR (advisory — won't fail if GITHUB_TOKEN absent).

    The commit targets the current branch (which the user has confirmed will
    be `dev` per project rules). We never push to `staging` or `main` directly.
    """
    sid = strategy_id.strip()
    scaffold_dir = _identify_strategy_dir(sid)
    if scaffold_dir is None or not scaffold_dir.exists():
        raise HTTPException(
            404,
            f"No scaffold directory found for '{sid}'. "
            "Run /generate first, or check that the directory is under backend/strategies/.",
        )

    # ── 1. Stage changes ──
    rc, out, err = _run_git("add", str(scaffold_dir.relative_to(_REPO_ROOT)))
    if rc != 0:
        raise HTTPException(500, f"git add failed: {err or out}")

    # ── 2. Commit ──
    msg = (req.commit_message or f"feat(strategy): add {sid} scaffold [Phase 14]").strip()
    rc, out, err = _run_git("commit", "-m", msg, "--allow-empty")
    if rc != 0:
        raise HTTPException(500, f"git commit failed: {err or out}")

    commit_hash = out.split("\n")[0] if out else "unknown"

    # ── 3. GitHub PR ──
    pr_url = None
    rc_br, current_branch, _ = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    branch = current_branch if rc_br == 0 else "dev"

    pr_title = (req.pr_title or f"feat: Add {sid} strategy scaffold").strip()
    pr_body  = (req.pr_body  or _default_pr_body(sid, scaffold_dir)).strip()
    pr_url   = _open_github_pr(branch, pr_title, pr_body)

    logger.info(f"[StrategyFactory] Activated {sid} | commit={commit_hash} | branch={branch} | PR={pr_url}")
    return {
        "status": "activated",
        "strategy_id": sid,
        "branch": branch,
        "commit": commit_hash,
        "pr_url": pr_url,
        "pr_opened": pr_url is not None,
    }


@router.delete("/{strategy_id}")
async def delete_strategy(
    strategy_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Delete a generated (non-live) strategy scaffold.
    Refuses to delete any strategy that is already registered in the live
    strategy registry (those must be removed manually via a PR).
    """
    import shutil

    sid = strategy_id.strip()
    try:
        registered = get_all_strategies()
    except Exception:
        registered = {}

    if sid in registered:
        raise HTTPException(
            400,
            f"'{sid}' is an active registered strategy. "
            "Remove it via a PR — do not use this endpoint for live strategies.",
        )

    scaffold_dir = _identify_strategy_dir(sid)
    if scaffold_dir is None or not scaffold_dir.exists():
        raise HTTPException(404, f"No scaffold directory found for '{sid}'")

    shutil.rmtree(scaffold_dir)
    logger.info(f"[StrategyFactory] Deleted scaffold: {scaffold_dir}")
    return {"status": "deleted", "strategy_id": sid, "path": str(scaffold_dir.relative_to(_REPO_ROOT))}


# ══════════════════════════════════════════════════════════════════════════════
#  Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _slug_dir(strategy_id: str) -> str:
    """Convert 'MyAlpha_v1' → 'strategy_myalpha_v1'."""
    return "strategy_" + strategy_id.lower().replace("-", "_")


def _identify_strategy_dir(strategy_id: str) -> Path | None:
    """
    Try to find the directory for a given strategy_id.
    First checks the slug convention, then scans all strategy dirs for a
    @register_strategy('strategy_id') decorator.
    """
    strategies_root = _REPO_ROOT / "backend" / "strategies"
    slug = _slug_dir(strategy_id)
    candidate = strategies_root / slug
    if candidate.exists():
        return candidate

    # Fallback: scan for the decorator
    for d in strategies_root.iterdir():
        if d.is_dir() and _read_strategy_id_from_dir(d) == strategy_id:
            return d
    return None


def _read_strategy_id_from_dir(d: Path) -> str | None:
    """Extract @register_strategy('X') from engine.py."""
    engine = d / "engine.py"
    if not engine.exists():
        return None
    try:
        text = engine.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("@register_strategy("):
                # @register_strategy("APA_v1") or @register_strategy('APA_v1')
                inner = s[len("@register_strategy("):].rstrip(")")
                return inner.strip("\"'")
    except Exception:
        pass
    return None


def _write_scaffold(target: Path, req: GenerateRequest) -> None:
    """Write __init__.py, params.py, engine.py for a new strategy."""
    sid = req.strategy_id
    timeframes = req.timeframes or ["H1", "M15"]

    # __init__.py
    (target / "__init__.py").write_text("", encoding="utf-8")

    # params.py
    params_lines = ["from dataclasses import dataclass\n", "\n",
                    "\n@dataclass\n", f"class {sid}Params:\n",
                    '    """Auto-generated params for ' + sid + '."""\n\n']
    for k, v in (req.params or {}).items():
        if isinstance(v, bool):      # MUST precede int — bool is a subclass of int
            params_lines.append(f"    {k}: bool = {v}\n")
        elif isinstance(v, int):
            params_lines.append(f"    {k}: int = {v}\n")
        elif isinstance(v, float):
            params_lines.append(f"    {k}: float = {v}\n")
        elif isinstance(v, str):
            params_lines.append(f"    {k}: str = {v!r}\n")
        else:
            params_lines.append(f"    # {k}: {v!r}  # TODO: type this\n")
    if not req.params:
        params_lines.append("    # Add strategy parameters here\n")
        params_lines.append("    pass\n")
    (target / "params.py").write_text("".join(params_lines), encoding="utf-8")

    # engine.py
    tf_list = ", ".join(f'"{t}"' for t in timeframes)
    engine_text = textwrap.dedent(f'''\
        """
        backend/strategies/{target.name}/engine.py

        {req.display_name}
        {'=' * max(len(req.display_name), 4)}
        {req.description or 'Auto-generated strategy scaffold.'}

        Author: {req.author or 'AlgoEdge Strategy Factory'}
        Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}

        Edit this file to implement your strategy logic.
        """

        import pandas as pd

        from backend.core.config_schema import UserConfigV2
        from backend.strategies.base_strategy import BaseStrategy, TradeSignal
        from backend.strategies.registry import register_strategy
        from backend.utils.logger import get_logger

        logger = get_logger(__name__)


        @register_strategy("{sid}")
        class {sid}Engine(BaseStrategy):
            """
            {req.display_name or sid}

            {req.description or 'TODO: describe your strategy.'}
            """

            def __init__(self, config: UserConfigV2):
                super().__init__(config)
                # TODO: load your params here, e.g.:
                # self.params = config.{sid.lower()}
                self.state: dict = {{}}

            def get_required_timeframes(self) -> list[str]:
                return [{tf_list}]

            async def on_bar(
                self,
                symbol: str,
                timeframe: str,
                candles: pd.DataFrame,
            ) -> TradeSignal | None:
                """
                Called on every closed bar for each required timeframe.
                Return a TradeSignal to open a trade, or None to pass.
                """
                if len(candles) < 20:
                    return None

                # TODO: implement your entry logic here.
                # Example:
                #   latest = candles.iloc[-1]
                #   if <condition>:
                #       return TradeSignal(
                #           strategy_id="{sid}",
                #           symbol=symbol,
                #           direction="BUY",
                #           signal_type="CUSTOM",
                #           timeframe=timeframe,
                #           entry_price=latest["close"],
                #           stop_loss=latest["close"] - atr,
                #           take_profit=latest["close"] + atr * 2,
                #           confluence_score=70,
                #           timestamp=float(latest.get("time", 0)),
                #       )
                return None
        ''')
    (target / "engine.py").write_text(engine_text, encoding="utf-8")


def _default_pr_body(sid: str, scaffold_dir: Path) -> str:
    files = [str(f.relative_to(_REPO_ROOT)) for f in scaffold_dir.rglob("*.py")]
    file_list = "\n".join(f"- `{f}`" for f in files)
    return textwrap.dedent(f"""\
        ## Strategy Factory: `{sid}`

        This PR adds the scaffold for the `{sid}` strategy, generated via the
        AlgoEdge Strategy Factory endpoint (`POST /api/strategy-factory/generate`).

        ### Files added
        {file_list}

        ### Next steps
        1. Implement `on_bar()` in `engine.py`
        2. Add any required params to `params.py`
        3. Wire the params into `UserConfigV2` / `config_schema.py`
        4. Add the strategy to the backtest param injection block in `backtest.py`
        5. Run backtests; merge when satisfied

        > Merging targets `dev`. Do **not** merge directly to `staging` or `main`.
    """)
