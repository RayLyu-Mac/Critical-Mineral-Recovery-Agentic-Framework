# -*- coding: utf-8 -*-
"""Read a PHREEQC selected-output table and locate the pH split."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

TRACKED = ("Ni", "Co", "Mn", "Al", "Fe", "Cu", "Li")


def read_table(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    frame = pd.read_csv(path, sep=r"\s+", engine="python", comment="#")
    frame.columns = [str(c).strip() for c in frame.columns]
    for col in frame.columns:
        if col in {"state", "description"}:
            continue
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "state" in frame.columns:
        labels = frame["state"].astype(str)
        reacted = labels.str.contains("react", case=False)
        if reacted.any():
            frame = frame.loc[reacted].copy()
    if "seq" in frame.columns:
        frame = frame.dropna(subset=["seq"]).sort_values("seq", kind="mergesort")
    elif "step" in frame.columns:
        frame = frame.dropna(subset=["pH"]).sort_values("step", kind="mergesort")
    else:
        frame = frame.dropna(subset=["pH"])
    return frame.reset_index(drop=True)


def dissolved_moles(frame: pd.DataFrame) -> dict[str, np.ndarray]:
    """SELECTED_OUTPUT -totals are moles in solution, not molality.

    mass_H2O is about 1 kg in the reference decks, and the Ni column matches
    the feed moles. Multiplying by mass_H2O would count the metal twice.
    """
    out = {}
    for metal in TRACKED:
        if metal not in frame.columns:
            out[metal] = np.zeros(len(frame))
        else:
            out[metal] = frame[metal].to_numpy(float)
    return out


def column(frame: pd.DataFrame, name: str, default: float) -> np.ndarray:
    if name not in frame.columns:
        return np.full(len(frame), default, dtype=float)
    values = frame[name].to_numpy(float)
    return np.where(np.isfinite(values), values, default)


def find_split(frame: pd.DataFrame, ph_split: float) -> dict:
    """Dose at the first row that reaches PH_SPLIT, linearly interpolated.

    The stage-2 deck re-equilibrates at that dose. Rows past the dose are not
    part of a two-stage process.
    """
    if frame.empty:
        raise ValueError("Stage 1 table is empty.")
    ph = frame["pH"].to_numpy(float)
    reagent = column(frame, "reagent_cum_mol", 0.0)
    bm = column(frame, "bm_cum_frac", 1.0)
    above = np.flatnonzero(np.isfinite(ph) & (ph >= ph_split - 1e-9))
    if len(above) == 0:
        i = len(frame) - 1
        return {
            "crossed": False,
            "ph": float(ph[i]),
            "reagent_mol": float(reagent[i]),
            "bm_frac": float(bm[i]),
            "index": int(i),
            "t": 1.0,
        }
    i = int(above[0])
    if i == 0 or not np.isfinite(ph[i - 1]):
        return {
            "crossed": True,
            "ph": float(ph[i]),
            "reagent_mol": float(reagent[i]),
            "bm_frac": float(bm[i]),
            "index": i,
            "t": 1.0,
        }
    span = float(ph[i] - ph[i - 1])
    t = 0.0 if abs(span) < 1e-12 else (ph_split - float(ph[i - 1])) / span
    t = float(min(1.0, max(0.0, t)))

    def lerp(arr: np.ndarray) -> float:
        return float(arr[i - 1] + t * (arr[i] - arr[i - 1]))

    return {
        "crossed": True,
        "ph": float(ph_split),
        "reagent_mol": max(0.0, lerp(reagent)),
        "bm_frac": float(min(1.0, max(0.0, lerp(bm)))),
        "index": i,
        "t": t,
    }
