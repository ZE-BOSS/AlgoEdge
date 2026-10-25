"""
backend/kronos_service/config.py

One frozen description of "which Kronos, sampled how". Precompute, the
evaluation harness, the gate and (later) the live service all read this, and
`model_id` is stamped on every feature row and every gated trade, so a result
can always be traced to the exact checkpoint and sampling settings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Which tokenizer and context each model was released with (model card).
# Kronos-base is deliberately absent: 102M params is not for a 2-vCPU VPS.
MODELS: dict[str, dict] = {
    "Kronos-mini": {"tokenizer": "Kronos-Tokenizer-2k", "max_context": 2048, "params": "4.1M"},
    "Kronos-small": {"tokenizer": "Kronos-Tokenizer-base", "max_context": 512, "params": "24.7M"},
}

MODEL_ROOT = Path("models/kronos")


@dataclass(frozen=True)
class KronosConfig:
    model: str = "Kronos-small"
    lookback: int = 400          # bars of history per forecast (<= max_context)
    pred_len: int = 12           # forecast horizon, in bars
    sample_count: int = 5        # Monte Carlo paths; cost is ~linear in this
    temperature: float = 1.0
    top_p: float = 0.9
    seed: int = 1234             # with the symbol and bar time -> per-path RNG
    threads: int = 1             # torch intra-op threads; 1 on the VPS
    model_root: str = str(MODEL_ROOT)

    def __post_init__(self) -> None:
        if self.model not in MODELS:
            raise ValueError(f"unknown Kronos model {self.model!r}; one of {sorted(MODELS)}")
        if not 16 <= self.lookback <= self.max_context:
            raise ValueError(f"lookback {self.lookback} must be 16..{self.max_context}")
        if self.pred_len < 1 or self.sample_count < 1:
            raise ValueError("pred_len and sample_count must be >= 1")

    @property
    def tokenizer(self) -> str:
        return MODELS[self.model]["tokenizer"]

    @property
    def max_context(self) -> int:
        return MODELS[self.model]["max_context"]

    def model_dir(self) -> Path:
        return Path(self.model_root) / self.model

    def tokenizer_dir(self) -> Path:
        return Path(self.model_root) / self.tokenizer

    def revision(self) -> str:
        """The Hugging Face commit the local weights were downloaded at."""
        try:
            return (self.model_dir() / "REVISION").read_text().strip()[:8]
        except OSError:
            return "unknown"

    @property
    def model_id(self) -> str:
        """Checkpoint + every setting that changes the output, hashed short.

        Two feature files with the same model_id were produced by the same
        model sampled the same way - the parity guarantee between precompute
        and live rests on this string."""
        blob = json.dumps({k: v for k, v in asdict(self).items()
                           if k not in ("threads", "model_root")}, sort_keys=True)
        h = hashlib.sha256((blob + self.revision()).encode()).hexdigest()[:8]
        return f"{self.model}@{self.revision()}-{h}"
