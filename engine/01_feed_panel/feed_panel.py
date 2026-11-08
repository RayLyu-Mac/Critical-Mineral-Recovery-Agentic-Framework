# -*- coding: utf-8 -*-
"""Ligand-free black-mass feed. Row 1 is the fixed sulfate recipe (or its chloride twin).

Extra rows, when PANEL_N > 1, are seeded draws around that recipe.
ratio is 0 and ligand is 'no ligand' on every row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
import config as cfg  # noqa: E402

_SALT_DATA = (
    {"name": "Al2(SO4)3", "g_per_L": 9.8, "MW": 342.15, "metal": "Al", "atoms": 2},
    {"name": "FeSO4·7H2O", "g_per_L": 3.9, "MW": 278.01, "metal": "Fe", "atoms": 1},
    {"name": "CuSO4", "g_per_L": 9.2, "MW": 159.61, "metal": "Cu", "atoms": 1},
    {"name": "CoSO4·7H2O", "g_per_L": 55.8, "MW": 281.10, "metal": "Co", "atoms": 1},
    {"name": "NiSO4·6H2O", "g_per_L": 102.4, "MW": 262.84, "metal": "Ni", "atoms": 1},
    {"name": "MnSO4·H2O", "g_per_L": 29.4, "MW": 169.02, "metal": "Mn", "atoms": 1},
    {"name": "Li2SO4·H2O", "g_per_L": 12.9, "MW": 127.96, "metal": "Li", "atoms": 2},
)
SULFATE_COLS = (
    "Al_sulfate_mol",
    "Fe_sulfate_mol",
    "Cu_sulfate_mol",
    "Co_sulfate_mol",
    "Ni_sulfate_mol",
    "Mn_sulfate_mol",
)
_CHLORIDE = (
    {"metal": "Al", "name": "AlCl3·6H2O", "MW": 241.43, "per_sulfate": 2.0},
    {"metal": "Fe", "name": "FeCl3·6H2O", "MW": 270.30, "per_sulfate": 1.0},
    {"metal": "Cu", "name": "CuCl2·2H2O", "MW": 170.48, "per_sulfate": 1.0},
    {"metal": "Co", "name": "CoCl2·6H2O", "MW": 237.93, "per_sulfate": 1.0},
    {"metal": "Ni", "name": "NiCl2·6H2O", "MW": 237.69, "per_sulfate": 1.0},
    {"metal": "Mn", "name": "MnCl2·4H2O", "MW": 197.90, "per_sulfate": 1.0},
    {"metal": "Li", "name": "LiCl·H2O", "MW": 60.41, "per_sulfate": 2.0},
)


def recipe_sulfate() -> dict[str, float]:
    moles = {row["name"]: row["g_per_L"] / row["MW"] for row in _SALT_DATA}
    out = {f"{row['metal']}_sulfate_mol": moles[row["name"]] for row in _SALT_DATA if row["metal"] != "Li"}
    out["Li_salt_mol"] = moles["Li2SO4·H2O"]
    return out


def _nmc(row: dict[str, float]) -> float:
    return float(row["Ni_sulfate_mol"] + row["Co_sulfate_mol"] + row["Mn_sulfate_mol"])


def _as_chloride(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for salt in _CHLORIDE:
        metal = salt["metal"]
        base = out["Li_salt_mol"] if metal == "Li" else out[f"{metal}_sulfate_mol"]
        out[f"{metal}_chloride_mol"] = float(salt["per_sulfate"]) * base
    out["salt_reference"] = cfg.CHLORIDE_REFERENCE
    return out.drop(columns=[*SULFATE_COLS, "Li_salt_mol"])


def _samples(n: int, seed: int) -> list[dict[str, float]]:
    rng = np.random.default_rng(seed)
    base = recipe_sulfate()
    rows = []
    for _ in range(n):
        row = {key: float(base[key] * (10 ** rng.uniform(-0.3, 0.3))) for key in (*SULFATE_COLS, "Li_salt_mol")}
        rows.append(row)
    return rows


def build_panel(panel_n: int, anion: str) -> pd.DataFrame:
    if anion not in {"sulfate", "chloride"}:
        raise ValueError("anion must be sulfate or chloride")
    if panel_n < 1:
        raise ValueError("panel_n must be >= 1")
    bodies = [recipe_sulfate()]
    if panel_n > 1:
        bodies.extend(_samples(panel_n - 1, int(cfg.SEED)))
    rows = []
    for i, body in enumerate(bodies, start=1):
        nmc = _nmc(body)
        rows.append(
            {
                **body,
                "comp_id": i,
                "parent_comp_id": 1,
                "chem": "RECIPE" if i == 1 else "DRAW",
                "li_ratio": (2.0 * body["Li_salt_mol"] / nmc) if nmc else 0.0,
                "nmc_total_mol": nmc,
                "ligand": "no ligand",
                "ratio": 0.0,
                "salt_reference": "feed_panel recipe, 1 L salt masses",
            }
        )
    frame = pd.DataFrame(rows)
    if anion == "chloride":
        frame = _as_chloride(frame)
    return frame


def write_compositions(frame: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path
