# -*- coding: utf-8 -*-
"""Seven-metal cuts and net-profit score for one ligand-free PHREEQC run.

Each solid metal except lithium can take one cut. The cut count is the
length of that metal list. Stage 1 places every cut on the reagent-1 curve.
Stage 2 places each metal on reagent 1 or reagent 2 by revenue.
A cut with purity below PURITY_THRESHOLD earns no revenue.
Lithium uses the better of one solid cut and the final brine, not both.
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np

from curve_io import TRACKED, column, dissolved_moles, read_table

INDEX = {metal: i for i, metal in enumerate(TRACKED)}


def _solid_metals() -> tuple[str, ...]:
    """Metals that can take a precipitate cut. Lithium is scored apart."""
    return tuple(metal for metal in TRACKED if metal != "Li")


def _empty_metal() -> dict:
    return {
        "cut": None,
        "cut_pH": None,
        "purity": 0.0,
        "recovery": 0.0,
        "recovered_mol": 0.0,
        "revenue_usd": 0.0,
        "usable": False,
    }


def _grid(pH: np.ndarray) -> np.ndarray:
    finite = pH[np.isfinite(pH)]
    if len(finite) == 0:
        return np.array([])
    lo, hi = float(finite.min()), float(finite.max())
    if hi - lo < 1e-6:
        return np.array([round(lo, 2)])
    grid = np.round(np.arange(lo, hi + 0.011, 0.25), 2)
    if len(grid) > 16:
        grid = np.round(np.linspace(lo, hi, 16), 2)
    return np.unique(grid)


def _snap(pH: np.ndarray, targets: np.ndarray) -> np.ndarray:
    ge = pH[None, None, :] >= (targets[:, :, None] - 1e-9)
    idx = ge.argmax(axis=2)
    never = ~ge.any(axis=2)
    return np.where(never, len(pH) - 1, idx).astype(int)


def _best_window(
    pH: np.ndarray,
    solid: np.ndarray,
    feed: dict[str, float],
    metals: tuple[str, ...],
    prices: dict[str, float],
    mw: dict[str, float],
    threshold: float,
) -> dict:
    blank = {"status": "EMPTY", "assignment": "", "cuts": [], "metals": {m: _empty_metal() for m in metals}}
    mask = np.isfinite(pH)
    pH = np.asarray(pH, float)[mask]
    solid = np.asarray(solid, float)[mask]
    if len(pH) == 0 or solid.size == 0:
        return blank
    grid = _grid(pH)
    if len(grid) == 0:
        return blank
    best_key = None
    best = None
    max_k = min(len(metals), len(pH))
    for k in range(max_k, 0, -1):
        combos = np.asarray(list(itertools.combinations(grid.tolist(), k)), dtype=float)
        if len(combos) == 0:
            continue
        idx = _snap(pH, combos)
        if k >= 2:
            rising = np.ones(len(combos), dtype=bool)
            for col in range(1, k):
                rising &= idx[:, col] > idx[:, col - 1]
        else:
            rising = np.ones(len(combos), dtype=bool)
        if not rising.any():
            continue
        snapped = solid[idx]
        prev = np.concatenate([np.zeros((len(idx), 1, solid.shape[1])), snapped[:, :-1, :]], axis=1)
        inc = np.maximum(0.0, snapped - prev)
        inc[inc < 1e-8] = 0.0
        total = inc.sum(axis=2)
        metal_sets = list(itertools.combinations(metals, k))
        for chosen in metal_sets:
            for perm in itertools.permutations(chosen):
                revenue = np.zeros(len(combos))
                quality = np.zeros(len(combos))
                details = []
                for cut, metal in enumerate(perm):
                    j = INDEX[metal]
                    pur = np.zeros(len(combos))
                    np.divide(inc[:, cut, j], total[:, cut], out=pur, where=total[:, cut] > 1e-18)
                    base = float(feed.get(metal, 0.0))
                    taken = inc[:, cut, j]
                    if base > 1e-18:
                        taken = np.minimum(taken, base)
                    rec = np.zeros(len(combos)) if base <= 1e-18 else np.minimum(1.0, taken / base)
                    usable = pur >= threshold
                    revenue += np.where(usable, taken * mw[metal] * prices[metal], 0.0)
                    quality += pur * rec
                    details.append((metal, pur, rec, taken, usable))
                revenue = np.where(rising, revenue, -1.0)
                quality = np.where(rising, quality, -1.0)
                pick = int(np.argmax(np.where(rising, revenue + quality * 1e-6, -1.0)))
                if not rising[pick]:
                    continue
                key = (float(revenue[pick]), float(quality[pick]))
                if best_key is None or key >= best_key:
                    best_key = key
                    cuts = []
                    assigned = {}
                    for cut, (metal, pur, rec, taken, usable) in enumerate(details):
                        row_i = int(idx[pick, cut])
                        item = {
                            "cut": cut + 1,
                            "pH": float(pH[row_i]),
                            "target_pH": float(combos[pick, cut]),
                            "purity": float(pur[pick]),
                            "recovery": float(rec[pick]),
                            "recovered_mol": float(taken[pick]),
                            "usable": bool(usable[pick]),
                            "revenue_usd": float(taken[pick] * mw[metal] * prices[metal]) if usable[pick] else 0.0,
                        }
                        assigned[metal] = item
                        cuts.append({"cut": cut + 1, "pH": item["pH"], "metal": metal, **{k: item[k] for k in item if k not in {"cut", "pH", "metal"}}})
                    for metal in metals:
                        assigned.setdefault(metal, _empty_metal())
                    best = {
                        "status": "OK",
                        "assignment": ",".join(perm),
                        "cuts": cuts,
                        "metals": assigned,
                    }
    return best or blank


def _window_value(window: dict) -> tuple[float, float]:
    revenue = 0.0
    quality = 0.0
    for row in window["metals"].values():
        revenue += float(row["revenue_usd"])
        if row.get("usable"):
            quality += float(row["purity"]) * float(row["recovery"])
    return revenue, quality


def _best_two_windows(
    pH_a: np.ndarray,
    solid_a: np.ndarray,
    pH_b: np.ndarray,
    solid_b: np.ndarray,
    feed: dict[str, float],
    metals: tuple[str, ...],
    prices: dict[str, float],
    mw: dict[str, float],
    threshold: float,
) -> tuple[dict, dict]:
    """Put each metal on one curve. The split is the one with the highest revenue."""
    metals = tuple(metals)
    cache_a: dict[tuple[str, ...], dict] = {}
    cache_b: dict[tuple[str, ...], dict] = {}

    def one(cache, pH, solid, chosen: tuple[str, ...]) -> dict:
        if chosen not in cache:
            cache[chosen] = _best_window(pH, solid, feed, chosen, prices, mw, threshold)
        return cache[chosen]

    best_key = None
    best = None
    for mask in range(1 << len(metals)):
        left = tuple(metal for i, metal in enumerate(metals) if mask >> i & 1)
        right = tuple(metal for i, metal in enumerate(metals) if not mask >> i & 1)
        low = one(cache_a, pH_a, solid_a, left)
        high = one(cache_b, pH_b, solid_b, right)
        revenue_a, quality_a = _window_value(low)
        revenue_b, quality_b = _window_value(high)
        key = (revenue_a + revenue_b, quality_a + quality_b)
        if best_key is None or key > best_key:
            best_key = key
            best = (low, high)
    low, high = best
    low = dict(low)
    high = dict(high)
    low["window"] = "reagent 1"
    high["window"] = "reagent 2"
    return low, high


def _solid_curve(frame, feed: dict[str, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    diss = dissolved_moles(frame)
    bm = column(frame, "bm_cum_frac", 1.0)
    n = len(frame)
    solid = np.zeros((n, len(TRACKED)))
    aqueous = np.zeros((n, len(TRACKED)))
    for metal in TRACKED:
        j = INDEX[metal]
        aqueous[:, j] = diss[metal]
        solid[:, j] = np.maximum(0.0, bm * float(feed.get(metal, 0.0)) - diss[metal])
    return frame["pH"].to_numpy(float), solid, aqueous


def _filtrate_curve(frame, entering: dict[str, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    diss = dissolved_moles(frame)
    n = len(frame)
    solid = np.zeros((n, len(TRACKED)))
    aqueous = np.zeros((n, len(TRACKED)))
    for metal in TRACKED:
        j = INDEX[metal]
        aqueous[:, j] = diss[metal]
        solid[:, j] = np.maximum(0.0, float(entering.get(metal, 0.0)) - diss[metal])
    return frame["pH"].to_numpy(float), solid, aqueous


def _row_moles(frame, index: int) -> dict[str, float]:
    diss = dissolved_moles(frame)
    return {metal: float(diss[metal][index]) for metal in TRACKED}


def score_run(root: Path, cfg) -> dict:
    root = Path(root)
    phree = root / "04_run_phreeqc"
    feed = json.loads((phree / "feed_mol.json").read_text(encoding="utf-8"))["feed_mol"]
    feed = {metal: float(feed.get(metal, 0.0)) for metal in TRACKED}
    stage1_path = phree / f"stage_1_{cfg.REAGENT_1}.csv"
    if not stage1_path.is_file():
        raise SystemExit(f"Missing {stage1_path}.")
    stage1 = read_table(stage1_path)
    stage_a_path = phree / "stage_A_feed.csv"
    stage_a = read_table(stage_a_path) if stage_a_path.is_file() else None
    split = None
    split_path = phree / "split.json"
    if split_path.is_file() and int(cfg.STAGE) == 2:
        split = json.loads(split_path.read_text(encoding="utf-8"))

    pH1, solid1, aq1 = _solid_curve(stage1, feed)
    prices = cfg.METAL_PRICE_USD_PER_KG
    mw = cfg.MW_METAL_KG
    threshold = float(cfg.PURITY_THRESHOLD)

    if int(cfg.STAGE) == 2 and split is not None:
        reagent = column(stage1, "reagent_cum_mol", 0.0)
        keep = reagent <= float(split["reagent_mol"]) + 1e-5
        if not keep.any():
            keep = np.zeros(len(stage1), dtype=bool)
            keep[0] = True
        pH_low, solid_low = pH1[keep], solid1[keep]
        filtrate_path = phree / "stage_B_filtrate.csv"
        filtrate = read_table(filtrate_path)
        entering = _row_moles(filtrate, -1)
        # Replace the low-window end with the filtrate cake so the filter matches stage 2.
        end_solid = np.zeros(len(TRACKED))
        bm = float(split["bm_frac"])
        for metal in TRACKED:
            end_solid[INDEX[metal]] = max(0.0, bm * feed[metal] - entering[metal])
        if len(pH_low):
            solid_low = np.vstack([solid_low, end_solid])
            pH_low = np.concatenate([pH_low, [float(filtrate["pH"].iloc[-1])]])
        else:
            solid_low = end_solid.reshape(1, -1)
            pH_low = np.array([float(filtrate["pH"].iloc[-1])])
        stage2_path = phree / f"stage_2_{cfg.REAGENT_2}.csv"
        if not stage2_path.is_file():
            raise SystemExit(f"Missing {stage2_path}.")
        stage2 = read_table(stage2_path)
        pH_high, solid_high, aq_high = _filtrate_curve(stage2, entering)
        final_aqueous = {metal: float(aq_high[-1, INDEX[metal]]) for metal in TRACKED}
        final_frame = stage2
        used_reagent_1 = float(split["reagent_mol"])
        low, high = _best_two_windows(
            pH_low, solid_low, pH_high, solid_high, feed, _solid_metals(), prices, mw, threshold
        )
    else:
        pH_low, solid_low = pH1, solid1
        final_aqueous = {metal: float(aq1[-1, INDEX[metal]]) for metal in TRACKED}
        final_frame = stage1
        used_reagent_1 = float(column(stage1, "reagent_cum_mol", float(cfg.AMOUNT_1))[-1])
        stage2 = None
        pH_high = np.array([])
        solid_high = np.zeros((0, len(TRACKED)))
        low = _best_window(pH_low, solid_low, feed, _solid_metals(), prices, mw, threshold)
        low["window"] = "reagent 1"
        high = {"status": "EMPTY", "assignment": "", "cuts": [], "metals": {}, "window": "reagent 2"}

    li_options = []
    li_options.append(_li_option_from_window("low", low, pH_low, solid_low, feed, prices, mw, threshold))
    li_options.append(_li_option_from_window("high", high, pH_high, solid_high, feed, prices, mw, threshold))
    brine_total = sum(final_aqueous.values())
    brine_li = float(final_aqueous.get("Li", 0.0))
    brine_purity = 0.0 if brine_total <= 1e-18 else brine_li / brine_total
    brine_recovery = 0.0 if feed["Li"] <= 1e-18 else min(1.0, brine_li / feed["Li"])
    brine_usable = brine_purity >= threshold and brine_li > 0
    brine = {
        "source": "final_brine",
        "purity": brine_purity,
        "recovery": brine_recovery,
        "recovered_mol": brine_li if brine_usable else 0.0,
        "revenue_usd": brine_li * mw["Li"] * prices["Li"] if brine_usable else 0.0,
        "usable": brine_usable,
    }
    li_options.append(brine)
    lithium = dict(max(li_options, key=lambda item: (item["revenue_usd"], item["purity"])))
    lithium["options"] = li_options

    metals = {metal: _empty_metal() for metal in TRACKED}
    for window in (low, high):
        for name, row in window["metals"].items():
            metals[name] = row
    metals["Li"] = {
        "cut": None,
        "cut_pH": None,
        "purity": lithium["purity"],
        "recovery": lithium["recovery"],
        "recovered_mol": lithium["recovered_mol"],
        "revenue_usd": lithium["revenue_usd"],
        "usable": lithium["usable"],
        "source": lithium["source"],
    }
    revenue = sum(row["revenue_usd"] for row in metals.values())
    weighted = 0.0
    for name, row in metals.items():
        if row["usable"]:
            weighted += row["purity"] * row["recovery"] * feed[name] * mw[name] * prices[name]

    costs = _costs(cfg, stage_a, final_frame, stage1, split, used_reagent_1)
    net = revenue - costs["total_cost_usd"]
    return {
        "basis": "phreeqc_selected_output_totals_are_moles",
        "feed_mol": feed,
        "purity_threshold": threshold,
        "windows": {"low": low, "high": high},
        "lithium": lithium,
        "metals": metals,
        "revenue_usd": revenue,
        "costs": costs,
        "net_profit_usd": net,
        "price_weighted_score": weighted,
        "stage1_csv": stage1_path.name,
        "stage2_csv": None if stage2 is None else stage2_path.name,
    }


def _li_option_from_window(label, window, pH, solid, feed, prices, mw, threshold) -> dict:
    """Best single chosen cut in this window, scored as a lithium solid."""
    best = {"source": f"{label}_solid", "purity": 0.0, "recovery": 0.0, "recovered_mol": 0.0, "revenue_usd": 0.0, "usable": False}
    if solid.size == 0 or not window.get("cuts"):
        return best
    # Rebuild increments at the chosen cut pH values in order.
    indices = []
    for cut in window["cuts"]:
        target = float(cut["pH"])
        hit = np.flatnonzero(np.isfinite(pH) & (pH >= target - 1e-6))
        indices.append(int(hit[0]) if len(hit) else len(pH) - 1)
    prev = np.zeros(len(TRACKED))
    for cut_i, row_i in enumerate(indices):
        now = solid[row_i]
        inc = np.maximum(0.0, now - prev)
        inc[inc < 1e-8] = 0.0
        prev = now
        total = float(inc.sum())
        li = float(inc[INDEX["Li"]])
        purity = 0.0 if total <= 1e-18 else li / total
        recovery = 0.0 if feed["Li"] <= 1e-18 else min(1.0, li / feed["Li"])
        usable = purity >= threshold and li > 0
        revenue = li * mw["Li"] * prices["Li"] if usable else 0.0
        if revenue > best["revenue_usd"]:
            best = {
                "source": f"{label}_cut_{cut_i + 1}",
                "purity": purity,
                "recovery": recovery,
                "recovered_mol": li if usable else 0.0,
                "revenue_usd": revenue,
                "usable": usable,
            }
    return best


def _costs(cfg, stage_a, final_frame, stage1, split, used_reagent_1: float) -> dict:
    def price(name: str) -> float:
        return float(cfg.REAGENT_PRICE_USD_PER_MOL.get(name, cfg.REAGENT_PRICE_DEFAULT))

    used2 = float(cfg.AMOUNT_2) if int(cfg.STAGE) == 2 else 0.0
    reagent_cost = used_reagent_1 * price(cfg.REAGENT_1) + used2 * price(cfg.REAGENT_2)
    final = final_frame.iloc[-1]
    mass = float(final["mass_H2O"]) if "mass_H2O" in final_frame.columns and np.isfinite(final.get("mass_H2O", np.nan)) else float(cfg.WATER_KG)
    ph_final = float(final["pH"])
    if stage_a is not None and len(stage_a) and "pH" in stage_a.columns:
        ph_init = float(stage_a["pH"].iloc[-1])
    else:
        ph_init = float(stage1["pH"].iloc[0])
    if "Alk" in final_frame.columns and np.isfinite(final.get("Alk", np.nan)) and ph_final > 7.0:
        acid_eq = max(0.0, float(final["Alk"])) * mass
    else:
        acid_eq = max(0.0, 10 ** (ph_final - 14.0) - 10 ** (-ph_final)) * mass if ph_final > 7.0 else 0.0
    neutralize = acid_eq * float(cfg.NEUTRALIZE_ACID_USD_PER_MOL_H)
    heat = float(cfg.WATER_KG) * float(cfg.CP_WATER_KJ_KG_K) * max(0.0, float(cfg.TEMP_C) - 25.0) * float(cfg.ENERGY_USD_PER_KJ)
    ph_energy = float(cfg.PH_ENERGY_USD_PER_PH_KG) * float(cfg.WATER_KG) * max(0.0, ph_final - ph_init)
    if int(cfg.STAGE) == 2 and split is not None:
        reagent_steps = int(split.get("index", 0)) + 1
        bm_series = column(stage1, "bm_cum_frac", 1.0)[:reagent_steps]
    else:
        reagent_steps = 1 if str(cfg.ADD_1) == "once" and str(cfg.BM_ADD) != "continuous" else int(cfg.STEPS_1)
        if str(cfg.BM_ADD) == "staged":
            reagent_steps = max(int(cfg.BM_BATCHES), int(cfg.STEPS_1)) if str(cfg.ADD_1) != "once" else int(cfg.BM_BATCHES)
        bm_series = column(stage1, "bm_cum_frac", 1.0)
    if str(cfg.BM_ADD) == "batch":
        n_bm = 1
    elif str(cfg.BM_ADD) == "staged":
        n_bm = max(1, len(np.unique(np.round(bm_series, 5))))
    else:
        n_bm = max(1, len(stage1) if int(cfg.STAGE) == 1 else reagent_steps)
    n_r1 = max(1, reagent_steps if int(cfg.STAGE) == 2 else (1 if str(cfg.ADD_1) == "once" else int(cfg.STEPS_1)))
    n_r2 = 0 if int(cfg.STAGE) == 1 else (1 if str(cfg.ADD_2) == "once" else int(cfg.STEPS_2))
    step_cost = float(cfg.STEP_PENALTY_USD) * (n_bm + n_r1 + n_r2)
    filt = float(cfg.STAGE2_FILTRATION_FIXED_COST_USD) if int(cfg.STAGE) == 2 else 0.0
    total = reagent_cost + neutralize + heat + ph_energy + step_cost + filt
    return {
        "reagent_cost_usd": reagent_cost,
        "reagent_1_mol": used_reagent_1,
        "reagent_2_mol": used2,
        "neutralize_cost_usd": neutralize,
        "neutralize_eq": acid_eq,
        "heat_cost_usd": heat,
        "ph_energy_cost_usd": ph_energy,
        "ph_init": ph_init,
        "ph_final": ph_final,
        "step_penalty_usd": step_cost,
        "n_bm_steps": n_bm,
        "n_reagent_1_steps": n_r1,
        "n_reagent_2_steps": n_r2,
        "filtration_cost_usd": filt,
        "total_cost_usd": total,
    }
