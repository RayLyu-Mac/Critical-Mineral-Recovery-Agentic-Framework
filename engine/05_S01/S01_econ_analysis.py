# -*- coding: utf-8 -*-
"""Plot one economic run and write 05_S01/econ_result.json."""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(HERE))

from curve_io import column, dissolved_moles, read_table  # noqa: E402
from s01_econ_cuts import score_run  # noqa: E402

IMPURITIES = ("Al", "Fe", "Cu")
VALUES = ("Ni", "Co", "Mn")


def _load_cfg(path: Path):
    spec = importlib.util.spec_from_file_location("econ_result_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_COLORS = {
    "Al": "#1f77b4",
    "Fe": "#ff7f0e",
    "Cu": "#2ca02c",
    "Ni": "#1f77b4",
    "Co": "#ff7f0e",
    "Mn": "#2ca02c",
}


def _series(frame, feed: dict[str, float], entering: dict[str, float] | None):
    """Dose, pH, ionic strength, and solid fraction of feed for one stage."""
    dose = column(frame, "reagent_cum_mol", 0.0)
    ph = frame["pH"].to_numpy(float)
    mu = frame["mu"].to_numpy(float) if "mu" in frame.columns else None
    bm = column(frame, "bm_cum_frac", 1.0)
    fracs = {}
    for metal in (*IMPURITIES, *VALUES):
        if metal not in frame.columns:
            continue
        aqueous = frame[metal].to_numpy(float)
        if entering is None:
            added = bm * float(feed.get(metal, 0.0))
            solid = np.maximum(0.0, added - aqueous)
        else:
            solid = np.maximum(0.0, float(entering.get(metal, 0.0)) - aqueous)
        base = float(feed.get(metal, 0.0))
        fracs[metal] = solid / base if base > 1e-18 else np.zeros(len(frame))
    return dose, ph, mu, fracs


def _cut_positions(result: dict) -> dict[str, tuple[str, float]]:
    """Metal name to (low or high window, cut pH)."""
    found = {}
    windows = result.get("windows") or {}
    for side in ("low", "high"):
        metals = (windows.get(side) or {}).get("metals") or {}
        for name, row in metals.items():
            if isinstance(row, dict) and row.get("pH") is not None:
                found[name] = (side, float(row["pH"]))
    for name, row in (result.get("metals") or {}).items():
        if name in found or not isinstance(row, dict):
            continue
        ph = row.get("pH")
        if ph is None:
            ph = row.get("cut_pH")
        if ph is not None:
            found[name] = ("low", float(ph))
    return found


def _x_at_ph(dose: np.ndarray, ph: np.ndarray, cut: float, start: int, stop: int) -> float | None:
    if stop <= start:
        return None
    segment_ph = ph[start:stop]
    segment_x = dose[start:stop]
    ok = np.isfinite(segment_ph) & np.isfinite(segment_x)
    if not ok.any():
        return None
    index = np.flatnonzero(ok)[int(np.argmin(np.abs(segment_ph[ok] - cut)))]
    return float(segment_x[index])


def _curve_table(stage1, feed: dict[str, float], stage2=None, entering: dict[str, float] | None = None) -> dict:
    """Stage, cumulative reagent dose, pH, ionic strength, and solid fraction of feed."""
    dose1, ph1, mu1, frac1 = _series(stage1, feed, None)
    n1 = len(dose1)
    dose = dose1
    ph = ph1
    mu = mu1
    fracs = {name: values.copy() for name, values in frac1.items()}
    split_x = None
    stage = np.ones(n1, dtype=int)
    if stage2 is not None and not stage2.empty:
        dose2, ph2, mu2, frac2 = _series(stage2, feed, entering)
        split_x = float(dose1[-1]) if len(dose1) else 0.0
        dose = np.concatenate([dose1, split_x + dose2])
        ph = np.concatenate([ph1, ph2])
        stage = np.concatenate([stage, np.full(len(dose2), 2, dtype=int)])
        if mu1 is not None and mu2 is not None:
            mu = np.concatenate([mu1, mu2])
        else:
            mu = None
        for name, values in frac2.items():
            if name in fracs:
                fracs[name] = np.concatenate([fracs[name], values])
    return {
        "stage": stage,
        "dose_mol": dose,
        "pH": ph,
        "mu": mu,
        "fracs": fracs,
        "n1": n1,
        "split_x": split_x,
    }


def _mod(key: str, path: Path):
    spec = importlib.util.spec_from_file_location(key, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stamp(cfg) -> dict:
    return {
        "temp_c": float(cfg.TEMP_C),
        "water_kg": float(cfg.WATER_KG),
        "anion": str(cfg.ANION),
        "bm_add": str(cfg.BM_ADD),
        "bm_batches": int(cfg.BM_BATCHES),
        "stage": int(cfg.STAGE),
        "reagent_1": str(cfg.REAGENT_1),
        "amount_1": float(cfg.AMOUNT_1),
        "add_1": str(cfg.ADD_1),
        "steps_1": int(cfg.STEPS_1),
        "reagent_2": str(cfg.REAGENT_2),
        "amount_2": float(cfg.AMOUNT_2),
        "add_2": str(cfg.ADD_2),
        "steps_2": int(cfg.STEPS_2),
    }


def _stamp_ok(path: Path, stamp: dict) -> bool:
    if not path.is_file():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return saved == stamp


def _on_grid(x_src: np.ndarray, y_src: np.ndarray, x_grid: np.ndarray) -> np.ndarray:
    mask = np.isfinite(x_src) & np.isfinite(y_src)
    if int(mask.sum()) < 2:
        return np.full(len(x_grid), np.nan)
    order = np.argsort(x_src[mask], kind="mergesort")
    x = x_src[mask][order]
    y = y_src[mask][order]
    keep = np.concatenate([[True], np.diff(x) > 1e-9])
    return np.interp(x_grid, x[keep], y[keep])


def _mean_spread(tables: list[dict], grid: dict) -> tuple[dict, dict]:
    """Average solid fraction, pH, and ionic strength onto the scored dose grid."""
    dose = grid["dose_mol"]
    metals = [name for name in (*IMPURITIES, *VALUES) if any(name in item["fracs"] for item in tables)]

    def stats(rows: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        arr = np.vstack(rows)
        mean = np.nanmean(arr, axis=0)
        sd = np.zeros_like(mean) if arr.shape[0] < 2 else np.nanstd(arr, axis=0, ddof=1)
        return mean, np.where(np.isfinite(sd), sd, 0.0)

    ph_mean, ph_sd = stats([_on_grid(item["dose_mol"], item["pH"], dose) for item in tables])
    mu_rows = [_on_grid(item["dose_mol"], item["mu"], dose) for item in tables if item["mu"] is not None]
    mu_mean, mu_sd = stats(mu_rows) if mu_rows else (None, None)
    fracs = {}
    frac_sd = {}
    for name in metals:
        rows = [
            _on_grid(item["dose_mol"], item["fracs"][name], dose)
            for item in tables
            if name in item["fracs"]
        ]
        fracs[name], frac_sd[name] = stats(rows)
    shown = {
        "stage": grid["stage"],
        "dose_mol": dose,
        "pH": ph_mean,
        "mu": mu_mean,
        "fracs": fracs,
        "n1": grid["n1"],
        "split_x": grid["split_x"],
    }
    return shown, {"n": len(tables), "pH": ph_sd, "mu": mu_sd, "fracs": frac_sd}


def _execute_comp(job: dict, dest: Path, stage1_csv: Path, stage2_csv: Path, filtrate_csv: Path) -> None:
    deck = job["deck"]
    run_deck = job["run_deck"]
    stage_a_csv = dest / "stage_A_feed.csv"
    for old in (stage_a_csv, stage1_csv, stage2_csv, filtrate_csv):
        if old.is_file():
            old.unlink()
    phr1 = dest / f"stage1_{job['reagent1']}.phr"
    phr1.write_text(
        deck.build_stage1(
            temp_c=job["stamp"]["temp_c"],
            water_kg=job["stamp"]["water_kg"],
            bm_add=job["stamp"]["bm_add"],
            bm_batches=job["stamp"]["bm_batches"],
            add=job["stamp"]["add_1"],
            steps=job["stamp"]["steps_1"],
            amount=job["stamp"]["amount_1"],
            reagent=job["reagent1"],
            reaction=job["reaction1"],
            salts=job["salts"],
            phases=job["phases1"],
            phase_lines=job["lines1"],
            totals=job["totals"],
            stage_a_csv=stage_a_csv,
            stage1_csv=stage1_csv,
        ),
        encoding="utf-8",
        newline="\n",
    )
    run_deck(phr1, dest)
    if job["stage"] != 2:
        return
    stage1 = read_table(stage1_csv)
    row = stage1.iloc[-1]
    dose = float(row["reagent_cum_mol"]) if "reagent_cum_mol" in stage1.columns else job["stamp"]["amount_1"]
    bm = float(row["bm_cum_frac"]) if "bm_cum_frac" in stage1.columns else 1.0
    phr2 = dest / f"stage2_{job['reagent2']}.phr"
    phr2.write_text(
        deck.build_stage2(
            temp_c=job["stamp"]["temp_c"],
            water_kg=job["stamp"]["water_kg"],
            salts=job["salts"],
            bm_frac=bm,
            reagent1=job["reagent1"],
            reagent1_mol=dose,
            reaction1=job["reaction1"],
            phases1=job["phases1"],
            reagent2=job["reagent2"],
            amount2=job["stamp"]["amount_2"],
            add2=job["stamp"]["add_2"],
            steps2=job["stamp"]["steps_2"],
            reaction2=job["reaction2"],
            phases2=job["phases2"],
            phase_lines=job["all_lines"],
            totals=job["totals"],
            filtrate_csv=filtrate_csv,
            stage2_csv=stage2_csv,
        ),
        encoding="utf-8",
        newline="\n",
    )
    run_deck(phr2, dest)


def _run_comp(job: dict) -> dict:
    dest = Path(job["dest"])
    try:
        dest.mkdir(parents=True, exist_ok=True)
        stage1_csv = dest / f"stage_1_{job['reagent1']}.csv"
        stage2_csv = dest / f"stage_2_{job['reagent2']}.csv"
        filtrate_csv = dest / "stage_B_filtrate.csv"
        stamp_path = dest / "stamp.json"
        ready = _stamp_ok(stamp_path, job["stamp"]) and stage1_csv.is_file()
        if job["stage"] == 2:
            ready = ready and stage2_csv.is_file() and filtrate_csv.is_file()
        if not ready:
            _execute_comp(job, dest, stage1_csv, stage2_csv, filtrate_csv)
            stamp_path.write_text(json.dumps(job["stamp"]) + "\n", encoding="utf-8")
        stage1 = read_table(stage1_csv)
        if stage1.empty:
            return {"error": f"comp {job['comp_id']}: empty stage 1", "comp_id": job["comp_id"]}
        stage2 = None
        entering = None
        if job["stage"] == 2:
            filtrate = read_table(filtrate_csv)
            entering = {
                metal: float(dissolved_moles(filtrate)[metal][-1])
                for metal in ("Ni", "Co", "Mn", "Al", "Fe", "Cu", "Li")
            }
            stage2 = read_table(stage2_csv)
        table = _curve_table(stage1, job["feed"], stage2, entering)
        table["comp_id"] = job["comp_id"]
        return table
    except (SystemExit, Exception) as exc:
        return {"error": f"comp {job['comp_id']}: {exc}", "comp_id": job["comp_id"]}


def _seed_scored_comp(root: Path, cfg, dest: Path, stamp: dict) -> None:
    """Reuse the scored recipe files so that composition is not run twice."""
    phree = root / "04_run_phreeqc"
    reagent1 = str(cfg.REAGENT_1)
    reagent2 = str(cfg.REAGENT_2)
    stage1 = phree / f"stage_1_{reagent1}.csv"
    if not stage1.is_file():
        return
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(stage1, dest / stage1.name)
    if int(cfg.STAGE) == 2:
        for name in (f"stage_2_{reagent2}.csv", "stage_B_filtrate.csv"):
            source = phree / name
            if source.is_file():
                shutil.copy2(source, dest / name)
    (dest / "stamp.json").write_text(json.dumps(stamp) + "\n", encoding="utf-8")


def _panel_tables(root: Path, cfg) -> list[dict]:
    """One precipitation curve per black-mass row. Failed rows are omitted."""
    csv_path = root / "01_feed_panel" / f"blackmass_compositions_{cfg.ANION}.csv"
    if not csv_path.is_file():
        return []
    deck = _mod("econ_deck", ENGINE / "03_make_phreeqc_deck" / "make_phreeqc_deck.py")
    thermo = _mod("econ_thermo", ENGINE / "02_ask_thermo" / "ask_thermo.py")
    phreeqc = _mod("econ_phreeqc", ENGINE / "04_run_phreeqc" / "run_phreeqc_deck.py")
    phreeqc.find_phreeqc()
    with csv_path.open(encoding="utf-8", newline="") as handle:
        comp_ids = [int(row["comp_id"]) for row in csv.DictReader(handle)]
    if len(comp_ids) < 2:
        return []
    feeds = []
    metals: list[str] = []
    for comp_id in comp_ids:
        _row, _anion, salts, feed = deck.load_feed_row(csv_path, comp_id)
        feeds.append((comp_id, salts, feed))
        for metal, _formula, _mol in salts:
            if metal not in metals:
                metals.append(metal)
    doc1 = thermo.collect(str(cfg.ANION), str(cfg.REAGENT_1), metals)
    phases1, lines1 = deck.phase_plan(doc1, metals)
    doc2 = None
    phases2: list[str] = []
    lines2: list[str] = []
    if int(cfg.STAGE) == 2:
        doc2 = thermo.collect(str(cfg.ANION), str(cfg.REAGENT_2), metals)
        phases2, lines2 = deck.phase_plan(doc2, metals)
    _names, all_lines = deck.union_phases([(phases1, lines1), (phases2, lines2)])
    reactions = [doc1["reagent"]["reaction"]]
    if doc2 is not None:
        reactions.append(doc2["reagent"]["reaction"])
    totals = deck._totals(metals, reactions, str(cfg.ANION))
    stamp = _stamp(cfg)
    scored_id = None
    scored_path = root / "04_run_phreeqc" / "feed_mol.json"
    if scored_path.is_file():
        scored_id = int(json.loads(scored_path.read_text(encoding="utf-8")).get("comp_id", 0))
    jobs = []
    for comp_id, salts, feed in feeds:
        dest = root / "04_run_phreeqc" / "panel" / f"comp_{comp_id}"
        if comp_id == scored_id:
            _seed_scored_comp(root, cfg, dest, stamp)
        jobs.append(
            {
                "deck": deck,
                "run_deck": phreeqc.run_deck,
                "dest": dest,
                "comp_id": comp_id,
                "feed": feed,
                "salts": salts,
                "stamp": stamp,
                "stage": int(cfg.STAGE),
                "reagent1": str(cfg.REAGENT_1),
                "reagent2": str(cfg.REAGENT_2),
                "reaction1": doc1["reagent"]["reaction"],
                "reaction2": None if doc2 is None else doc2["reagent"]["reaction"],
                "phases1": phases1,
                "phases2": phases2,
                "lines1": lines1,
                "all_lines": all_lines,
                "totals": totals,
            }
        )
    tables = []
    errors = []
    workers = min(4, len(jobs))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_run_comp, job): job["comp_id"] for job in jobs}
        for future in as_completed(future_map):
            item = future.result()
            if item.get("error"):
                errors.append(item["error"])
                print(item["error"])
            else:
                tables.append(item)
                print(f"panel comp {item['comp_id']} ready")
    tables.sort(key=lambda item: int(item["comp_id"]))
    if errors:
        print(f"panel feeds used {len(tables)} of {len(jobs)}")
    return tables


def _fmt4(value) -> str:
    if value is None or not np.isfinite(value):
        return ""
    return f"{float(value):.4g}"


def write_curves_csv(table: dict, path: Path, spread: dict | None = None) -> Path:
    """Write the plotted curves. Fractions use 4 significant figures.

    Metal columns are the mean across feeds. ``*_sd`` columns are 1 standard deviation.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metals = [name for name in (*IMPURITIES, *VALUES) if name in table["fracs"]]
    mu = table["mu"]
    headers = ["stage", "dose_mol", "pH", "mu", *metals]
    if spread is not None:
        headers += ["pH_sd", "mu_sd", *[f"{name}_sd" for name in metals]]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for index in range(len(table["dose_mol"])):
            row = [
                str(int(table["stage"][index])),
                _fmt4(table["dose_mol"][index]),
                _fmt4(table["pH"][index]),
                _fmt4(None if mu is None else mu[index]),
                *[_fmt4(table["fracs"][name][index]) for name in metals],
            ]
            if spread is not None:
                mu_sd = None if spread["mu"] is None else spread["mu"][index]
                row += [
                    _fmt4(spread["pH"][index]),
                    _fmt4(mu_sd),
                    *[_fmt4(spread["fracs"][name][index]) for name in metals],
                ]
            writer.writerow(row)
    return path


def _reagent_spans(dose: np.ndarray, split_x: float | None, reagent1: str, reagent2: str) -> list[tuple[float, float, str]]:
    """Each reagent occupies one dose interval, from the first added mole to the last."""
    if len(dose) == 0:
        return []
    end = float(dose[-1])
    if split_x is None:
        return [(0.0, end, reagent1)]
    return [(0.0, float(split_x), reagent1), (float(split_x), end, reagent2 or "reagent 2")]


def _mark_reagents(axes, spans: list[tuple[float, float, str]]) -> None:
    fills = ("#4C78A8", "#F58518")
    for ax in axes:
        for index, (x0, x1, name) in enumerate(spans):
            ax.axvspan(x0, x1, color=fills[index % 2], alpha=0.10, zorder=0)
            if index > 0:
                ax.axvline(x0, color="#444444", linewidth=1, zorder=2)
            mid = 0.5 * (x0 + x1)
            ax.text(
                mid,
                0.03,
                f"{name}\n{x0:.4g}–{x1:.4g} mol",
                transform=ax.get_xaxis_transform(),
                ha="center",
                va="bottom",
                fontsize=8,
                color="#333333",
                zorder=4,
                bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.8},
            )


def _shade(ax, dose: np.ndarray, mean: np.ndarray, sd: np.ndarray | None, color: str) -> None:
    if sd is None:
        return
    ax.fill_between(dose, mean - sd, mean + sd, color=color, alpha=0.22, linewidth=0, zorder=1)


def plot_curves(
    stage1,
    feed: dict[str, float],
    path: Path,
    reagent1: str,
    result: dict,
    stage2=None,
    entering: dict[str, float] | None = None,
    reagent2: str = "",
    table: dict | None = None,
    spread: dict | None = None,
    cut_table: dict | None = None,
) -> None:
    """One figure for both stages. The line is the mean. The shade is ±1 SD."""
    if table is None:
        if stage1 is None or stage1.empty:
            return
        table = _curve_table(stage1, feed, stage2, entering)
    dose = table["dose_mol"]
    ph = table["pH"]
    mu = table["mu"]
    fracs = table["fracs"]
    n1 = table["n1"]
    split_x = table["split_x"]
    cuts_from = cut_table or table
    fig, axes = plt.subplots(3, 1, figsize=(8, 10), sharex=True)
    _shade(axes[0], dose, ph, None if spread is None else spread["pH"], "#1f77b4")
    axes[0].plot(dose, ph, color="#1f77b4", linewidth=1.8, label="pH", zorder=3)
    if mu is not None:
        twin = axes[0].twinx()
        _shade(twin, dose, mu, None if spread is None else spread["mu"], "#ff7f0e")
        twin.plot(dose, mu, color="#ff7f0e", linewidth=1.8, label="ionic strength", zorder=3)
        twin.set_ylabel("Ionic strength (mol/kg)")
    axes[0].set_ylabel("pH")
    title = reagent1 if split_x is None else f"{reagent1} then {reagent2}"
    if spread is not None and spread["n"] >= 2:
        title = f"{title}, mean of {spread['n']} feeds"
    axes[0].set_title(title)
    cuts = _cut_positions(result)
    for ax, metals, label in (
        (axes[1], IMPURITIES, "Al, Fe, Cu solid / feed"),
        (axes[2], VALUES, "Ni, Co, Mn solid / feed"),
    ):
        for metal in metals:
            if metal not in fracs:
                continue
            color = _COLORS[metal]
            sd = None if spread is None else spread["fracs"].get(metal)
            _shade(ax, dose, fracs[metal], sd, color)
            ax.plot(dose, fracs[metal], color=color, linewidth=1.8, label=metal, zorder=3)
            side, cut_ph = cuts.get(metal, (None, None))
            if cut_ph is None:
                continue
            start, stop = (n1, len(dose)) if side == "high" and split_x is not None else (0, n1 if split_x is not None else len(dose))
            x_cut = _x_at_ph(cuts_from["dose_mol"], cuts_from["pH"], cut_ph, start, stop)
            if x_cut is None:
                continue
            ax.axvline(x_cut, color=color, linestyle=":", linewidth=1.1, zorder=2)
            ax.annotate(
                metal,
                xy=(x_cut, 1),
                xycoords=("data", "axes fraction"),
                xytext=(0, -2),
                textcoords="offset points",
                rotation=90,
                va="top",
                ha="right",
                fontsize=8,
                color=color,
            )
        ax.set_ylim(-0.05, 1.05)
        ax.set_ylabel("Fraction of feed in solid")
        if spread is not None and spread["n"] >= 2:
            label = f"{label}, ±1 SD"
        ax.set_title(label)
        handles, labels = ax.get_legend_handles_labels()
        if spread is not None and spread["n"] >= 2:
            handles.append(Patch(facecolor="#9aa0a6", alpha=0.35, label="±1 SD"))
            labels.append("±1 SD")
        ax.legend(handles, labels, frameon=False)
    _mark_reagents(axes, _reagent_spans(dose, split_x, reagent1, reagent2))
    axes[2].set_xlabel("Reagent added (mol)")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def plot_metal_bars(result: dict, path: Path) -> Path:
    """Purity and recovery for each metal, with the purity gate."""
    metals = result.get("metals") or {}
    names = [name for name in ("Al", "Fe", "Cu", "Co", "Ni", "Mn", "Li") if isinstance(metals.get(name), dict)]
    path = Path(path)
    if not names:
        return path
    purity = [float(metals[name].get("purity") or 0.0) for name in names]
    recovery = [float(metals[name].get("recovery") or 0.0) for name in names]
    gate = float(result.get("purity_threshold") or 0.70)
    positions = np.arange(len(names))
    width = 0.36
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar(positions - width / 2, purity, width, label="purity", color="#1f77b4")
    ax.bar(positions + width / 2, recovery, width, label="recovery", color="#2ca02c")
    ax.axhline(gate, color="#666666", linestyle="--", linewidth=1, label=f"purity gate {gate:g}")
    ax.set_xticks(positions, names)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("Fraction")
    ax.set_title("Recovery and purity by element")
    ax.legend(frameon=False, ncol=3)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def analyze(root: Path) -> Path:
    root = Path(root)
    cfg = _load_cfg(root / "config.py")
    result = score_run(root, cfg)
    out = root / "05_S01"
    out.mkdir(parents=True, exist_ok=True)
    dest = out / "econ_result.json"
    dest.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    feed = result["feed_mol"]
    phree = root / "04_run_phreeqc"
    stage1_path = phree / f"stage_1_{cfg.REAGENT_1}.csv"
    if not stage1_path.is_file():
        raise SystemExit(f"Missing {stage1_path}.")
    stage1 = read_table(stage1_path)
    stage2 = None
    entering = None
    if int(cfg.STAGE) == 2:
        stage2_path = phree / f"stage_2_{cfg.REAGENT_2}.csv"
        filtrate_path = phree / "stage_B_filtrate.csv"
        if stage2_path.is_file() and filtrate_path.is_file():
            filtrate = read_table(filtrate_path)
            entering = {metal: float(dissolved_moles(filtrate)[metal][-1]) for metal in ("Ni", "Co", "Mn", "Al", "Fe", "Cu", "Li")}
            stage2 = read_table(stage2_path)
        old_stage2 = out / "image" / "stage2.png"
        if old_stage2.is_file():
            old_stage2.unlink()
    shown = None
    spread = None
    recipe = None
    if stage1 is not None and not stage1.empty:
        recipe = _curve_table(stage1, feed, stage2, entering)
        try:
            panel = _panel_tables(root, cfg)
        except SystemExit as exc:
            print(exc)
            panel = []
        shown = recipe
        if len(panel) >= 2:
            shown, spread = _mean_spread(panel, recipe)
        write_curves_csv(shown, out / "curves.csv", spread)
    plot_curves(
        stage1,
        feed,
        out / "image" / "stage1.png",
        str(cfg.REAGENT_1),
        result,
        stage2=stage2,
        entering=entering,
        reagent2=str(cfg.REAGENT_2),
        table=shown,
        spread=spread,
        cut_table=recipe,
    )
    plot_metal_bars(result, out / "image" / "metals.png")
    print(
        f"net_profit_usd {result['net_profit_usd']:.4f}  revenue {result['revenue_usd']:.4f}  "
        f"cost {result['costs']['total_cost_usd']:.4f}"
    )
    return dest
