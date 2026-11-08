# -*- coding: utf-8 -*-
"""Ligand-free PHREEQC decks.

Stage 1 follows one black-mass schedule:
  batch       all salts first, then reagent 1
  continuous  salts and reagent 1 in the same incremental reaction
  staged      each recorded step is its own equilibrium (the salt fraction
              is constant between black-mass dumps, so one incremental
              stoichiometry cannot trace it). Equilibrium is path-independent,
              so each step adds the cumulative totals to fresh water.

Stage 2, when requested, adds all of reagent 1, drops the solids, and
then adds all of reagent 2.

Hydroxide phases that the deck overrides include delta_h and no -analytic
line, so TEMP_C is not locked at 25 C. A carbonate reagent writes its
carbonate solids into this file. sit.dat blocks are copied with -analytic.
Li2CO3 is not in sit.dat, so the deck constant is written instead.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from paths import OVERLAY_DAT, SIT_DAT  # noqa: E402

# formula, metal atoms in that formula, metal atoms in one CSV mole.
SALTS: dict[str, dict[str, tuple[str, int, int]]] = {
    "sulfate": {
        "Al": ("Al2(SO4)3", 2, 2),
        "Fe": ("Fe2(SO4)3", 2, 1),
        "Cu": ("CuSO4", 1, 1),
        "Co": ("CoSO4:7H2O", 1, 1),
        "Ni": ("NiSO4:6H2O", 1, 1),
        "Mn": ("MnSO4:H2O", 1, 1),
        "Li": ("Li2SO4:H2O", 2, 2),
    },
    "chloride": {
        "Al": ("AlCl3:6H2O", 1, 1),
        "Fe": ("FeCl3:6H2O", 1, 1),
        "Cu": ("CuCl2:2H2O", 1, 1),
        "Co": ("CoCl2:6H2O", 1, 1),
        "Ni": ("NiCl2:6H2O", 1, 1),
        "Mn": ("MnCl2:4H2O", 1, 1),
        "Li": ("LiCl:H2O", 1, 1),
    },
}
ANION_ELEMENT = {"sulfate": "S", "chloride": "Cl"}
COLUMN = re.compile(r"^([A-Z][a-z]?)_(sulfate|chloride|salt)_mol$")

# name -> (reaction, log_k at 25 C, delta_h kJ/mol, note)
# log_k matches the AgentLoopDemo deck. delta_h is from sit.dat where the
# same dissolution reaction exists. Fe and Mn have no sit.dat delta_h; the
# values below are only so temperature moves those two phases.
DECK_HYDROXIDES: dict[str, tuple[str, float, float, str]] = {
    "Fe(OH)3(s)": (
        "Fe(OH)3 = Fe+3 - 3H+ + 3H2O",
        2.78,
        -88.0,
        "delta_h estimated; sit.dat Ferrihydrite(s) has log_k 2.78 and no delta_h",
    ),
    "Cu(OH)2(s)": (
        "Cu(OH)2 = Cu+2 - 2H+ + 2H2O",
        8.34,
        -62.764,
        "delta_h from sit.dat Cu(OH)2(s); log_k is the deck value (sit.dat is 8.64)",
    ),
    "Co(OH)2(s)": (
        "Co(OH)2 = Co+2 - 2H+ + 2H2O",
        13.11,
        -93.56,
        "delta_h from sit.dat Co(OH)2(s,rose2)",
    ),
    "Ni(OH)2(s)": (
        "Ni(OH)2 = Ni+2 - 2H+ + 2H2O",
        13.30,
        -84.389,
        "delta_h from sit.dat Ni(OH)2(s); log_k is the deck value (sit.dat is 11.03)",
    ),
    "Mn(OH)2(s)": (
        "Mn(OH)2 = Mn+2 - 2H+ + 2H2O",
        15.28,
        -92.0,
        "delta_h estimated; sit.dat Pyrochroite has log_k 15.3 and no delta_h",
    ),
}
LI2CO3 = (
    "Li2CO3",
    "Li2CO3 = 2Li+ + CO3-2",
    -2.9,
    "nist46_leachant_blocks.dat; no delta_h in that file, so this phase stays at its 25 C log_k",
)


class DeckError(Exception):
    """The deck cannot be written."""


def f7(value: float) -> str:
    return f"{value:.7e}"


def load_feed_row(csv_path: Path, comp_id: int | None) -> tuple[dict, str, list[tuple[str, str, float]], dict[str, float]]:
    """Return the CSV row, anion, salt lines (metal, formula, deck mol), and metal moles."""
    import pandas as pd

    if not csv_path.is_file():
        raise DeckError(f"Feed CSV not found: {csv_path}")
    frame = pd.read_csv(csv_path)
    if frame.empty:
        raise DeckError(f"Feed CSV has no rows: {csv_path}")
    if comp_id is None:
        row = frame.iloc[0]
    else:
        hit = frame[frame["comp_id"].astype(int) == int(comp_id)]
        if hit.empty:
            raise DeckError(f"comp_id {comp_id} is not in {csv_path.name}.")
        row = hit.iloc[0]
    found: list[tuple[str, str, float]] = []
    for column, value in row.items():
        match = COLUMN.match(str(column))
        if match is None:
            continue
        mol = float(value)
        if mol < 0:
            raise DeckError(f"{column} is negative.")
        if mol > 0:
            found.append((match.group(1), match.group(2), mol))
    anions = {tag for _m, tag, _v in found if tag != "salt"}
    if len(anions) != 1:
        raise DeckError(f"The CSV must use one anion. Found {sorted(anions) or 'none'}.")
    anion = anions.pop()
    salts: list[tuple[str, str, float]] = []
    feed_mol: dict[str, float] = {}
    for metal, _tag, mol in found:
        entry = SALTS[anion].get(metal)
        if entry is None:
            raise DeckError(f"No {anion} formula for {metal}.")
        formula, deck_atoms, csv_atoms = entry
        deck_mol = mol * csv_atoms / deck_atoms
        salts.append((metal, formula, deck_mol))
        feed_mol[metal] = deck_mol * deck_atoms
    if not salts:
        raise DeckError("The selected row has no salt moles.")
    return row.to_dict(), anion, salts, feed_mol


def schedule(bm_add: str, bm_batches: int, add: str, steps: int, amount: float) -> list[tuple[float, float]]:
    """(black-mass fraction, reagent moles) at each recorded stage-1 step."""
    if bm_add == "batch":
        n = 1 if add == "once" else steps
        return [(1.0, amount * s / n) for s in range(1, n + 1)]
    if bm_add == "continuous":
        n = steps
        return [(s / n, amount * s / n) for s in range(1, n + 1)]
    k = bm_batches
    if add == "once":
        return [((b + 1) / k, amount) for b in range(k)]
    n = max(k, steps)
    points = []
    for s in range(1, n + 1):
        batch_index = min(k, int((s * k + n - 1) // n))
        points.append((batch_index / k, amount * s / n))
    return points


_SIT_TEXT: str | None = None


def _sit_text() -> str:
    global _SIT_TEXT
    if _SIT_TEXT is None:
        _SIT_TEXT = SIT_DAT.read_text(encoding="utf-8", errors="replace") if SIT_DAT.is_file() else ""
    return _SIT_TEXT


def _database_phase(name: str) -> list[str] | None:
    """Copy one sit.dat PHASES entry, including log_k, delta_h, and -analytic."""
    lines = _sit_text().splitlines()
    for index, line in enumerate(lines):
        if line != name:
            continue
        block = [name]
        for nxt in lines[index + 1:]:
            if not nxt.strip():
                break
            # sit.dat puts the dissolution reaction on the next line with no indent.
            if nxt[0] in " \t#":
                block.append(nxt.rstrip())
                continue
            if "=" in nxt:
                block.append("    " + nxt.strip())
                continue
            break
        if any("log_k" in item for item in block):
            return block
    return None


def _is_carbonate(name: str, reaction: str | None) -> bool:
    text = f"{name} {reaction or ''}"
    return "CO3" in text or "co3" in text


def _reagent_elements(reaction: dict[str, float]) -> list[str]:
    found: list[str] = []
    for species in reaction:
        for element in re.findall(r"[A-Z][a-z]?", species):
            if element not in {"H", "O"} and element not in found:
                found.append(element)
    return found


def phase_plan(thermo: dict, feed_metals: list[str]) -> tuple[list[str], list[str]]:
    """Return (equilibrium phase names, PHASES-block lines) for one reagent."""
    doc = thermo.get("reagent", {})
    if not doc.get("enabled"):
        missing = ", ".join(doc.get("missing") or ["unknown"])
        raise DeckError(f"Reagent {doc.get('name')} is disabled. Missing: {missing}.")
    if doc.get("companion"):
        raise DeckError(f"Reagent {doc.get('name')} needs a companion. This loop does not add one.")
    if not doc.get("reaction"):
        raise DeckError(f"Reagent {doc.get('name')} has no reaction.")
    names: list[str] = []
    lines: list[str] = []
    for phase in doc.get("phases", []):
        name = phase.get("phase")
        if not name:
            continue
        log_k = phase.get("log_k")
        reaction = phase.get("reaction")
        source = phase.get("source")
        if name == LI2CO3[0] and log_k is None:
            reaction = LI2CO3[1]
            log_k = LI2CO3[2]
            source = "deck"
        if log_k is None:
            continue
        named = phase.get("metals") or []
        if named and not (set(named) & set(feed_metals)):
            continue
        if name not in names:
            names.append(name)
        if name in DECK_HYDROXIDES:
            deck_reaction, deck_log_k, delta_h, note = DECK_HYDROXIDES[name]
            lines += [name, f"    {deck_reaction}", f"    log_k {deck_log_k:g}", f"    delta_h {delta_h:g}  # {note}"]
        elif _is_carbonate(name, reaction):
            copied = _database_phase(name)
            if copied:
                lines += copied
            else:
                note = LI2CO3[3] if name == LI2CO3[0] else "carbonate solid"
                lines += [name, f"    {reaction}", f"    log_k {float(log_k):g}  # {note}"]
                if phase.get("delta_h_kJ_mol") is not None:
                    lines.append(f"    delta_h {float(phase['delta_h_kJ_mol']):g}")
        elif source not in {None, "sit.dat"} and reaction:
            lines += [name, f"    {reaction}", f"    log_k {float(log_k):g}"]
            if phase.get("delta_h_kJ_mol") is not None:
                lines.append(f"    delta_h {float(phase['delta_h_kJ_mol']):g}")
    if not names:
        raise DeckError("The reagent has no phase with a log_k for this feed.")
    return names, lines


def union_phases(plans: list[tuple[list[str], list[str]]]) -> tuple[list[str], list[str]]:
    names: list[str] = []
    lines: list[str] = []
    seen: set[str] = set()
    for phase_names, block in plans:
        for name in phase_names:
            if name not in names:
                names.append(name)
        for line in block:
            head = line.strip().split()[0] if line[:1] not in " #" else ""
            if head and not line.startswith(" ") and head not in seen and not line.startswith("#"):
                seen.add(head)
            if line.startswith(" ") or line.startswith("#") or (line.strip() and line.strip().split()[0] not in seen):
                pass
        # Re-copy blocks by phase name once.
    written: set[str] = set()
    for _names, block in plans:
        current = None
        chunk: list[str] = []
        def flush() -> None:
            nonlocal current, chunk
            if current and current not in written and chunk:
                lines.extend(chunk)
                written.add(current)
            current, chunk = None, []
        for line in block:
            if line and not line.startswith((" ", "#")):
                flush()
                current = line.split()[0]
                chunk = [line]
            else:
                chunk.append(line)
        flush()
    return names, lines


def _totals(feed_metals: list[str], reactions: list[dict], anion: str) -> list[str]:
    totals = list(feed_metals)
    for reaction in reactions:
        for element in _reagent_elements(reaction):
            if element not in totals:
                totals.append(element)
    extra = ANION_ELEMENT[anion]
    if extra not in totals:
        totals.append(extra)
    return totals


def _selected(num: int, path: Path, totals: list[str], phases: list[str], *, equilibrium: bool) -> list[str]:
    out = [
        f"SELECTED_OUTPUT {num}",
        f"    -file   {path.resolve().as_posix()}",
        "    -reset  true",
        "    -pH     true",
        "    -pe     true",
        "    -temperature true",
        "    -ionic_strength true",
        f"    -totals {' '.join(totals)}",
    ]
    if phases:
        if equilibrium:
            out.append(f"    -equilibrium_phases {' '.join(phases)}")
        out.append(f"    -si {' '.join(phases)}")
    return out


def _punch(num: int, seq: str, reagent: str, bm: str) -> list[str]:
    return [
        f"USER_PUNCH {num}",
        "    -headings seq reagent_cum_mol bm_cum_frac",
        f"    10 PUNCH {seq}, {reagent}, {bm}",
    ]


def _solution(temp_c: float, water_kg: float) -> list[str]:
    return [
        "SOLUTION 1 leachate",
        f"    temp      {temp_c:g}",
        "    pH        7 charge",
        f"    water     {water_kg:g}",
        "    units     mol/kgw",
    ]


def _salt_reaction(salts: list[tuple[str, str, float]], frac: float, reaction_id: int) -> list[str]:
    lines = [f"REACTION {reaction_id}"]
    for metal, formula, mol in salts:
        lines.append(f"    {formula:<16}{f7(mol * frac)}")
    lines.append("    1.0")
    return lines


def _reagent_lines(reaction: dict[str, float], moles: float) -> list[str]:
    return [f"    {species:<16}{f7(coef * moles)}" for species, coef in reaction.items()]


def _knobs() -> list[str]:
    return [
        "KNOBS",
        "    -iterations 7000",
        "    -convergence_tolerance 1e-6",
        "    -tolerance             1e-9",
        "",
    ]


def _header(temp_c: float, water_kg: float, bm_add: str, reagent: str, amount: float, phases_lines: list[str]) -> list[str]:
    note = [
        f"TITLE Ligand-free black mass, {reagent}, {temp_c:g} C, {water_kg:g} kg water",
        f"# BM_ADD {bm_add}. Reagent {reagent} {amount:g} mol. No ligand.",
        "# Deck hydroxides below carry delta_h and do not copy -analytic.",
        "",
    ]
    if phases_lines:
        note += ["PHASES", *phases_lines, ""]
    note += _knobs()
    return note


def build_stage1(
    *,
    temp_c: float,
    water_kg: float,
    bm_add: str,
    bm_batches: int,
    add: str,
    steps: int,
    amount: float,
    reagent: str,
    reaction: dict[str, float],
    salts: list[tuple[str, str, float]],
    phases: list[str],
    phase_lines: list[str],
    totals: list[str],
    stage_a_csv: Path,
    stage1_csv: Path,
) -> str:
    points = schedule(bm_add, bm_batches, add, steps, amount)
    out = _header(temp_c, water_kg, bm_add, reagent, amount, phase_lines)
    # Stage A: black mass only when the whole feed is present before any reagent.
    out += ["# STAGE A", *_solution(temp_c, water_kg)]
    if bm_add == "batch":
        out += _salt_reaction(salts, 1.0, 1)
    out += _selected(1, stage_a_csv, totals, phases, equilibrium=False)
    out += ["SAVE solution 1", "END", "", "SELECTED_OUTPUT 1", "    -active false", "END", ""]

    linear = bm_add in {"batch", "continuous"}
    if linear:
        out += _incremental_stage1(
            bm_add=bm_add,
            points=points,
            reaction=reaction,
            salts=salts,
            phases=phases,
            totals=totals,
            stage1_csv=stage1_csv,
            amount=amount,
        )
    else:
        out += _point_stage1(
            points=points,
            temp_c=temp_c,
            water_kg=water_kg,
            reaction=reaction,
            salts=salts,
            phases=phases,
            totals=totals,
            stage1_csv=stage1_csv,
        )
    return "\n".join(out).rstrip() + "\n"


def _incremental_stage1(*, bm_add, points, reaction, salts, phases, totals, stage1_csv, amount) -> list[str]:
    n = len(points)
    out = [
        "# STAGE 1 incremental",
        "USE solution 1",
        "INCREMENTAL_REACTIONS true",
        "EQUILIBRIUM_PHASES 1",
    ]
    out += [f"    {name}  0.0  0" for name in phases]
    out += ["REACTION 2"]
    if bm_add == "continuous":
        for metal, formula, mol in salts:
            out.append(f"    {formula:<16}{f7(mol)}")
        out += _reagent_lines(reaction, amount)
        out.append(f"    1.0 moles in {n} steps")
        reagent_expr = f"({amount:.12g}/{n})*STEP_NO"
        bm_expr = f"STEP_NO/{n}"
    else:
        step_mol = amount / n
        out += [f"    {species:<16}{coef:g}" for species, coef in reaction.items()]
        out.append(f"    {f7(amount)} moles in {n} steps")
        reagent_expr = f"({step_mol:.12g})*STEP_NO"
        bm_expr = "1"
    out += _selected(2, stage1_csv, totals, phases, equilibrium=True)
    out += _punch(2, "STEP_NO", reagent_expr, bm_expr)
    out += ["END", "", "INCREMENTAL_REACTIONS false", "END", ""]
    return out


def _point_stage1(*, points, temp_c, water_kg, reaction, salts, phases, totals, stage1_csv) -> list[str]:
    """One fresh equilibrium per staged step. The selected-output file is opened once.

    Repeating ``-file`` inside the loop makes PHREEQC truncate the table, so only
    the last step would remain.
    """
    out: list[str] = ["# STAGE 1 staged points"]
    out += _selected(2, stage1_csv, totals, phases, equilibrium=True)
    out += [
        "    -active false",
        "USER_PUNCH 2",
        "    -headings seq reagent_cum_mol bm_cum_frac",
        "END",
        "",
    ]
    for seq, (bm_frac, reagent_mol) in enumerate(points, start=1):
        out += [
            f"# step {seq} bm {bm_frac:g} reagent {reagent_mol:g}",
            *_solution(temp_c, water_kg),
            "REACTION 1",
        ]
        for metal, formula, mol in salts:
            out.append(f"    {formula:<16}{f7(mol * bm_frac)}")
        out += _reagent_lines(reaction, reagent_mol)
        out += ["    1.0", "EQUILIBRIUM_PHASES 1"]
        out += [f"    {name}  0.0  0" for name in phases]
        out += ["SELECTED_OUTPUT 2", "    -active true"]
        out += _punch(2, str(seq), f"{reagent_mol:.12g}", f"{bm_frac:.12g}")
        out += ["END", "", "SELECTED_OUTPUT 2", "    -active false", "END", ""]
    return out


def build_stage2(
    *,
    temp_c: float,
    water_kg: float,
    salts: list[tuple[str, str, float]],
    bm_frac: float,
    reagent1: str,
    reagent1_mol: float,
    reaction1: dict[str, float],
    phases1: list[str],
    reagent2: str,
    amount2: float,
    add2: str,
    steps2: int,
    reaction2: dict[str, float],
    phases2: list[str],
    phase_lines: list[str],
    totals: list[str],
    filtrate_csv: Path,
    stage2_csv: Path,
) -> str:
    n2 = 1 if add2 == "once" else steps2
    out = _header(temp_c, water_kg, "stage2-filtrate", reagent1, reagent1_mol, phase_lines)
    out += [
        "# Reproduce the stage-1 split, then drop solids.",
        *_solution(temp_c, water_kg),
        "REACTION 1",
    ]
    if bm_frac > 0:
        for metal, formula, mol in salts:
            out.append(f"    {formula:<16}{f7(mol * bm_frac)}")
    out += _reagent_lines(reaction1, reagent1_mol)
    out += ["    1.0", "EQUILIBRIUM_PHASES 1"]
    out += [f"    {name}  0.0  0" for name in phases1]
    out += _selected(1, filtrate_csv, totals, phases1, equilibrium=True)
    out += _punch(1, "0", f"{reagent1_mol:.12g}", f"{bm_frac:.12g}")
    out += ["SAVE solution 20001", "END", "", "SELECTED_OUTPUT 1", "    -active false", "END", ""]
    both = list(dict.fromkeys([*phases1, *phases2]))
    out += [
        f"# STAGE 2 add {reagent2}",
        "USE solution 20001",
    ]
    if n2 > 1:
        out.append("INCREMENTAL_REACTIONS true")
    out += ["EQUILIBRIUM_PHASES 2"]
    out += [f"    {name}  0.0  0" for name in both]
    out += [f"REACTION 2 add_{re.sub(r'[^A-Za-z0-9]', '', reagent2)}"]
    out += [f"    {species:<16}{coef:g}" for species, coef in reaction2.items()]
    if n2 > 1:
        out.append(f"    {f7(amount2)} moles in {n2} steps")
        reagent_expr = f"({amount2 / n2:.12g})*STEP_NO"
        seq_expr = "STEP_NO"
    else:
        out.append(f"    {f7(amount2)} moles")
        reagent_expr = f"{amount2:.12g}"
        seq_expr = "1"
    out += _selected(2, stage2_csv, totals, both, equilibrium=True)
    out += _punch(2, seq_expr, reagent_expr, f"{bm_frac:.12g}")
    out += ["END"]
    if n2 > 1:
        out += ["", "INCREMENTAL_REACTIONS false", "END"]
    out.append("")
    return "\n".join(out).rstrip() + "\n"


def write_feed_mol(path: Path, feed_mol: dict[str, float], comp_id: int, anion: str) -> None:
    path.write_text(
        json.dumps({"comp_id": comp_id, "anion": anion, "feed_mol": feed_mol, "basis": "metal_moles"}, indent=2) + "\n",
        encoding="utf-8",
    )
