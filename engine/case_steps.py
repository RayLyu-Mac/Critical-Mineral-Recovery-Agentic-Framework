# -*- coding: utf-8 -*-
"""One ligand-free PHREEQC case, split into single steps.

Each function reads ``<root>/config.py`` and writes under ``root``.
``run_all`` runs them in order. The no-LangChain crew calls the same
functions from separate ``.py`` files.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from curve_io import read_table  # noqa: E402

FOLDERS = ("01_feed_panel", "02_ask_thermo", "03_make_phreeqc_deck", "04_run_phreeqc", "05_S01")


def layout(root: Path) -> dict[str, Path]:
    root = Path(root)
    folders = {name: root / name for name in FOLDERS}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return folders


def load_config(root: Path):
    path = Path(root) / "config.py"
    if not path.is_file():
        raise SystemExit(f"Missing {path}. Run 00_load_config.py first.")
    spec = importlib.util.spec_from_file_location("econ_case_config", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load config: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_LOADED: dict[str, object] = {}


def _module(name: str):
    cached = _LOADED.get(name)
    if cached is not None:
        return cached
    folder = {
        "feed_panel": ENGINE / "01_feed_panel" / "feed_panel.py",
        "deck": ENGINE / "03_make_phreeqc_deck" / "make_phreeqc_deck.py",
        "thermo": ENGINE / "02_ask_thermo" / "ask_thermo.py",
        "phreeqc": ENGINE / "04_run_phreeqc" / "run_phreeqc_deck.py",
        "analysis": ENGINE / "05_S01" / "S01_econ_analysis.py",
    }[name]
    spec = importlib.util.spec_from_file_location(f"econ_{name}", folder)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load {folder}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LOADED[name] = mod
    return mod


def _feed_csv(root: Path, anion: str) -> Path:
    return layout(root)["01_feed_panel"] / f"blackmass_compositions_{anion}.csv"


def _context(root: Path):
    """Config, feed row, and reagent phase plans for the later steps."""
    active = load_config(root)
    folders = layout(root)
    feed = _module("feed_panel")
    deck = _module("deck")
    thermo = _module("thermo")
    csv_path = _feed_csv(root, active.ANION)
    if not csv_path.is_file():
        frame = feed.build_panel(int(active.PANEL_N), active.ANION)
        feed.write_compositions(frame, csv_path)
    row, anion, salts, feed_mol = deck.load_feed_row(csv_path, None)
    comp_id = int(row["comp_id"])
    metals = [metal for metal, _formula, _mol in salts]
    deck.write_feed_mol(folders["04_run_phreeqc"] / "feed_mol.json", feed_mol, comp_id, anion)
    doc1 = thermo.collect(anion, active.REAGENT_1, metals)
    (folders["02_ask_thermo"] / "thermo_1.json").write_text(json.dumps(doc1, indent=2) + "\n", encoding="utf-8")
    phases1, lines1 = deck.phase_plan(doc1, metals)
    doc2 = None
    phases2, lines2 = [], []
    if int(active.STAGE) == 2:
        doc2 = thermo.collect(anion, active.REAGENT_2, metals)
        (folders["02_ask_thermo"] / "thermo_2.json").write_text(json.dumps(doc2, indent=2) + "\n", encoding="utf-8")
        phases2, lines2 = deck.phase_plan(doc2, metals)
    _all_names, all_lines = deck.union_phases([(phases1, lines1), (phases2, lines2)])
    reactions = [doc1["reagent"]["reaction"]]
    if doc2 is not None:
        reactions.append(doc2["reagent"]["reaction"])
    totals = deck._totals(metals, reactions, anion)
    return {
        "cfg": active,
        "folders": folders,
        "deck": deck,
        "salts": salts,
        "doc1": doc1,
        "doc2": doc2,
        "phases1": phases1,
        "phases2": phases2,
        "lines1": lines1,
        "all_lines": all_lines,
        "totals": totals,
    }


def _drop_stage2_decks(folder: Path) -> None:
    """A one-stage case keeps stage1_<reagent>.phr only."""
    if not folder.is_dir():
        return
    for path in folder.glob("stage2_*.phr"):
        try:
            path.unlink()
        except OSError:
            continue


def _delete_tree(folder: Path) -> None:
    """Remove a case output folder. OneDrive can deny the directory delete."""
    if not folder.is_dir():
        return
    try:
        shutil.rmtree(folder)
        return
    except OSError:
        pass
    for path in sorted(folder.rglob("*"), reverse=True):
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        except OSError:
            continue


def clear_outputs(root: Path) -> None:
    """Remove decks, PHREEQC output, and the score from a reused case folder.

    02_ask_thermo stays. It has no ligand. It stores the salt and reagent
    equilibrium data, and the next case overwrites thermo_1.json. OneDrive
    often denies deletion of that folder.
    """
    root = Path(root)
    for name in ("03_make_phreeqc_deck", "04_run_phreeqc", "05_S01"):
        _delete_tree(root / name)


def step_feed(root: Path) -> Path:
    clear_outputs(root)
    active = load_config(root)
    feed = _module("feed_panel")
    deck = _module("deck")
    folders = layout(root)
    frame = feed.build_panel(int(active.PANEL_N), active.ANION)
    csv_path = _feed_csv(root, active.ANION)
    feed.write_compositions(frame, csv_path)
    row, anion, salts, feed_mol = deck.load_feed_row(csv_path, None)
    comp_id = int(row["comp_id"])
    deck.write_feed_mol(folders["04_run_phreeqc"] / "feed_mol.json", feed_mol, comp_id, anion)
    print(f"FEED_CSV={csv_path}")
    return csv_path


def step_thermo(root: Path, ctx: dict | None = None) -> Path:
    ctx = _context(root) if ctx is None else ctx
    path = ctx["folders"]["02_ask_thermo"] / "thermo_1.json"
    print(f"THERMO={path}")
    return path


def step_stage1_deck(root: Path, ctx: dict | None = None) -> Path:
    ctx = _context(root) if ctx is None else ctx
    active = ctx["cfg"]
    folders = ctx["folders"]
    stage_a = folders["04_run_phreeqc"] / "stage_A_feed.csv"
    stage_1 = folders["04_run_phreeqc"] / f"stage_1_{active.REAGENT_1}.csv"
    deck_dir = folders["03_make_phreeqc_deck"]
    if int(active.STAGE) != 2:
        _drop_stage2_decks(deck_dir)
    phr1 = deck_dir / f"stage1_{active.REAGENT_1}.phr"
    phr1.write_text(
        ctx["deck"].build_stage1(
            temp_c=float(active.TEMP_C),
            water_kg=float(active.WATER_KG),
            bm_add=str(active.BM_ADD),
            bm_batches=int(active.BM_BATCHES),
            add=str(active.ADD_1),
            steps=int(active.STEPS_1),
            amount=float(active.AMOUNT_1),
            reagent=str(active.REAGENT_1),
            reaction=ctx["doc1"]["reagent"]["reaction"],
            salts=ctx["salts"],
            phases=ctx["phases1"],
            phase_lines=ctx["lines1"],
            totals=ctx["totals"],
            stage_a_csv=stage_a,
            stage1_csv=stage_1,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"STAGE1_DECK={phr1}")
    return phr1


def step_stage1_phreeqc(root: Path, ctx: dict | None = None) -> Path:
    ctx = _context(root) if ctx is None else ctx
    active = ctx["cfg"]
    folders = ctx["folders"]
    phr1 = folders["03_make_phreeqc_deck"] / f"stage1_{active.REAGENT_1}.phr"
    if not phr1.is_file():
        phr1 = step_stage1_deck(root, ctx)
    stage_1 = folders["04_run_phreeqc"] / f"stage_1_{active.REAGENT_1}.csv"
    print(f"Stage 1 deck: {phr1}")
    _module("phreeqc").run_deck(phr1, folders["04_run_phreeqc"])
    if not stage_1.is_file():
        raise SystemExit(f"PHREEQC did not write {stage_1.name}.")
    print(f"STAGE1_CSV={stage_1}")
    return stage_1


def step_stage2(root: Path, ctx: dict | None = None) -> Path | None:
    ctx = _context(root) if ctx is None else ctx
    active = ctx["cfg"]
    folders = ctx["folders"]
    if int(active.STAGE) != 2:
        _drop_stage2_decks(folders["03_make_phreeqc_deck"])
        print("STAGE=1. Stage 2 skipped.")
        return None
    if ctx["doc2"] is None:
        raise SystemExit("STAGE is 2 but reagent 2 has no thermo data.")
    stage_1 = folders["04_run_phreeqc"] / f"stage_1_{active.REAGENT_1}.csv"
    if not stage_1.is_file():
        raise SystemExit(f"Missing {stage_1}. Run stage 1 PHREEQC first.")
    table = read_table(stage_1)
    if table.empty:
        raise SystemExit(f"{stage_1.name} has no rows.")
    row = table.iloc[-1]
    dose = float(row["reagent_cum_mol"]) if "reagent_cum_mol" in table.columns else float(active.AMOUNT_1)
    if dose <= 0:
        dose = min(1e-4, float(active.AMOUNT_1))
    bm = float(row["bm_cum_frac"]) if "bm_cum_frac" in table.columns else 1.0
    split = {
        "crossed": True,
        "ph": float(row["pH"]) if "pH" in table.columns else None,
        "reagent_mol": dose,
        "bm_frac": bm,
        "index": int(len(table) - 1),
        "t": 1.0,
        "basis": "full_amount_1",
    }
    (folders["04_run_phreeqc"] / "split.json").write_text(json.dumps(split, indent=2) + "\n", encoding="utf-8")
    filtrate = folders["04_run_phreeqc"] / "stage_B_filtrate.csv"
    stage_2 = folders["04_run_phreeqc"] / f"stage_2_{active.REAGENT_2}.csv"
    phr2 = folders["03_make_phreeqc_deck"] / f"stage2_{active.REAGENT_2}.phr"
    phr2.write_text(
        ctx["deck"].build_stage2(
            temp_c=float(active.TEMP_C),
            water_kg=float(active.WATER_KG),
            salts=ctx["salts"],
            bm_frac=float(split["bm_frac"]),
            reagent1=str(active.REAGENT_1),
            reagent1_mol=float(split["reagent_mol"]),
            reaction1=ctx["doc1"]["reagent"]["reaction"],
            phases1=ctx["phases1"],
            reagent2=str(active.REAGENT_2),
            amount2=float(active.AMOUNT_2),
            add2=str(active.ADD_2),
            steps2=int(active.STEPS_2),
            reaction2=ctx["doc2"]["reagent"]["reaction"],
            phases2=ctx["phases2"],
            phase_lines=ctx["all_lines"],
            totals=ctx["totals"],
            filtrate_csv=filtrate,
            stage2_csv=stage_2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"Stage 2 deck: {phr2}  reagent 1 {split['reagent_mol']:.4g} mol then reagent 2 {float(active.AMOUNT_2):.4g} mol")
    _module("phreeqc").run_deck(phr2, folders["04_run_phreeqc"])
    if not stage_2.is_file():
        raise SystemExit(f"PHREEQC did not write {stage_2.name}.")
    print(f"STAGE2_CSV={stage_2}")
    return stage_2


def step_score(root: Path) -> Path:
    root = Path(root)
    load_config(root)
    result = _module("analysis").analyze(root)
    print(f"RUN_ROOT={root}")
    print(f"ECON_JSON={result}")
    return result


def run_all(root: Path) -> Path:
    """Feed, thermo, both decks, PHREEQC, and the economic score."""
    step_feed(root)
    ctx = _context(root)
    step_thermo(root, ctx)
    step_stage1_deck(root, ctx)
    step_stage1_phreeqc(root, ctx)
    step_stage2(root, ctx)
    step_score(root)
    print(f"RUN_ROOT={root}")
    return Path(root)


def seed_config(root: Path, source: Path) -> Path:
    """Copy a config into the case folder only when that folder has none."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    dest = root / "config.py"
    if dest.is_file():
        print(f"CONFIG={dest}")
        print("Kept the existing case config.")
        return dest
    shutil.copyfile(source, dest)
    print(f"CONFIG={dest}")
    print(f"Seeded from {source}")
    return dest
