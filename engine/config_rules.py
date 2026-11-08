# -*- coding: utf-8 -*-
"""Validate and write MODIFY fields. Both agent loops use this module."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import config as cfg

_ASSIGN = re.compile(rf"^({'|'.join(cfg.MODIFY)})\s*=\s*.*$", re.M)


class ConfigError(Exception):
    """The proposal cannot be written. The loop may ask again."""


def current_fields(text: str) -> dict[str, object]:
    found = {match.group(1): match.group(0).split("=", 1)[1].strip() for match in _ASSIGN.finditer(text)}
    missing = [name for name in cfg.MODIFY if name not in found]
    if missing:
        raise ConfigError(f"config.py is missing {', '.join(missing)}.")
    shown: dict[str, object] = {}
    for name, raw in found.items():
        try:
            shown[name] = ast.literal_eval(raw)
        except (SyntaxError, ValueError):
            shown[name] = raw
    return shown


def _usable_reagents() -> list[str]:
    from ask_thermo_loader import usable_reagent_names

    return usable_reagent_names()


def _checked(name: str, value: object, usable: list[str]) -> object:
    if name not in cfg.MODIFY:
        raise ConfigError(f"The model cannot change {name}.")
    if name == "TEMP_C":
        number = _float(name, value)
        if not cfg.TEMP_C_MIN <= number <= cfg.TEMP_C_MAX:
            raise ConfigError(f"TEMP_C must be {cfg.TEMP_C_MIN} to {cfg.TEMP_C_MAX}. Received {value!r}.")
        return round(number, 2)
    if name == "WATER_KG":
        number = _float(name, value)
        if not cfg.WATER_KG_MIN <= number <= cfg.WATER_KG_MAX:
            raise ConfigError(f"WATER_KG must be {cfg.WATER_KG_MIN} to {cfg.WATER_KG_MAX}. Received {value!r}.")
        return round(number, 4)
    if name == "ANION":
        text = str(value).strip().lower()
        if text not in {"sulfate", "chloride"}:
            raise ConfigError(f"ANION must be sulfate or chloride. Received {value!r}.")
        return text
    if name == "BM_ADD":
        text = str(value).strip().lower()
        allowed = tuple(getattr(cfg, "BM_ADD_CHOICES", ("batch", "staged", "continuous")))
        if text not in allowed:
            raise ConfigError(f"BM_ADD must be {', '.join(allowed)}. Received {value!r}.")
        return text
    if name == "BM_BATCHES":
        number = _int(name, value)
        if number < 1 or number > cfg.BM_BATCHES_MAX:
            raise ConfigError(f"BM_BATCHES must be 1 to {cfg.BM_BATCHES_MAX}.")
        return number
    if name == "STAGE":
        number = _int(name, value)
        allowed = {int(item) for item in getattr(cfg, "STAGE_CHOICES", (1, 2))}
        if number not in allowed:
            raise ConfigError(f"STAGE must be {' or '.join(str(item) for item in sorted(allowed))}.")
        return number
    if name in {"REAGENT_1", "REAGENT_2"}:
        text = str(value).strip()
        allowed = tuple(getattr(cfg, f"{name}_CHOICES", usable))
        if text not in allowed or text not in usable:
            raise ConfigError(f"{name} {text!r} is not usable. Usable: {', '.join(allowed)}.")
        return text
    if name in {"AMOUNT_1", "AMOUNT_2"}:
        number = _float(name, value)
        lo = getattr(cfg, f"{name}_MIN", None)
        hi = float(getattr(cfg, f"{name}_MAX", cfg.AMOUNT_MAX))
        if lo is None:
            if number <= 0 or number > hi:
                raise ConfigError(f"{name} must be > 0 and <= {hi}.")
        elif not float(lo) <= number <= hi:
            raise ConfigError(f"{name} must be {lo} to {hi}. Received {value!r}.")
        return round(number, 4)
    if name in {"ADD_1", "ADD_2"}:
        text = str(value).strip().lower()
        allowed = tuple(getattr(cfg, f"{name}_CHOICES", ("once", "continuous")))
        if text not in allowed:
            raise ConfigError(f"{name} must be {', '.join(allowed)}. Received {value!r}.")
        return text
    if name in {"STEPS_1", "STEPS_2"}:
        number = _int(name, value)
        lo = int(getattr(cfg, f"{name}_MIN", 1))
        hi = int(getattr(cfg, f"{name}_MAX", cfg.STEPS_MAX))
        if number < lo or number > hi:
            raise ConfigError(f"{name} must be {lo} to {hi}.")
        return number
    raise ConfigError(f"Unknown config field {name}.")


def _float(name: str, value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a number. Received {value!r}.") from None


def _int(name: str, value: object) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be an integer. Received {value!r}.") from None
    if number != int(number):
        raise ConfigError(f"{name} must be an integer. Received {value!r}.")
    return int(number)


def _cross_check(fields: dict[str, object]) -> dict[str, object]:
    out = dict(fields)
    if out["BM_ADD"] == "batch":
        out["BM_BATCHES"] = 1
    elif out["BM_ADD"] == "staged" and int(out["BM_BATCHES"]) < 2:
        raise ConfigError("BM_BATCHES must be 2 or more when BM_ADD is staged.")
    if out["BM_ADD"] == "continuous" and out["ADD_1"] != "continuous":
        out["ADD_1"] = "continuous"
    return out


def resolved_fields(fields: dict[str, object], usable: list[str] | None = None) -> dict[str, object]:
    known = usable if usable is not None else _usable_reagents()
    checked = {name: _checked(name, fields[name], known) for name in cfg.MODIFY if name in fields}
    if set(checked) != set(cfg.MODIFY):
        missing = [name for name in cfg.MODIFY if name not in checked]
        raise ConfigError(f"Config is missing {', '.join(missing)}.")
    return _cross_check(checked)


def _literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, float):
        return repr(value)
    return str(int(value))


def apply_changes(text: str, raw: dict[str, object], usable: list[str] | None = None) -> str:
    unknown = [key for key in raw if key not in cfg.MODIFY]
    if unknown:
        raise ConfigError(f"Unknown config fields: {', '.join(unknown)}.")
    before = resolved_fields(current_fields(text), usable)
    merged = dict(before)
    merged.update(raw)
    after = resolved_fields(merged, usable)
    if after == before:
        raise ConfigError(f"The resolved config equals the current config. Change at least one of {', '.join(cfg.MODIFY)}.")
    updated = text
    for name in cfg.MODIFY:
        if before[name] == after[name]:
            continue
        line = f"{name} = {_literal(after[name])}"
        pattern = re.compile(rf"^{name}\s*=\s*.*$", re.M)
        if pattern.search(updated) is None:
            raise ConfigError(f"config.py has no assignment for {name}.")
        updated = pattern.sub(line, updated, count=1)
    return updated


def fields_key(fields: dict[str, object]) -> str:
    return json.dumps(resolved_fields(fields), sort_keys=True)


def load_module(path: Path):
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(f"econ_cfg_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"Cannot load config: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod
