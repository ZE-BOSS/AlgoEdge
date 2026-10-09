"""
backend/rl/director.py

The supervised half: a frozen model that calls direction, with the RL policy
deciding what to do about it.

WHY SPLIT IT THIS WAY
---------------------
RL does not clearly beat supervised learning at calling direction - a 2022
comparative study found supervised methods ahead on crypto, and direction is a
prediction problem, which wants a predictor. RL's measured edge is cost-aware
sequential decisions: hold, exit, size. That is a control problem, which wants
a controller.

So: **the director predicts, the policy decides.** The director says "up, with
this much confidence"; the RL policy decides whether that is worth taking here,
where the stop goes, what to aim at, and when to leave. Neither is asked to do
the other's job.

TRAIN IT FIRST, THEN FREEZE IT
------------------------------
`fit` is called once, on the training fold, and the weights do not move again.
Training both halves at once lets the RL half learn to exploit the director's
mistakes rather than the market's - it would find the regions where the
director is reliably wrong and trade those, which looks like skill and
generalises to nothing.

WHAT IT PREDICTS, AND WHY THAT LABEL
------------------------------------
The sign of the forward return over `horizon` bars, net of a cost floor. Not
the magnitude: magnitude is dominated by volatility, so a model fitted to it
spends its capacity predicting how big the next move is rather than which way.
The cost floor matters too - a model trained to call every 0.1-pip wobble
learns noise, so moves smaller than the spread are labelled "no call" and
dropped from the fit.

THE LABEL IS THE MOST LEAK-PRONE OBJECT IN THE SYSTEM
-----------------------------------------------------
It is literally the future. Three guards, all structural:

  * `fit` takes an explicit row index and uses NOTHING outside it;
  * the last `horizon` rows of any fold are dropped, because their labels
    reach past the end of it - the same purge the CPCV folds already apply;
  * the scaler is the AGENT'S, fitted on the agent's training fold, not
    re-fitted here on whatever rows were handed in.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import numpy as np

# Below this, a move is not a call - it is noise inside the spread.
DEFAULT_COST_R = 0.10


@dataclass
class Director:
    """Regularised logistic regression on the agent's own features.

    Logistic regression and not something deeper, for the same reason the
    policy is linear: the charter has to state what the agent does, and a
    direction model whose reasoning cannot be read makes the charter a
    description of a black box. The coefficients here are the statement.

    Fitted by gradient descent in numpy - scikit-learn is not installed, and
    this is forty lines.
    """
    names: tuple[str, ...] = ()
    w: np.ndarray = field(default_factory=lambda: np.zeros(0))
    b: float = 0.0
    horizon: int = 12
    cost_r: float = DEFAULT_COST_R
    fitted_on: str = ""
    n_train: int = 0
    train_accuracy: float = 0.0

    @property
    def fitted(self) -> bool:
        return self.w.size > 0

    # -- labels -------------------------------------------------------------

    @staticmethod
    def labels(close: np.ndarray, atr: np.ndarray, horizon: int,
               cost_r: float) -> tuple[np.ndarray, np.ndarray]:
        """`(y, usable)` - the sign of the forward move, and where it is a call.

        Measured in ATRs rather than price, so one threshold works across
        EURUSD and Crash 1000. The last `horizon` rows are never usable: their
        label reaches past the end of the array.
        """
        close = np.asarray(close, dtype=float)
        atr = np.maximum(np.asarray(atr, dtype=float), 1e-12)
        n = len(close)
        forward = np.full(n, np.nan)
        if n > horizon:
            forward[:-horizon] = (close[horizon:] - close[:-horizon]) / atr[:-horizon]
        y = (forward > 0).astype(float)
        usable = np.isfinite(forward) & (np.abs(forward) >= float(cost_r))
        return y, usable

    # -- fitting ------------------------------------------------------------

    def fit(self, Z: np.ndarray, close: np.ndarray, atr: np.ndarray, rows: np.ndarray,
            names=None, *, l2: float = 1e-3, iterations: int = 400, lr: float = 0.5,
            fitted_on: str = "") -> "Director":
        """Fit on `rows` and nothing else.

        `rows` is the agent's TRAINING fold. Rows whose label window runs past
        the end of the fold are dropped here rather than trusted to the caller:
        a label that reaches into the test fold is the leak this whole module
        has to avoid, and it is one line.
        """
        Z = np.asarray(Z, dtype=float)
        rows = np.asarray(rows, dtype=np.int64)
        y, usable = self.labels(close, atr, self.horizon, self.cost_r)

        # Purge: a row whose label reaches beyond the fold cannot be used, even
        # if the label itself is finite.
        keep = rows[(rows + self.horizon) < len(close)]
        keep = keep[usable[keep]]
        if len(keep) < 50:
            raise ValueError(
                f"only {len(keep)} usable rows - a direction model fitted on that "
                f"is noise with coefficients")

        X, t = Z[keep], y[keep]
        self.names = tuple(names or self.names or ())
        w = np.zeros(X.shape[1])
        b = 0.0
        for _ in range(int(iterations)):
            p = 1.0 / (1.0 + np.exp(-np.clip(X @ w + b, -500, 500)))
            err = p - t
            w -= lr * (X.T @ err / len(X) + l2 * w)
            b -= lr * float(err.mean())
        self.w, self.b = w, float(b)
        self.n_train = int(len(keep))
        self.fitted_on = fitted_on
        self.train_accuracy = float(np.mean((self.probability(X) > 0.5) == (t > 0.5)))
        return self

    # -- prediction ---------------------------------------------------------

    def probability(self, Z: np.ndarray) -> np.ndarray:
        """P(up) per row."""
        if not self.fitted:
            raise RuntimeError("the director has not been fitted")
        Z = np.asarray(Z, dtype=float)
        if Z.ndim == 1:
            Z = Z[None, :]
        return 1.0 / (1.0 + np.exp(-np.clip(Z @ self.w + self.b, -500, 500)))

    def call(self, Z: np.ndarray, min_confidence: float = 0.55) -> np.ndarray:
        """+1 up, -1 down, 0 no call. The feature the policy actually sees.

        A "no call" is a real answer and the common one: a director that always
        has an opinion is a director that has none.
        """
        p = self.probability(Z)
        out = np.zeros(len(p), dtype=np.int8)
        out[p >= float(min_confidence)] = 1
        out[p <= 1.0 - float(min_confidence)] = -1
        return out

    def confidence(self, Z: np.ndarray) -> np.ndarray:
        """0..1, how far from a coin flip. Fed to the policy as a feature."""
        return np.abs(self.probability(Z) - 0.5) * 2.0

    # -- readability --------------------------------------------------------

    def importance(self, top: int = 8) -> list[tuple[str, float]]:
        """Which features move the call. For the charter, so the direction
        half is as readable as the policy half."""
        names = list(self.names) or [f"f{i}" for i in range(len(self.w))]
        pairs = sorted(zip(names, self.w), key=lambda kv: -abs(kv[1]))
        return [(n, float(v)) for n, v in pairs[:top]]

    def english(self) -> str:
        if not self.fitted:
            return "no direction model"
        bits = ", ".join(
            f"{n} {'up' if v > 0 else 'down'}" for n, v in self.importance(4))
        return (f"A direction model fitted on {self.n_train} bars "
                f"({self.train_accuracy:.0%} in-sample accuracy, "
                f"{self.horizon}-bar horizon). It leans on {bits}.")

    # -- persistence --------------------------------------------------------

    def to_dict(self) -> dict:
        return {"names": list(self.names), "w": [float(v) for v in self.w],
                "b": float(self.b), "horizon": int(self.horizon),
                "cost_r": float(self.cost_r), "fitted_on": self.fitted_on,
                "n_train": int(self.n_train),
                "train_accuracy": float(self.train_accuracy)}

    @classmethod
    def from_dict(cls, d: dict) -> "Director":
        out = cls(names=tuple(d.get("names") or ()), horizon=int(d.get("horizon", 12)),
                  cost_r=float(d.get("cost_r", DEFAULT_COST_R)),
                  fitted_on=str(d.get("fitted_on", "")),
                  n_train=int(d.get("n_train", 0)),
                  train_accuracy=float(d.get("train_accuracy", 0.0)))
        out.w = np.asarray(d.get("w") or [], dtype=float)
        out.b = float(d.get("b", 0.0))
        return out

    @property
    def content_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# -- the join -----------------------------------------------------------------


def augment(Z: np.ndarray, director: "Director | None",
            min_confidence: float = 0.55) -> np.ndarray:
    """Add the director's call and confidence as two extra observation columns.

    Two columns, not a replacement. The policy keeps every market feature it
    had and gains the director's opinion alongside - so it can learn WHERE to
    believe it, which is the whole point of putting a controller on top of a
    predictor rather than simply trading the predictor's output.

    With no director the matrix is returned unchanged, so a mode-A agent is
    byte-identical to one built before this existed.
    """
    Z = np.asarray(Z, dtype=float)
    if director is None or not director.fitted or not len(Z):
        return Z
    call = director.call(Z, min_confidence).astype(float)
    conf = director.confidence(Z)
    return np.column_stack([Z, call, conf])


DIRECTOR_FEATURES = ("director_call", "director_confidence")
