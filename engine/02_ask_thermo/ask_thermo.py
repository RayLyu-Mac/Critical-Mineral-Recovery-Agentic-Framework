# -*- coding: utf-8 -*-
"""Salt and reagent thermo for a ligand-free deck. Delegates to engine/ref/02_ask_thermo/ask_thermo.py."""
from __future__ import annotations

import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
import ask_thermo_loader as loader  # noqa: E402


def collect(anion: str, reagent: str, metals: list[str]) -> dict:
    return loader.collect(anion, reagent, metals)


def usable_reagent_names() -> list[str]:
    return loader.usable_reagent_names()
