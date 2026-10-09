"""
backend/rl/policy.py

The policy: two heads over one encoder, and a parameter vector an optimiser
can search.

SHAPE OF THE DECISION
---------------------
    entry head   observation            -> LONG | FLAT | SHORT, with a confidence
    risk  head   observation            -> which stop multiple, which R:R,
                                           each from a menu the admin registered
    exit  head   observation + position  -> HOLD | CLOSE_PARTIAL | CLOSE_ALL | TIGHTEN_STOP

That is the entire action space, and what it leaves out is the design: **there
is no action that sets a lot size or a risk percentage.**

The risk head needs its own justification, because a stop distance looks like a
risk control. It is not, here. Lot size is `risk_dollars / stop_distance` and
`risk_dollars` belongs to the risk layer, so choosing a wider stop buys a
SMALLER position rather than a larger risk - the dollars at stake are identical
whichever stop the agent picks. It is choosing the shape of the trade, not its
size. The two edges where that could still bite are both closed already: a stop
inside 2x the spread is refused by the engine's minimum-stop floor, and a
position too big for the account is clamped by the lot ceiling.

The menu is the ceiling. The agent picks an index into a list the admin
pre-registered on the `AgentSpec`; there is no action that reaches a value
outside it.

WHY THE DEFAULT IS LINEAR
-------------------------
A linear head over 25 causal features is already close to readable: the weights
rank the features, and `distill.py` turns the top ones into charter gates by
thresholding them. A deeper net distils worse, and the distilled thesis has to
REPRODUCE the policy's decisions on held-out bars or the charter is decoration -
so expressiveness that cannot be distilled is expressiveness that cannot be
approved.

`hidden` > 0 gives one tanh layer for when the linear head is genuinely not
enough. It costs parameters, and parameter count is what a population optimiser
pays for, so it is off by default and recorded in the artefact when on.

TWO OPTIMISERS, ONE POLICY
--------------------------
`to_flat` / `from_flat` expose the parameters as one vector, which is all a
cross-entropy-method search needs - and CEM needs no torch, so the whole
pipeline runs on this machine today. When `stable_baselines3` and `torch` are
installed, `train.py` uses PPO instead and distils the resulting net into this
same representation, so everything downstream - the charter, the registry, the
live engine - is unchanged either way.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np

LONG, FLAT, SHORT = "BUY", "FLAT", "SELL"
ENTRY_ACTIONS = (LONG, FLAT, SHORT)

HOLD, CLOSE_PARTIAL, CLOSE_ALL, TIGHTEN = "HOLD", "CLOSE_PARTIAL", "CLOSE_ALL", "TIGHTEN_STOP"
EXIT_ACTIONS = (HOLD, CLOSE_PARTIAL, CLOSE_ALL, TIGHTEN)


def softmax(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    x = x - np.max(x)
    e = np.exp(np.clip(x, -500, 500))
    s = e.sum()
    return e / s if s > 0 else np.full_like(e, 1.0 / len(e))


@dataclass
class LinearPolicy:
    """Weights, and the two decisions they make.

    Everything is plain numpy so a policy is a JSON blob: that is what lets the
    registry content-hash it, the live engine load it without a deep-learning
    runtime on the VPS, and a test construct one by hand.
    """
    n_features: int
    n_position: int = 7
    hidden: int = 0
    exit_head: bool = True
    # How many stop multiples and R:R ratios the admin put on the menu. Zero
    # means no risk head: the slot config decides both, which is what a
    # supervisor-only agent wants since it never opens anything.
    n_stop: int = 0
    n_rr: int = 0

    W_entry: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    b_entry: np.ndarray = field(default_factory=lambda: np.zeros(0))
    W_exit: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    b_exit: np.ndarray = field(default_factory=lambda: np.zeros(0))
    W_hidden: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    b_hidden: np.ndarray = field(default_factory=lambda: np.zeros(0))
    W_stop: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    b_stop: np.ndarray = field(default_factory=lambda: np.zeros(0))
    W_rr: np.ndarray = field(default_factory=lambda: np.zeros((0, 0)))
    b_rr: np.ndarray = field(default_factory=lambda: np.zeros(0))

    seed: int = 0
    trained_iterations: int = 0

    def __post_init__(self) -> None:
        if self.W_entry.size == 0:
            self.reset()

    # -- construction -------------------------------------------------------

    def reset(self, seed: int | None = None) -> "LinearPolicy":
        """Zeros, not random. A zero policy is FLAT everywhere, which means an
        untrained agent trades nothing instead of trading noise - the right
        starting point for something that will be measured against doing
        nothing."""
        rng = np.random.default_rng(self.seed if seed is None else seed)
        enc = self.hidden or self.n_features
        if self.hidden:
            # Small random hidden weights: a zero hidden layer has zero
            # gradient through tanh and the search would never leave it.
            self.W_hidden = rng.normal(0, 0.1, (self.n_features, self.hidden))
            self.b_hidden = np.zeros(self.hidden)
        else:
            self.W_hidden = np.zeros((0, 0))
            self.b_hidden = np.zeros(0)
        self.W_entry = np.zeros((enc, len(ENTRY_ACTIONS)))
        self.b_entry = np.zeros(len(ENTRY_ACTIONS))
        n_exit_in = enc + self.n_position
        self.W_exit = np.zeros((n_exit_in, len(EXIT_ACTIONS)))
        self.b_exit = np.zeros(len(EXIT_ACTIONS))
        self.W_stop = np.zeros((enc, max(1, self.n_stop)))
        self.b_stop = np.zeros(max(1, self.n_stop))
        self.W_rr = np.zeros((enc, max(1, self.n_rr)))
        self.b_rr = np.zeros(max(1, self.n_rr))
        return self

    # -- the encoder --------------------------------------------------------

    def encode(self, z: np.ndarray) -> np.ndarray:
        z = np.asarray(z, dtype=float)
        if not self.hidden:
            return z
        return np.tanh(z @ self.W_hidden + self.b_hidden)

    # -- the entry head -----------------------------------------------------

    def entry_logits(self, z: np.ndarray) -> np.ndarray:
        return self.encode(z) @ self.W_entry + self.b_entry

    def entry_probs(self, z: np.ndarray) -> np.ndarray:
        logits = self.entry_logits(z)
        if logits.ndim == 1:
            return softmax(logits)
        return np.apply_along_axis(softmax, 1, logits)

    def act_entry(self, z: np.ndarray, min_confidence: float = 0.55
                  ) -> tuple[str | None, float, np.ndarray]:
        """One bar's entry decision.

        Deterministic (argmax) rather than sampled. A sampled policy would give
        a different backtest every run, and this project's parity rule - live
        and backtest must agree - is worth more than exploration at inference.
        Exploration happens in the trainer, where it belongs.

        Returns `(direction or None, confidence, probabilities)`. `None` is a
        real answer and the common one: FLAT winning, or a direction winning
        without enough confidence, both mean no signal.
        """
        p = self.entry_probs(np.asarray(z, dtype=float).ravel())
        k = int(np.argmax(p))
        conf = float(p[k])
        if ENTRY_ACTIONS[k] == FLAT or conf < float(min_confidence):
            return None, conf, p
        return ENTRY_ACTIONS[k], conf, p

    def entry_decisions(self, Z: np.ndarray, min_confidence: float = 0.55,
                        rng: np.random.Generator | None = None
                        ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Vectorised over every bar at once.

        An episode is tens of thousands of bars and the engine runs after this,
        so a Python loop here is the difference between a training run that
        finishes and one that does not.

        `rng` switches from argmax to SAMPLING, which is how a policy-gradient
        optimiser explores. Inference always passes `rng=None`: a sampled
        policy would give a different backtest every run, and this project's
        parity rule - live and backtest must agree - is worth more than
        exploration at inference time.

        `min_confidence` applies to the ARGMAX path only, and that is not a
        detail. It is an inference-time conservatism knob, not part of the
        action space: with three actions an untrained policy's best probability
        is 0.333, so applying a 0.55 floor while sampling means a fresh policy
        takes no action at all, produces no decisions, and a policy-gradient
        optimiser never sees a gradient. (Measured: six PPO iterations, every
        one reporting "no decisions in this batch".) When the policy has
        sampled an action, that action IS its choice.

        Returns `(side, confidence, chosen)` where `side` is +1/0/-1 and
        `chosen` is the action index actually taken, which is what the gradient
        needs - the argmax is not the choice when sampling.
        """
        Z = np.asarray(Z, dtype=float)
        if Z.ndim != 2 or not len(Z):
            return (np.zeros(0, dtype=np.int8), np.zeros(0), np.zeros(0, dtype=np.int8))
        P = self.entry_probs(Z)
        if rng is None:
            k = np.argmax(P, axis=1)
        else:
            # One uniform draw per row against the cumulative distribution:
            # the vectorised equivalent of rng.choice per bar, which would be
            # a Python loop over 100,000 bars.
            cum = np.cumsum(P, axis=1)
            u = rng.random((len(P), 1))
            k = (u > cum).sum(axis=1)
            k = np.clip(k, 0, P.shape[1] - 1)
        conf = P[np.arange(len(P)), k]
        # +1 long, 0 flat, -1 short - as int8 so the signal builder can mask it
        side = np.where(k == 0, 1, np.where(k == 2, -1, 0)).astype(np.int8)
        if rng is None:
            side = np.where(conf >= float(min_confidence), side, 0).astype(np.int8)
        return side, conf, k.astype(np.int8)

    # -- the risk head ------------------------------------------------------

    @property
    def risk_head(self) -> bool:
        return bool(self.n_stop and self.n_rr)

    def risk_choice(self, z: np.ndarray, rng=None) -> tuple[int, int]:
        """Which stop multiple and which R:R, as indices into the admin's menu.

        Indices, not values: the policy never holds the numbers, only a
        position in a list it was given. A menu that changes between training
        and serving is caught by the registry's content hash rather than
        silently reinterpreted.
        """
        if not self.risk_head:
            return 0, 0
        e = self.encode(np.asarray(z, dtype=float).ravel())
        ps = softmax(e @ self.W_stop + self.b_stop)
        pr = softmax(e @ self.W_rr + self.b_rr)
        if rng is None:
            return int(np.argmax(ps)), int(np.argmax(pr))
        return (int(rng.choice(len(ps), p=ps)), int(rng.choice(len(pr), p=pr)))

    def risk_choices(self, Z: np.ndarray, rng=None) -> tuple[np.ndarray, np.ndarray]:
        """Vectorised over every bar, like `entry_decisions` and for the same
        reason: an episode is tens of thousands of bars."""
        Z = np.asarray(Z, dtype=float)
        if not self.risk_head or Z.ndim != 2 or not len(Z):
            return np.zeros(len(Z) if Z.ndim == 2 else 0, dtype=np.int64), \
                np.zeros(len(Z) if Z.ndim == 2 else 0, dtype=np.int64)
        E = self.encode(Z)
        PS = np.apply_along_axis(softmax, 1, E @ self.W_stop + self.b_stop)
        PR = np.apply_along_axis(softmax, 1, E @ self.W_rr + self.b_rr)
        if rng is None:
            return np.argmax(PS, axis=1), np.argmax(PR, axis=1)
        us = rng.random((len(PS), 1))
        ur = rng.random((len(PR), 1))
        si = np.clip((us > np.cumsum(PS, axis=1)).sum(axis=1), 0, PS.shape[1] - 1)
        ri = np.clip((ur > np.cumsum(PR, axis=1)).sum(axis=1), 0, PR.shape[1] - 1)
        return si, ri

    # -- the exit head ------------------------------------------------------

    def act_exit(self, z: np.ndarray, pos: np.ndarray,
                 min_confidence: float = 0.55) -> tuple[str, float, np.ndarray]:
        """One open position, on one closed bar.

        Returns HOLD unless something else is both the argmax and confident.
        That default matters: the stop, the target, the trailing stop and
        `max_hold_bars` are still the backstop, and the agent's job is to act
        EARLIER, never to replace them. An unconfident exit head is therefore
        harmless rather than disruptive.
        """
        if not self.exit_head:
            return HOLD, 0.0, np.array([1.0, 0.0, 0.0, 0.0])
        x = np.concatenate([self.encode(np.asarray(z, dtype=float).ravel()),
                            np.asarray(pos, dtype=float).ravel()])
        p = softmax(x @ self.W_exit + self.b_exit)
        k = int(np.argmax(p))
        conf = float(p[k])
        if conf < float(min_confidence):
            return HOLD, conf, p
        return EXIT_ACTIONS[k], conf, p

    # -- one flat parameter vector ------------------------------------------

    @property
    def shapes(self) -> list[tuple[str, tuple[int, ...]]]:
        out = []
        if self.hidden:
            out += [("W_hidden", self.W_hidden.shape), ("b_hidden", self.b_hidden.shape)]
        out += [("W_entry", self.W_entry.shape), ("b_entry", self.b_entry.shape)]
        if self.risk_head:
            out += [("W_stop", self.W_stop.shape), ("b_stop", self.b_stop.shape),
                    ("W_rr", self.W_rr.shape), ("b_rr", self.b_rr.shape)]
        if self.exit_head:
            out += [("W_exit", self.W_exit.shape), ("b_exit", self.b_exit.shape)]
        return out

    @property
    def n_params(self) -> int:
        return int(sum(int(np.prod(s)) for _, s in self.shapes))

    def to_flat(self) -> np.ndarray:
        return np.concatenate([getattr(self, n).ravel() for n, _ in self.shapes]) \
            if self.shapes else np.zeros(0)

    def from_flat(self, theta: np.ndarray) -> "LinearPolicy":
        theta = np.asarray(theta, dtype=float).ravel()
        if len(theta) != self.n_params:
            raise ValueError(f"expected {self.n_params} parameters, got {len(theta)}")
        i = 0
        for name, shape in self.shapes:
            k = int(np.prod(shape))
            setattr(self, name, theta[i:i + k].reshape(shape).copy())
            i += k
        return self

    def copy(self) -> "LinearPolicy":
        return LinearPolicy.from_dict(self.to_dict())

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        d = {"n_features": self.n_features, "n_position": self.n_position,
             "hidden": self.hidden, "exit_head": self.exit_head, "seed": self.seed,
             "n_stop": self.n_stop, "n_rr": self.n_rr,
             "trained_iterations": self.trained_iterations}
        for name in ("W_hidden", "b_hidden", "W_entry", "b_entry", "W_exit", "b_exit",
                     "W_stop", "b_stop", "W_rr", "b_rr"):
            arr = getattr(self, name)
            d[name] = {"shape": list(arr.shape), "data": [float(v) for v in arr.ravel()]}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LinearPolicy":
        p = cls(n_features=int(d["n_features"]), n_position=int(d.get("n_position", 7)),
                hidden=int(d.get("hidden", 0)), exit_head=bool(d.get("exit_head", True)),
                n_stop=int(d.get("n_stop", 0)), n_rr=int(d.get("n_rr", 0)),
                seed=int(d.get("seed", 0)))
        p.trained_iterations = int(d.get("trained_iterations", 0))
        for name in ("W_hidden", "b_hidden", "W_entry", "b_entry", "W_exit", "b_exit",
                     "W_stop", "b_stop", "W_rr", "b_rr"):
            blob = d.get(name)
            if not blob:
                continue
            arr = np.asarray(blob.get("data") or [], dtype=float)
            p.__setattr__(name, arr.reshape(tuple(blob.get("shape") or (len(arr),))))
        return p

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    # -- readability --------------------------------------------------------

    def feature_importance(self, names) -> list[tuple[str, float]]:
        """Which features move the long/short decision, most first.

        For a linear head this is exact: the difference between the long and
        short weights IS the feature's effect on the decision. With a hidden
        layer it is the product of the two layers, which is an approximation,
        and `distill.py` checks any resulting charter by replaying the policy's
        decisions rather than trusting this ranking.
        """
        # long-minus-short on the encoder's own basis
        w = self.W_entry[:, 0] - self.W_entry[:, 2]
        if self.hidden:
            w = self.W_hidden @ w
        w = np.asarray(w, dtype=float).ravel()
        names = list(names)[:len(w)]
        pairs = sorted(zip(names, w), key=lambda kv: -abs(kv[1]))
        return [(n, float(v)) for n, v in pairs]

    def is_flat_everywhere(self) -> bool:
        """A zero policy. Worth naming: it is the untrained state, and a
        trainer that reports a result from one has found nothing."""
        return bool(np.allclose(self.W_entry, 0.0) and np.allclose(self.b_entry, 0.0))
