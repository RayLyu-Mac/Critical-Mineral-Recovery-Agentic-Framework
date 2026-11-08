# -*- coding: utf-8 -*-
"""Load engine/ref/02_ask_thermo/ask_thermo.py. The ligand is always 'no ligand'."""
from __future__ import annotations

import copy
import importlib.util
import sys

from paths import REF_THERMO

_MOD = None
_COLLECT: dict[tuple, dict] = {}


def module():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("econ_ref_ask_thermo", REF_THERMO)
        if spec is None or spec.loader is None:
            raise FileNotFoundError(f"ask_thermo.py not found: {REF_THERMO}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def collect(anion: str, reagent: str, metals: list[str]) -> dict:
    """Thermo for salts and one reagent. Ligand is always 'no ligand'.

    The same anion, reagent, and metals are read many times in one case.
    The cache holds the source dict. Callers receive a copy.
    """
    key = (anion, reagent, tuple(metals))
    cached = _COLLECT.get(key)
    if cached is None:
        cached = module().collect("no ligand", [anion], reagent, list(metals))
        _COLLECT[key] = cached
    return copy.deepcopy(cached)


def usable_reagent_names() -> list[str]:
    """Enabled reagents with a reaction and no companion species."""
    thermo = module()
    metals = list(thermo._METALS)
    names: list[str] = []
    for name in thermo.REAGENTS:
        doc = thermo._reagent_thermo(name, metals)
        if doc.get("enabled") and not doc.get("companion") and doc.get("reaction"):
            phases = [p for p in doc.get("phases", []) if p.get("phase") and p.get("log_k") is not None]
            if phases:
                names.append(name)
    return sorted(names)
