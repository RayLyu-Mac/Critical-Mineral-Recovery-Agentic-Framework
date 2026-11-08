# -*- coding: utf-8 -*-
"""PHREEQC path resolution, deck text, and shard runner.

OH-screen deck text (phr_header, write_leachate, write_oh_naoh_titration) is the
black-mass screen writer. Keep it stable: OH_sweep CSV rows are compared to the
pre-restructure baseline.
"""

from __future__ import annotations

import glob
import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

import config as cfg

SEL_OH = 10

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def log(msg: str) -> None:
    print(msg, flush=True)


def resolve_phreeqc_exe() -> str | None:
    """PHREEQC_EXE, else `phreeqc` on PATH. Cluster defaults are set by env.sh."""
    exe = os.environ.get("PHREEQC_EXE", "").strip()
    if exe and Path(exe).is_file():
        return str(Path(exe).resolve())
    return shutil.which("phreeqc") or shutil.which("phreeqc.exe")


def resolve_sit_dat() -> str | None:
    """PHREEQC_DB, else sit.dat under the cluster data dirs. No workstation paths."""
    db = os.environ.get("PHREEQC_DB", "").strip()
    if db and Path(db).is_file():
        return str(Path(db).resolve())
    for candidate in (
        cfg.DATA_DIR / "sit.dat",
        cfg.OH_PRECIP_ROOT / "sit.dat",
        cfg.SCRATCH_ROOT / "data" / "sit.dat",
    ):
        if candidate.is_file():
            return str(candidate.resolve())
    return None


def verify_sit_dat(path: str | None = None) -> bool:
    db = path or resolve_sit_dat()
    return bool(db) and Path(db).is_file()


def find_blocks_dat(search_dirs: list[Path] | None = None) -> Path:
    candidates: list[Path] = [cfg.blocks_dat_path()]
    for d in search_dirs or []:
        candidates.append(Path(d) / cfg.BLOCKS_DAT)
    for p in candidates:
        if p.is_file():
            return p.resolve()
    raise FileNotFoundError(
        f"{cfg.BLOCKS_DAT} not found; expected {cfg.blocks_dat_path()}"
    )


def blocks_without_carbonate_phases(blocks: str) -> str:
    """Drop Li2CO3 (and trailing PHASES blocks) from NIST46 blocks for OH-only decks."""
    text = blocks.rstrip()
    text = re.sub(
        r"\nPHASES\s*\nLi2CO3\b[\s\S]*?(?=\n(?:PHASES|SOLUTION|END)\b|\Z)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.rstrip() + "\n"


def phr_header(
    blocks: str,
    eq_list: str,
    *,
    quiet: bool = False,
    include_co3: bool = True,
    oh_titration: bool = False,
    oh_file: str = "speciation_results/OH_sweep.csv",
    co3_file: str = "speciation_results/CO3_sweep.csv",
) -> str:
    print_block = "PRINT\n    -reset false\n\n" if quiet else ""
    if oh_titration:
        punch_headings = (
            "comp_id sim_id ligand ratio track pH naoh_cum "
            "m_Li m_Ni m_Co m_Mn "
            "log10_a_Al log10_a_Fe log10_a_Cu log10_a_Co log10_a_Ni log10_a_Mn "
            "description"
        )
        title = "Black-mass OH precipitation screen (incremental NaOH)"
    else:
        punch_headings = (
            "comp_id sim_id ligand ratio track pH_set dose_Na2CO3 "
            "m_Li m_Ni m_Co m_Mn description"
        )
        title = (
            "NIST46 leachant screen — OH precipitation of NMC523+Li"
            if not include_co3
            else "NIST46 leachant screen — OH and CO3 precipitation of NMC523+Li"
        )
    co3_block = ""
    if include_co3:
        co3_block = f"""
SELECTED_OUTPUT {11}
    -file {co3_file}
    -active false
    -reset true
    -percent_error true
    -temperature true
    -pH true
    -pe true
    -ionic_strength true
    -charge_balance true
    -totals Li Al Fe Cu Co Ni Mn Na S C N
    -si {eq_list}
    -equilibrium_phases {eq_list}
USER_PUNCH {11}
    -headings {punch_headings}
"""
    fix_pH_block = ""
    if not oh_titration:
        fix_pH_block = """Fix_pH
    H+ = H+
    log_k 0
"""
    return f"""TITLE {title}

{print_block}{blocks}

PHASES
{fix_pH_block}Al(OH)3(am)
    Al(OH)3 = Al+3 - 3H+ + 3H2O
    log_k 6.5
Fe(OH)3(s)
    Fe(OH)3 = Fe+3 - 3H+ + 3H2O
    log_k 2.78
Cu(OH)2(s)
    Cu(OH)2 = Cu+2 - 2H+ + 2H2O
    log_k 8.34
Co(OH)2(s)
    Co(OH)2 = Co+2 - 2H+ + 2H2O
    log_k 13.11
Ni(OH)2(s)
    Ni(OH)2 = Ni+2 - 2H+ + 2H2O
    log_k 13.30
    delta_h -84.389
Mn(OH)2(s)
    Mn(OH)2 = Mn+2 - 2H+ + 2H2O
    log_k 15.28

KNOBS
    -iterations 7000
    -convergence_tolerance 1e-6
    -tolerance             1e-9

SELECTED_OUTPUT {SEL_OH}
    -file {oh_file}
    -active false
    -reset true
    -percent_error true
    -temperature true
    -pH true
    -pe true
    -ionic_strength true
    -charge_balance true
    -totals Li Al Fe Cu Co Ni Mn Na S C N
    -si {eq_list}
    -equilibrium_phases {eq_list}
USER_PUNCH {SEL_OH}
    -headings {punch_headings}
{co3_block}END
"""


def fe_line(fe_mol: float) -> str:
    if cfg.FE_STATE.lower() == "fe3+":
        return f"    Fe2(SO4)3       {fe_mol / 2.0:.7e}"
    return f"    FeSO4:7H2O      {fe_mol:.7e}"


def write_leachate(
    f,
    sol_id: int,
    ligand_key: str | None,
    ratio: float,
    comp: dict[str, Any] | None = None,
    label: str | None = None,
) -> None:
    s = cfg.SDL if comp is None else comp
    name = label or "NMC523_SDL"
    f.write(f"SOLUTION {sol_id} {name}\n")
    f.write(f"    temp {cfg.TEMP_C:.1f}\n")
    f.write("    pH 7 charge\n")
    f.write(f"    water {cfg.LEACHATE_VOLUME_KGW:.5f}\n")
    f.write("    units mol/kgw\n")
    f.write("REACTION 1\n")
    f.write(f"    Al2(SO4)3       {float(s['Al_sulfate_mol']):.7e}\n")
    f.write(fe_line(float(s["Fe_sulfate_mol"])) + "\n")
    f.write(f"    CuSO4           {float(s['Cu_sulfate_mol']):.7e}\n")
    f.write(f"    CoSO4:7H2O      {float(s['Co_sulfate_mol']):.7e}\n")
    f.write(f"    NiSO4:6H2O      {float(s['Ni_sulfate_mol']):.7e}\n")
    f.write(f"    MnSO4:H2O       {float(s['Mn_sulfate_mol']):.7e}\n")
    f.write(f"    Li2SO4:H2O      {float(s['Li_salt_mol']):.7e}\n")
    stiff = (
        ligand_key is not None
        and ratio > 0
        and str(ligand_key).lower() in {"nta", "edta"}
    )
    if ligand_key is not None and ratio > 0 and not stiff:
        moles = cfg.ligand_dose_mol(ratio, s)
        meta = cfg.LIGANDS[ligand_key]
        n_h = int(meta["n_acid_h"])
        if ligand_key == "ammonia":
            f.write(f"    NH3             {moles:.7e}\n")
        else:
            f.write(f"    {meta['phreeqc_master']}  {moles:.7e}\n")
            if n_h > 0:
                f.write(f"    H+              {n_h * moles:.7e}\n")
    f.write("    1.0\n")
    f.write(f"SAVE solution {sol_id}\nEND\n\n")
    if stiff:
        # One-shot addition of NTA/EDTA does not converge. Spread it over 10 steps.
        moles = cfg.ligand_dose_mol(ratio, s)
        meta = cfg.LIGANDS[str(ligand_key)]
        n_h = int(meta["n_acid_h"])
        f.write(f"USE solution {sol_id}\n")
        f.write("INCREMENTAL_REACTIONS true\n")
        f.write(f"REACTION {800000 + int(sol_id)}\n")
        f.write(f"    {meta['phreeqc_master']}  1.0\n")
        if n_h > 0:
            f.write(f"    H+              {n_h:g}\n")
        f.write(f"    {moles:.7e} moles in 10 steps\n")
        f.write(f"SAVE solution {sol_id}\n")
        f.write("INCREMENTAL_REACTIONS false\nEND\n\n")


def oh_phases() -> str:
    return "\n".join(f"    {p}  0.0 0" for p in cfg.OH_PHASES)


def oh_eq_name_list() -> str:
    """Equilibrium/SI list for OH precipitation only (no Fix_pH or carbonates)."""
    return " ".join(cfg.OH_PHASES)


def deactivate(sel_id: int) -> str:
    return f"SELECTED_OUTPUT {sel_id}\n    -active false\nEND\n\n"


def write_oh_naoh_titration(
    f,
    base_id: int,
    ligand: str,
    ratio: float,
    sim0: int,
    *,
    comp_id: int = 0,
    total_naoh: float | None = None,
    n_steps: int | None = None,
) -> int:
    """Single continuous NaOH titration: INCREMENTAL_REACTIONS + REACTION ... moles in N steps."""
    total = (
        float(cfg.BLACKMASS_NAOH_TITRATION_MOLES)
        if total_naoh is None
        else float(total_naoh)
    )
    steps = (
        int(cfg.BLACKMASS_NAOH_TITRATION_STEPS)
        if n_steps is None
        else int(n_steps)
    )
    if total <= 0 or steps < 1:
        raise ValueError("total_naoh must be > 0 and n_steps >= 1")
    sim = sim0 + 1
    delta = total / steps
    desc = f"OH_{ligand}_r{ratio:g}_incr"
    f.write(f"\n# incremental NaOH titration comp_id={comp_id} {ligand} r={ratio:g}\n")
    f.write(f"USE solution {base_id}\n")
    f.write("INCREMENTAL_REACTIONS true\n")
    f.write(f"EQUILIBRIUM_PHASES {sim}\n")
    f.write(oh_phases() + "\n")
    f.write(f"REACTION {sim}\n")
    f.write("    Na+  1.0\n")
    f.write("    OH-  1.0\n")
    f.write(f"    {total:g} moles in {steps} steps\n")
    f.write(f"SELECTED_OUTPUT {SEL_OH}\n")
    f.write("    -active true\n")
    f.write(f"USER_PUNCH {SEL_OH}\n")
    f.write(f'    10 ligand$ = "{ligand}"\n')
    f.write(f"    20 ratio = {ratio:.8g}\n")
    f.write(f"    30 comp_id = {int(comp_id)}\n")
    f.write(f"    40 sim_id = {sim}\n")
    f.write(f"    50 naoh_cum = {delta:.12g} * STEP_NO\n")
    f.write(f'    60 desc$ = "{desc}"\n')
    f.write(
        '    70 PUNCH comp_id, sim_id, ligand$, ratio, "OH", PH, naoh_cum, '
        'TOT("Li"), TOT("Ni"), TOT("Co"), TOT("Mn"), '
        'LOG10(ACT("Al+3")), LOG10(ACT("Fe+2")), LOG10(ACT("Cu+2")), '
        'LOG10(ACT("Co+2")), LOG10(ACT("Ni+2")), LOG10(ACT("Mn+2")), desc$\n'
    )
    f.write("END\n\n")
    f.write("INCREMENTAL_REACTIONS false\n")
    f.write(deactivate(SEL_OH))
    return sim


DEFAULT_KNOBS = """KNOBS
    -iterations 7000
    -convergence_tolerance 1e-6
    -tolerance             1e-9
    -diagonal_scale        true
    -pe_step_size          2
    -step_size             50
"""

# NTA and EDTA exceed 7000 iterations on black-mass feeds (see NTA error.inp, simulation 2339).
# Oxalic at ligand:NMC = 2 also exhausts 7000 iterations and then aborts the rest of the file.
# Keep step_size at 50. A smaller step aborts the initial sulfate solution.
STIFF_KNOBS = """KNOBS
    -iterations 20000
    -convergence_tolerance 1e-8
    -tolerance             1e-12
    -diagonal_scale        true
    -pe_step_size          2
    -step_size             50
"""

PUNCH_HEADINGS = (
    "comp_id sim_id ligand ratio reagent stage dose_mol "
    "m_Li m_Ni m_Co m_Mn "
    "f_Li f_Ni f_Co f_Mn f_Al f_Fe f_Cu description pH pre_dose_mol"
)


def knobs_for(ligand: str) -> str:
    if str(ligand).strip().lower() in {"nta", "edta"}:
        return STIFF_KNOBS
    return DEFAULT_KNOBS


_SIT_CACHE: str | None = None
_BOUND_CACHE: dict[str, dict[str, list[str]]] = {}


def sit_database_text() -> str:
    """sit.dat contents, or empty when the database is not on this machine."""
    global _SIT_CACHE
    if _SIT_CACHE is not None:
        return _SIT_CACHE
    path = resolve_sit_dat()
    if not path:
        _SIT_CACHE = ""
        return _SIT_CACHE
    _SIT_CACHE = Path(path).read_text(encoding="utf-8", errors="replace")
    return _SIT_CACHE


def bound_for(ligand_key: str | None, blocks: str) -> dict[str, list[str]]:
    """Aqueous ligand complexes for USER_PUNCH. Empty if the ligand is absent."""
    from complexes import parse_bound_species

    if not ligand_key:
        return {}
    key = str(ligand_key).strip()
    if key.lower() not in {name.lower() for name in cfg.LIGANDS}:
        return {}
    canonical = next(name for name in cfg.LIGANDS if name.lower() == key.lower())
    if canonical in _BOUND_CACHE:
        return _BOUND_CACHE[canonical]
    species = str(cfg.LIGANDS[canonical]["phreeqc_species"])
    found = parse_bound_species([blocks, sit_database_text()], species)
    _BOUND_CACHE[canonical] = found
    return found


def template_header(
    blocks: str,
    eq_list: str,
    out_file: str,
    *,
    knobs: str | None = None,
    phase_extra: str = "",
) -> str:
    """Header for reagent-template decks. Includes Fix_pH and the NIST overlay."""
    headings = PUNCH_HEADINGS
    knobs_block = DEFAULT_KNOBS if knobs is None else knobs
    return f"""TITLE Precipitation template

PRINT
    -reset false

{blocks.rstrip()}

PHASES
Fix_pH
    H+ = H+
    log_k 0
Fe(OH)3(s)
    Fe(OH)3 = Fe+3 - 3H+ + 3H2O
    log_k 2.78
Cu(OH)2(s)
    Cu(OH)2 = Cu+2 - 2H+ + 2H2O
    log_k 8.34
Co(OH)2(s)
    Co(OH)2 = Co+2 - 2H+ + 2H2O
    log_k 13.11
Ni(OH)2(s)
    Ni(OH)2 = Ni+2 - 2H+ + 2H2O
    log_k 13.30
    delta_h -84.389
Mn(OH)2(s)
    Mn(OH)2 = Mn+2 - 2H+ + 2H2O
    log_k 15.28
{phase_extra}
{knobs_block.rstrip()}

SELECTED_OUTPUT {SEL_OH}
    -file {out_file}
    -active false
    -reset true
    -percent_error true
    -temperature true
    -pH true
    -pe true
    -ionic_strength true
    -charge_balance true
    -totals Li Al Fe Cu Co Ni Mn Na S C N
    -si {eq_list}
    -equilibrium_phases {eq_list}
USER_PUNCH {SEL_OH}
    -headings {headings}
END
"""


def read_speciation_csv(path: Path) -> pd.DataFrame:
    path = Path(path)
    last_err: Exception | None = None
    for kwargs in (
        {"comment": "#"},
        {},
        {"skiprows": 1},
    ):
        try:
            df = pd.read_csv(path, **kwargs)
        except (OSError, pd.errors.ParserError, ValueError, UnicodeDecodeError) as exc:
            last_err = exc
            continue
        if df.empty:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        return df
    if last_err is not None:
        raise last_err
    return pd.DataFrame()


def consolidate(results_dir: Path, dest: Path, pattern: str) -> Path | None:
    files = sorted(Path(p) for p in glob.glob(str(results_dir / pattern)))
    files = [p for p in files if p.resolve() != dest.resolve()]
    if not files:
        return None
    frames: list[pd.DataFrame] = []
    for path in files:
        try:
            df = read_speciation_csv(path)
        except (OSError, pd.errors.ParserError, ValueError) as exc:
            log(f"WARNING: skip {path.name}: {exc}")
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return None
    merged = pd.concat(frames, ignore_index=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(dest, index=False)
    log(f"Consolidated -> {dest.name} rows={len(merged)}")
    return dest


def clear_shard_results(
    work: Path,
    *,
    phr_prefix: str,
    sweep_pattern: str,
    dest_name: str,
) -> None:
    """Drop outs, shard CSVs, and merged CSV so a rewritten deck is not skipped by --resume."""
    n = 0
    for phr in work.glob(f"{phr_prefix}_*.phr"):
        out = work / f"{phr.stem}.out"
        if out.is_file():
            out.unlink()
            n += 1
    sweep_dir = work / cfg.SWEEP_RESULTS_DIRNAME
    if sweep_dir.is_dir():
        shard_glob = sweep_pattern.format(shard="*")
        for path in sweep_dir.glob(shard_glob):
            path.unlink()
            n += 1
    dest = work / dest_name
    if dest.is_file():
        dest.unlink()
        n += 1
    if n:
        log(f"Cleared {n} stale result file(s) in {work}")


def write_stage_decks(
    work: Path,
    *,
    phr_prefix: str,
    blocks: str,
    stages: list[Any],
    df: pd.DataFrame,
    blob: str,
    shard_size: int,
    phase_extra: str = "",
) -> int:
    """Write precip_*_NN.phr shards. One PHREEQC failure then loses only this shard."""
    from stages import eq_name_list, write_chain

    import feed

    size = max(1, int(shard_size))
    if len(stages) >= 3 and size >= 1000:
        raise ValueError(
            "3-stage decks need shard_size < 1000 so filtrate solution ids do not collide"
        )
    n = len(df)
    n_shards = max(1, math.ceil(n / size)) if n else 0
    for i in range(n_shards):
        chunk = df.iloc[i * size : (i + 1) * size]
        if chunk.empty:
            continue
        out_file = f"{cfg.SWEEP_RESULTS_DIRNAME}/{phr_prefix}_{i:02d}.csv"
        phr_path = work / f"{phr_prefix}_{i:02d}.phr"
        sim = 0
        sol_id = 0
        names = {str(x).strip().lower() for x in chunk["ligand"]} if "ligand" in chunk.columns else set()
        knobs = STIFF_KNOBS if names & {"nta", "edta", "oxalic"} else DEFAULT_KNOBS
        with phr_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(
                template_header(
                    blocks,
                    eq_name_list(stages),
                    out_file,
                    knobs=knobs,
                    phase_extra=phase_extra,
                )
            )
            for _, row in chunk.iterrows():
                comp = feed.composition_dict(row)
                comp_id = int(row["comp_id"])
                ligand = str(row["ligand"])
                ratio = float(row["ratio"])
                ligand_key = None if ligand.lower() in {"none", ""} or ratio <= 0 else ligand
                sol_id += 1
                write_leachate(
                    f,
                    sol_id,
                    ligand_key,
                    ratio,
                    comp=comp,
                    label=f"comp{comp_id}_{ligand}",
                )
                nmc = float(row["nmc_total_mol"]) if "nmc_total_mol" in row.index else 1.0
                mn = float(row["Mn_sulfate_mol"]) if "Mn_sulfate_mol" in row.index else 1.0
                sim = write_chain(
                    f,
                    stages,
                    sol_id=sol_id,
                    sim0=sim,
                    comp_id=comp_id,
                    ligand=ligand,
                    ratio=ratio,
                    blob=blob,
                    nmc_total=nmc,
                    mn_total=mn,
                    bound=bound_for(ligand_key, blocks),
                )
        log(f"Shard {i:02d}: {len(chunk)} comps -> {phr_path.name}")
    return n_shards


def output_run_finished(out: Path) -> bool:
    """True only if PHREEQC reached End of Run (not a mid-deck Stopping.)."""
    if not out.is_file() or out.stat().st_size <= 0:
        return False
    with out.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        n = min(fh.tell(), 12000)
        fh.seek(-n, os.SEEK_END)
        tail = fh.read().decode("utf-8", "replace")
    return "End of Run" in tail and "Stopping." not in tail


def run_one(exe: str, db: str, phr_path: Path, *, progress: bool = True) -> Path:
    work_root = phr_path.parent
    out = work_root / f"{phr_path.stem}.out"
    sweep_dir = cfg.SWEEP_RESULTS_DIRNAME
    (work_root / sweep_dir).mkdir(parents=True, exist_ok=True)
    n_guess = max(phr_path.read_text(encoding="utf-8", errors="replace").count("USE solution"), 1)
    cmd = [exe, phr_path.name, out.name, db]
    log(f"PHREEQC {phr_path.name}")
    t0 = time.time()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(work_root),
    )
    sim_re = re.compile(r"Reading input data for simulation\s+(\d+)", re.IGNORECASE)
    bar = tqdm(total=n_guess, unit="sim", desc=phr_path.stem, ncols=80) if tqdm and progress else None
    errors: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        if sim_re.search(line) and bar is not None:
            bar.update(1)
        if "ERROR" in line.upper():
            errors.append(line.rstrip())
    if bar is not None:
        bar.close()
    proc.wait()
    elapsed = time.time() - t0
    log(f"  rc={proc.returncode} wall={elapsed:.0f}s out={out.name}")
    if errors:
        log(f"  WARNING: {len(errors)} error line(s)")
        for e in errors[:10]:
            log(f"    {e}")
    if proc.returncode != 0:
        raise RuntimeError(f"PHREEQC failed on {phr_path.name}; inspect {out}")
    return out


def run_shards(
    work: Path,
    *,
    phr_prefix: str,
    sweep_pattern: str,
    dest_name: str,
    resume: bool = False,
    shard: int | None = None,
    jobs: int | None = None,
) -> Path:
    exe = resolve_phreeqc_exe()
    db = resolve_sit_dat()
    if exe is None:
        raise RuntimeError("phreeqc not found; set PHREEQC_EXE")
    if db is None:
        raise RuntimeError("sit.dat not found; set PHREEQC_DB")
    log(f"PHREEQC : {exe}")
    log(f"sit.dat : {db}")
    shards = sorted(work.glob(f"{phr_prefix}_*.phr"))
    if shard is not None:
        want = f"{phr_prefix}_{shard:02d}.phr"
        shards = [p for p in shards if p.name == want]
    if not shards:
        raise FileNotFoundError(f"No {phr_prefix}_*.phr in {work}")
    if jobs is None:
        raw = os.environ.get("PRECIP_JOBS", "").strip()
        jobs = int(raw) if raw else 1
    workers = max(1, int(jobs))
    failures: list[str] = []
    pending: list[Path] = []
    for phr_path in shards:
        shard_id = phr_path.stem.split("_")[-1]
        sweep = work / cfg.SWEEP_RESULTS_DIRNAME / sweep_pattern.format(shard=shard_id)
        out = work / f"{phr_path.stem}.out"
        if resume and sweep.is_file() and sweep.stat().st_size > 0 and output_run_finished(out):
            log(f"RESUME skip {phr_path.name} (found {sweep.name})")
            continue
        if resume and sweep.is_file() and sweep.stat().st_size > 0 and not output_run_finished(out):
            log(f"RESUME rerun {phr_path.name} (incomplete {sweep.name})")
        pending.append(phr_path)
    if workers == 1:
        for phr_path in pending:
            try:
                run_one(exe, db, phr_path)
            except RuntimeError as exc:
                log(f"ERROR: {exc}")
                failures.append(phr_path.name)
    else:
        log(f"PHREEQC parallel jobs={workers} shards={len(pending)}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(run_one, exe, db, phr_path, progress=False): phr_path
                for phr_path in pending
            }
            for fut in as_completed(futures):
                phr_path = futures[fut]
                try:
                    fut.result()
                except RuntimeError as exc:
                    log(f"ERROR: {exc}")
                    failures.append(phr_path.name)
    dest = work / dest_name
    consolidate(work / cfg.SWEEP_RESULTS_DIRNAME, dest, sweep_pattern.format(shard="*"))
    if failures:
        raise RuntimeError(f"PHREEQC failed on {len(failures)} shard(s): {', '.join(failures)}")
    return dest


def ensure_lib_path() -> None:
    lib = str(cfg.OH_PRECIP_ROOT / "lib")
    if lib not in sys.path:
        sys.path.insert(0, lib)
