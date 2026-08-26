"""
backend/core/schema_introspection.py

[7.1/H2] Generates a machine-readable parameter schema from the dataclasses
in core/config_schema.py and every strategy's params.py — `{key, group,
label, type, unit, help, default, affects}` per field — so the frontend can
render parameters from their actual backend meaning instead of a hand-
maintained, drift-prone label list (§5.2.1 of the master plan: "this kills
the whole class of frontend-label-drifted-from-backend-meaning bugs").

`help` is extracted from each field's PYTHON ATTRIBUTE DOCSTRING — the
existing convention already used everywhere in config_schema.py and every
strategy's params.py (a bare string literal immediately following a
dataclass field assignment). Python doesn't expose these at runtime by
default, so they're extracted via `ast` parsing of the class source — the
same technique Sphinx's `autodoc` uses for the same reason. Falls back to ""
(never guessed) when a field has no such docstring.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
import typing
from dataclasses import MISSING, fields, is_dataclass


def extract_field_docs(cls: type) -> dict[str, str]:
    """AST-based extraction of the string literal immediately following each dataclass field assignment."""
    try:
        source = textwrap.dedent(inspect.getsource(cls))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return {}

    class_node = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if class_node is None:
        return {}

    docs: dict[str, str] = {}
    body = class_node.body
    for i, node in enumerate(body):
        field_name = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            field_name = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            field_name = node.targets[0].id
        if field_name is None:
            continue
        if i + 1 < len(body):
            nxt = body[i + 1]
            if (
                isinstance(nxt, ast.Expr)
                and isinstance(nxt.value, ast.Constant)
                and isinstance(nxt.value.value, str)
            ):
                docs[field_name] = inspect.cleandoc(nxt.value.value)
    return docs


def _resolve_type_str(field_type: typing.Any) -> tuple[str, list[str] | None]:
    """Best-effort {type-name, enum-options} for the schema's "type" column."""
    origin = typing.get_origin(field_type)
    args = typing.get_args(field_type)

    if origin is typing.Literal:
        return "enum", [str(a) for a in args]
    if origin in (list, typing.List):
        return "list", None
    if origin in (dict, typing.Dict):
        return "map", None
    # `X | None` (UnionType) or `Optional[X]`
    if origin is not None and type(None) in args:
        inner = next((a for a in args if a is not type(None)), None)
        inner_name, inner_enum = _resolve_type_str(inner) if inner is not None else ("string", None)
        return inner_name, inner_enum

    type_name = getattr(field_type, "__name__", str(field_type))
    return {
        "str": "string", "int": "int", "float": "float", "bool": "bool",
    }.get(type_name, type_name), None


def _default_value(f: dataclasses.Field) -> typing.Any:
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:  # type: ignore[misc]
        try:
            return f.default_factory()  # type: ignore[misc]
        except Exception:
            return None
    return None


def build_dataclass_schema(cls: type, group: str) -> list[dict]:
    """One schema row per field of a single dataclass (RiskParams, VWAPParams, etc)."""
    if not is_dataclass(cls):
        return []
    docs = extract_field_docs(cls)
    rows = []
    for f in fields(cls):
        if f.name.startswith("_"):
            continue
        type_str, enum_options = _resolve_type_str(f.type if isinstance(f.type, type) else typing.get_type_hints(cls).get(f.name, str))
        default = _default_value(f)
        help_text = docs.get(f.name, "")
        row = {
            "key": f"{group}.{f.name}",
            "group": group,
            "label": f.name.replace("_", " ").strip().capitalize(),
            "type": type_str,
            "unit": None,
            "help": help_text,
            "default": default if not isinstance(default, dict) or default else default,
            "affects": [],
        }
        if enum_options:
            row["enum_options"] = enum_options
        rows.append(row)
    return rows


# Group name -> (dataclass, UserConfigV2 attribute path). Mirrors UserConfigV2's
# own field layout in core/config_schema.py so `group` always matches the real
# config path a value would be read/written at.
def build_full_schema() -> list[dict]:
    from backend.core.config_schema import (
        RiskParams, PropFirmParams, UserConfigV2,
    )
    from backend.strategies.strategy_apa.params import APAParams
    from backend.strategies.strategy_vwap.params import VWAPParams
    from backend.core.config_schema import CRTParams, HTFFVGFlipParams
    from backend.strategies.strategy_five_bias_ifvg.params import BiasIFVGParams
    from backend.strategies.strategy_six_ny_open_retest.params import NYOpenRetestParams
    from backend.core.config_schema import DriftJumpAlphaParams

    groups: list[tuple[type, str]] = [
        (RiskParams, "risk"),
        (PropFirmParams, "prop_firm"),
        (APAParams, "apa"),
        (VWAPParams, "vwap"),
        (CRTParams, "crt"),
        (HTFFVGFlipParams, "htf_fvg_flip"),
        (BiasIFVGParams, "bias_ifvg"),
        (NYOpenRetestParams, "ny_open_retest"),
        (DriftJumpAlphaParams, "drift_jump_alpha"),
    ]

    schema: list[dict] = []
    for cls, group in groups:
        schema.extend(build_dataclass_schema(cls, group))
    return schema
