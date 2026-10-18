"""
backend/rl/spec.py

`AgentSpec` - everything about an agent that is decided BEFORE training starts.

WHY A FROZEN SPEC AT ALL
------------------------
Instrument selection is the leak nobody writes code for. Train on eleven
markets, report the three that worked, and the result is survivorship bias in
the experiment rather than in the code - no probe catches it, because every
feature was causal and every fold was purged. The only fix is to pre-register
the list and then report every asset attempted, including the ones that were
discarded. So the spec is frozen, content-hashed, and the validation report
prints its hash next to the numbers.

The same applies to the feature list, the reward weights and the seeds. A seed
count chosen after seeing the dispersion is not a seed count.

WHAT IS DELIBERATELY ABSENT
---------------------------
No risk percentage, no lot size, no stop distance, no daily loss cap, no
balance. Those belong to `RiskEngine` / `SlotBook` / `CircuitBreaker`, which
dispose of what the agent proposes (plan 4.3). They are not listed here and
not withheld here - there is nowhere to put them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from backend.rl.features import DEFAULT_FEATURES, FEATURES, unknown

# The reward split the user specified. Kept as a module constant so a spec that
# departs from it is visible in a diff rather than buried in a default.
MONEY_WEIGHT = 0.70
ADHERENCE_WEIGHT = 0.30

SUPERVISE_ALL = "ALL"


@dataclass(frozen=True)
class AgentSpec:
    """The pre-registration. Frozen, so a run cannot quietly change its terms."""

    name: str
    symbols: tuple[str, ...]
    # MODE B. Empty: the agent finds its own entries (mode A). Set to a
    # registered strategy id: that strategy finds the setups and the agent
    # decides, per setup, whether to TAKE it, where the stop goes and what to
    # aim at - then manages the exit.
    #
    # The point of naming a base strategy is the baseline it brings with it.
    # A mode-B agent is measured against the SAME signal list run through the
    # engine untouched, which is the strategy as it trades today. That is a far
    # sharper test than "did it make money".
    base_strategy: str = ""
    # MODE B over SEVERAL strategies (2026-10-18): every one of them finds
    # setups, the agent is offered all of them, and it sees which strategies
    # fired recently and in which direction (`rl/confluence.py`) - so it can
    # learn which COMBINATIONS pay. Takes precedence over `base_strategy`; an
    # empty tuple leaves an old single-strategy spec exactly as it was.
    base_strategies: tuple[str, ...] = ()
    timeframe: str = "M5"
    htf: tuple[str, ...] = ()
    features: tuple[str, ...] = DEFAULT_FEATURES

    # reward
    money_weight: float = MONEY_WEIGHT
    adherence_weight: float = ADHERENCE_WEIGHT
    downside_penalty: float = 1.0        # Sortino-style: losses count this much more

    # training
    seeds: tuple[int, ...] = (1, 2, 3, 4, 5)
    cpcv_groups: int = 8
    cpcv_test_groups: int = 2
    embargo_bars: int = 288              # one M5 day; also the project's max_hold default
    iterations: int = 40
    population: int = 24
    # Compute is the one honest constraint here and no amount of planning
    # compresses it: 5 seeds x 10 CPCV paths x (24 candidates x 40 iterations)
    # is 48,000 engine runs per asset. So a candidate is scored on a SAMPLE of
    # its training rows - `eval_windows` contiguous windows of `eval_window_bars`
    # - which is stochastic evaluation, not a shortcut: the final figures all
    # come from whole test folds. `max_cpcv_paths` caps the combinatorial
    # explosion (8 choose 2 is 28) while staying above the gate's floor of 8.
    eval_windows: int = 2
    eval_window_bars: int = 20_000
    max_cpcv_paths: int = 10
    elite_frac: float = 0.25
    init_sigma: float = 0.5
    # How many perturbations of the exit head to try per iteration. Each one is
    # a full engine run, so this is the second-biggest term in the compute
    # budget after `iterations` - and the exit head is the most overfit-prone
    # component here, so a bigger number buys more overfitting as readily as
    # more signal.
    exit_tries: int = 4

    # heads
    #
    # A frozen SUPERVISED direction model, fitted on the training fold, whose
    # call and confidence become two extra observation columns. RL does not
    # clearly beat supervised learning at calling direction - that is a
    # prediction problem - while RL's measured edge is the cost-aware
    # sequential part. So the director predicts and the policy decides.
    #
    # Fitted first and then frozen, deliberately: training both at once lets
    # the policy learn to exploit the director's mistakes rather than the
    # market's, which looks like skill and generalises to nothing.
    director: bool = False
    director_horizon: int = 12
    exit_head: bool = True
    # The exit head may run on a FASTER timeframe than the entry. An M1 exit on
    # an M5 entry is five times the resolution and keeps `BarFeed` parity
    # exactly, which a per-tick exit cannot - so this is the parity-safe half
    # of "react faster", and `risk/giveback.py` is the other half.
    #
    # Empty means the exit head runs on the entry timeframe.
    exit_timeframe: str = ""
    # Which other strategies this agent may supervise (plan 4.9). Empty means
    # it supervises nothing, which is the default: an agent that was never
    # measured as a supervisor does not become one by being selected.
    supervises: tuple[str, ...] = ()
    supervise_symbols: tuple[str, ...] = ()

    # -- the two risk numbers the agent DOES choose ------------------------
    #
    # The stop distance and the reward-to-risk ratio, each picked from a menu
    # the ADMIN pre-registers here. Discrete rather than continuous, for three
    # reasons that all matter: a softmax over a handful of choices is what the
    # optimiser handles well, a charter can state "stops at 1.5x ATR" and mean
    # it, and the menu itself is the ceiling - the agent cannot pick a stop
    # outside the list because there is no such action.
    #
    # Why this does NOT give the agent control of money. Lot size is
    # `risk_dollars / stop_distance`, and `risk_dollars` stays with the risk
    # layer. A wider stop therefore buys a SMALLER position, not a bigger risk:
    # the dollars at stake are the same whichever entry the agent picks. The
    # two remaining edges are both already enforced elsewhere - a stop tighter
    # than 2x the spread is refused by the engine's minimum-stop floor, and a
    # position too large for the account is clamped by the lot ceiling.
    stop_atr_choices: tuple[float, ...] = (0.75, 1.0, 1.5, 2.0, 3.0)
    rr_choices: tuple[float, ...] = (1.0, 1.5, 2.0, 3.0, 5.0)
    # False pins both to the slot config's `stop_atr_multiple` and `min_rr`,
    # which is what a supervisor-only agent wants - it never opens anything.
    risk_head: bool = True

    # entry shape. The agent proposes a direction and, with `risk_head`, where
    # its stop goes and what it is aiming at. It never sizes the position.
    # Chance is 1/3 with three actions, so 0.40 means "meaningfully better than
    # a coin flip". 0.55 demands near-certainty that a linear policy over noisy
    # market features almost never reaches: measured, a policy trained for six
    # iterations took ZERO trades at 0.55 while scoring positively on its own
    # sampled batches - the floor, not the policy, was the constraint.
    #
    # Re-measured 2026-10-18 at a REAL training length (XAUUSD M5 Jan-Jul 2026,
    # 40 iterations, 5 seeds x 8 paths): 0.55 took 2,090 test trades against
    # 1,618 at 0.40, no job took zero, median test R +0.040 against -0.011, and
    # 22 of 40 jobs positive against 19. The zero-trade problem was a six-
    # iteration artefact, so 0.55 is the default now.
    min_confidence: float = 0.55
    max_hold_bars: int = 288

    notes: str = ""
    attempted_symbols: tuple[str, ...] = ()   # every asset tried, incl. discarded

    @property
    def bases(self) -> tuple[str, ...]:
        """The base strategies, however the spec named them."""
        if self.base_strategies:
            return tuple(self.base_strategies)
        return (self.base_strategy,) if self.base_strategy else ()

    @property
    def base_label(self) -> str:
        """The base strategies for a sentence: "ORB_v1" or "ORB_v1 + TrendBreakout_v1"."""
        return " + ".join(self.bases)

    @property
    def multi_base(self) -> bool:
        """Two or more base strategies: the confluence inputs are on."""
        return len(self.bases) >= 2

    @property
    def mode(self) -> str:
        """A: its own strategy. B: optimising existing ones. C: supervisor."""
        if self.bases:
            return "B"
        return "C" if (self.supervises and not self.exit_head) else "A"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("an agent needs a name")
        if not self.symbols:
            raise ValueError("pre-register at least one symbol")
        bad = unknown(self.features)
        if bad:
            raise ValueError(f"not registered features: {bad}")
        if abs(self.money_weight + self.adherence_weight - 1.0) > 1e-9:
            raise ValueError("money_weight + adherence_weight must be 1.0")
        if len(self.seeds) < 5:
            # The literature's own standing advice, and this project has been
            # burned by single-seed results. Five is the floor, not the target.
            raise ValueError("at least 5 seeds - a one-seed result is not a result")
        if self.cpcv_test_groups >= self.cpcv_groups:
            raise ValueError("cpcv_test_groups must be fewer than cpcv_groups")
        if self.max_cpcv_paths < 8:
            # The gate's own floor. Fewer paths is a cheaper run and a weaker
            # claim, and the claim is the product.
            raise ValueError("at least 8 CPCV paths - the gate requires it")
        if self.exit_timeframe:
            from backend.strategies.bar_feed import TF_MINUTES
            fast = TF_MINUTES.get(self.exit_timeframe.upper())
            slow = TF_MINUTES.get(self.timeframe.upper())
            if not fast or not slow:
                raise ValueError(f"unknown timeframe in "
                                 f"{self.timeframe}/{self.exit_timeframe}")
            if fast > slow:
                raise ValueError(
                    f"the exit timeframe {self.exit_timeframe} is SLOWER than the "
                    f"entry timeframe {self.timeframe} - an exit head exists to "
                    f"react sooner, not later")
        if self.bases:
            from backend.strategies.registry import list_strategies
            known = list_strategies()
            for sid in self.bases:
                if sid not in known:
                    raise ValueError(
                        f"base strategy {sid!r} is not registered. Known: {sorted(known)}")
                if str(sid).startswith("RL::"):
                    raise ValueError("an agent cannot be another agent's base strategy")
            if len(set(self.bases)) != len(self.bases):
                raise ValueError("a base strategy is listed twice")
        if self.risk_head:
            if not self.stop_atr_choices or not self.rr_choices:
                raise ValueError("a risk head needs a menu to choose from")
            if any(v <= 0 for v in self.stop_atr_choices):
                raise ValueError("a stop multiple must be positive")
            if any(v <= 0 for v in self.rr_choices):
                raise ValueError("a reward-to-risk ratio must be positive")
        if self.supervise_symbols:
            extra = set(self.supervise_symbols) - set(self.symbols)
            if extra:
                # An agent trained on Crash 1000 does not get to supervise
                # GBPJPY. Stated in the plan; enforced here.
                raise ValueError(f"cannot supervise symbols it was not trained on: {sorted(extra)}")

    # -- derived ------------------------------------------------------------

    @property
    def n_features(self) -> int:
        """The market features alone."""
        return len(self.features)

    @property
    def observation_width(self) -> int:
        """What the POLICY actually sees: the market features, plus the
        director's call and confidence when there is one."""
        from backend.rl.director import DIRECTOR_FEATURES
        return (self.n_features
                + (len(DIRECTOR_FEATURES) if self.director else 0)
                + (len(self.bases) + 1 if self.multi_base else 0))

    @property
    def supervises_all(self) -> bool:
        return SUPERVISE_ALL in self.supervises

    def may_supervise(self, strategy_id: str, symbol: str) -> bool:
        if not self.supervises:
            return False
        if symbol not in (self.supervise_symbols or self.symbols):
            return False
        return self.supervises_all or strategy_id in self.supervises

    def feature_plain(self) -> dict[str, str]:
        """Feature name -> the English the charter will use."""
        return {n: FEATURES[n].plain for n in self.features}

    # -- identity -----------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        # Left out when empty so every spec saved before the field existed keeps
        # the same dict - and therefore the same content hash.
        if not d.get("base_strategies"):
            d.pop("base_strategies", None)
        return {k: (list(v) if isinstance(v, tuple) else v) for k, v in d.items()}

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSpec":
        known = {f for f in cls.__dataclass_fields__}
        kw = {}
        for k, v in (d or {}).items():
            if k not in known:
                continue
            kw[k] = tuple(v) if isinstance(v, list) else v
        return cls(**kw)

    @property
    def content_hash(self) -> str:
        """Identity by content, so two specs that differ anywhere are different
        runs. `notes` is included: a note that changes the experiment's meaning
        should change its hash."""
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class RunArtefacts:
    """Where a training run's output goes. Separate from the spec because it is
    about this machine, not about the experiment."""
    root: str = "data/rl"
    agent_id: str = ""
    seed_reports: list[dict] = field(default_factory=list)
