# -*- coding: utf-8 -*-
"""Ask for a ligand, salts, a precipitant, and metals. Print related thermo as JSON.

Run from any directory:

    python pipelines/agentloop/AgentLoopDemo/script/02_ask_thermo/ask_thermo.py

Each value is a flag. A missing flag is asked on the command line:

    python pipelines/agentloop/AgentLoopDemo/script/02_ask_thermo/ask_thermo.py --ligand tartaric --salts sulfate --reagent NaOH --metals Ni,Co,Mn
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA = Path(__file__).resolve().parents[1] / "data"
SIT_DAT = _DATA / "sit.dat"
OVERLAY_DAT = _DATA / "nist46_leachant_blocks.dat"
PHREEQC_IO = _DATA / "phreeqc_io.py"
NIST_DB = _DATA / "NIST_SRD_46_ported.db"

_SECTIONS = frozenset(
    {
        "SOLUTION_MASTER_SPECIES",
        "SOLUTION_SPECIES",
        "PHASES",
        "EXCHANGE_MASTER_SPECIES",
        "EXCHANGE_SPECIES",
        "SURFACE_MASTER_SPECIES",
        "SURFACE_SPECIES",
        "RATES",
        "END",
    }
)
_SALT_TOKENS = {
    "sulfate": re.compile(r"SO4"),
    "chloride": re.compile(r"Cl(?!O)"),
}
_SALT_ALIASES = {
    "sulfate": "sulfate",
    "sulphate": "sulfate",
    "so4": "sulfate",
    "chloride": "chloride",
    "cl": "chloride",
    "cl-": "chloride",
}
_METALS = ("Li", "Ni", "Co", "Mn", "Al", "Fe", "Cu")
_METAL_NOTE = re.compile(r"metal=([A-Za-z]+)")
_OH_PHASES = (
    "Gibbsite",
    "Fe(OH)3(s)",
    "Cu(OH)2(s)",
    "Co(OH)2(s)",
    "Ni(OH)2(s)",
    "Mn(OH)2(s)",
)
_CO3_PHASES = (
    "Ni(CO3):5.5H2O(cr)",
    "Sphaerocobaltite",
    "Rhodochrosite",
    "Li2CO3",
    "Ni(OH)2(s)",
    "Co(OH)2(s)",
    "Mn(OH)2(s)",
    "Cu(OH)2(s)",
)
_DECK_DEFINED = (
    "Fix_pH",
    "Al(OH)3(am)",
    "Fe(OH)3(s)",
    "Cu(OH)2(s)",
    "Co(OH)2(s)",
    "Ni(OH)2(s)",
    "Mn(OH)2(s)",
    "Gibbsite",
)
_LIGAND_ALIASES = {
    "tartrate": "tartaric",
    "tartarate": "tartaric",
    "citrate": "citric",
    "oxalate": "oxalic",
    "acetate": "acetic",
    "glycinate": "glycine",
    "edta": "EDTA",
    "nta": "NTA",
    "ida": "IDA",
}
_PRECIP: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("NH3_NaOH", (r"\bnh3_naoh\b", r"\bammonia\b", r"\bnh3\b")),
    ("NH4HCO3", (r"\bnh4hco3\b", r"\bammonium bicarbonate\b")),
    ("H2O2_NaOH", (r"\bh2o2_naoh\b", r"\bhydrogen peroxide\b", r"\bh2o2\b")),
    ("Na2CO3", (r"\bna2co3\b", r"\bsodium carbonate\b")),
    ("NaOH", (r"\bnaoh\b", r"\bsodium hydroxide\b")),
    ("Na2S", (r"\bna2s\b", r"\bsodium sulfide\b", r"\bsulfide\b")),
    ("Na3PO4", (r"\bna3po4\b", r"\bphosphate\b")),
    ("KMnO4", (r"\bkmno4\b", r"\bpermanganate\b")),
    ("NaClO", (r"\bnaclo\b", r"\bhypochlorite\b")),
    ("Oxalate", (r"\boxalate reagent\b",)),
    ("O2", (r"\boxygen\b",)),
)
LIGANDS: dict[str, dict[str, Any]] = {
    "citric": {"phreeqc_master": "Cit", "phreeqc_species": "Cit-3", "sit_native": True},
    "oxalic": {"phreeqc_master": "Ox", "phreeqc_species": "Ox-2", "sit_native": True},
    "malic": {"phreeqc_master": "Malate", "phreeqc_species": "Malate-2", "sit_native": False},
    "tartaric": {"phreeqc_master": "Tartrate", "phreeqc_species": "Tartrate-2", "sit_native": False},
    "succinic": {"phreeqc_master": "Succinat", "phreeqc_species": "Succinat-2", "sit_native": True},
    "acetic": {"phreeqc_master": "Acetate", "phreeqc_species": "Acetate-", "sit_native": True},
    "lactic": {"phreeqc_master": "Lactate", "phreeqc_species": "Lactate-", "sit_native": False},
    "formic": {"phreeqc_master": "Formate", "phreeqc_species": "Formate-", "sit_native": False},
    "ascorbic": {"phreeqc_master": "Ascorb", "phreeqc_species": "Ascorb-", "sit_native": False},
    "gluconic": {"phreeqc_master": "Glucon", "phreeqc_species": "Glucon-", "sit_native": False},
    "glycine": {"phreeqc_master": "Gly", "phreeqc_species": "Gly-", "sit_native": False},
    "EDTA": {"phreeqc_master": "Edta", "phreeqc_species": "Edta-4", "sit_native": True},
    "NTA": {"phreeqc_master": "Nta", "phreeqc_species": "Nta-3", "sit_native": True},
    "IDA": {"phreeqc_master": "Ida", "phreeqc_species": "Ida-2", "sit_native": False},
    "ammonia": {"phreeqc_master": "N(-3)", "phreeqc_species": "NH3", "sit_native": True},
}


@dataclass(frozen=True)
class _Reagent:
    name: str
    reaction: dict[str, float]
    phases: tuple[str, ...]
    requires: tuple[str, ...]
    source: str
    companion: dict[str, float] = field(default_factory=dict)


REAGENTS: dict[str, _Reagent] = {
    "NaOH": _Reagent(
        name="NaOH",
        reaction={"Na+": 1.0, "OH-": 1.0},
        phases=_OH_PHASES,
        requires=("Na+", "OH-", "Ni(OH)2(s)", "Co(OH)2(s)", "Mn(OH)2(s)"),
        source="functions/ClusterScript black-mass OH screen",
    ),
    "Na2CO3": _Reagent(
        name="Na2CO3",
        reaction={"Na+": 2.0, "CO3-2": 1.0},
        phases=_CO3_PHASES,
        requires=("Na+", "CO3-2", "Ni(CO3):5.5H2O(cr)", "Sphaerocobaltite", "Rhodochrosite", "Li2CO3"),
        source="sources/md/attari2026graphical.md; CarbonateTest stage 2",
    ),
    "NH3_NaOH": _Reagent(
        name="NH3_NaOH",
        reaction={"Na+": 1.0, "OH-": 1.0},
        phases=_OH_PHASES,
        requires=("NH3", "OH-", "Ni(NH3)+2", "Co(NH3)+2", "Mn(NH3)+2"),
        companion={"NH3": 1.0},
        source="sources/md/resendizmora2025modelling.md",
    ),
    "Na2S": _Reagent(
        name="Na2S",
        reaction={"Na+": 2.0, "HS-": 1.0, "OH-": 1.0},
        phases=("Alabandite", "CoS(alpha)", "Millerite"),
        requires=("HS-", "Alabandite", "CoS(alpha)", "Millerite"),
        source="sources/md/lin2025separation.md",
    ),
    "Na3PO4": _Reagent(
        name="Na3PO4",
        reaction={"Na+": 3.0, "PO4-3": 1.0},
        phases=("Co3(PO4)2(s)", "Ni3(PO4)2(cr)"),
        requires=("PO4-3", "Co3(PO4)2(s)", "Ni3(PO4)2(cr)"),
        source="sources/md/lin2025separation.md; Li3PO4 FePO4 Mn phosphate absent",
    ),
    "Oxalate": _Reagent(
        name="Oxalate",
        reaction={"Na+": 2.0, "Ox-2": 1.0},
        phases=("Ni(Ox)(s)", "Co(Ox)(s)", "Mn(Ox)(s)"),
        requires=("Ox-2", "Ni(Ox)(s)", "Co(Ox)(s)", "Mn(Ox)(s)"),
        source="NIST46 has Ox-2; Ni/Co/Mn oxalate solids are not in sit.dat or the overlay",
    ),
    "KMnO4": _Reagent(
        name="KMnO4",
        reaction={"K+": 1.0, "MnO4-": 1.0},
        phases=("MnO2(s)",),
        requires=("K+", "MnO4-", "MnO2(s)"),
        source="sources/md/lin2025separation.md Mn:KMnO4 ~ 1.9 at pH 1.8",
    ),
    "O2": _Reagent(
        name="O2",
        reaction={"O2": 1.0},
        phases=("MnO2(s)", "Manganite", "Hausmannite", "Co3O4(s)", "O2(g)", "Gibbsite", "Fe(OH)3(s)", "Cu(OH)2(s)"),
        requires=("O2", "O2(g)", "MnO2(s)", "Manganite", "Hausmannite", "Co3O4(s)"),
        source="sit.dat native O2, MnO2(s), Manganite, Hausmannite, Co3O4(s); O2(g) at 0 0 caps pO2 at 1 atm",
    ),
    "NaClO": _Reagent(
        name="NaClO",
        reaction={"Na+": 1.0, "ClO-": 1.0},
        phases=("Co3O4(s)",),
        requires=("ClO-", "Co3O4(s)"),
        source="sources/md/lin2025separation.md; sit.dat has no Cl(+1)/ClO-",
    ),
    "NH4HCO3": _Reagent(
        name="NH4HCO3",
        reaction={"NH4+": 1.0, "HCO3-": 1.0},
        phases=_CO3_PHASES,
        requires=("NH4+", "HCO3-", "Ni(CO3):5.5H2O(cr)", "Sphaerocobaltite", "Rhodochrosite", "Li2CO3"),
        source="sources/md/unknown2026controlling.md ammonium bicarbonate",
    ),
    "H2O2_NaOH": _Reagent(
        name="H2O2_NaOH",
        reaction={"Na+": 1.0, "OH-": 1.0},
        phases=("Fe(OH)3(s)",),
        requires=("H2O2", "OH-", "Fe(OH)3(s)"),
        companion={"H2O2": 1.0},
        source="sources/md/lei2026recovering.md; sit.dat has no H2O2",
    ),
}


_FIELDS = (
    ("ligand", "Ligand name."),
    ("salts", "Salts involved. Separate names with commas."),
    ("reagent", "Precipitation reagent."),
    ("metals", "Metal ions. Separate names with commas."),
)


def _ask(question: str) -> str:
    print(question, file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    try:
        return input().strip()
    except EOFError:
        raise SystemExit(2) from None


def _split_list(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,;\s]+", text) if part.strip()]


def _match_ligand(token: str) -> str | None:
    raw = " ".join(token.strip().lower().split())
    if raw in {"none", "no", "no ligand", "noligand"}:
        return "no ligand"
    table = {key.lower(): key for key in LIGANDS}
    if raw in _LIGAND_ALIASES:
        return _LIGAND_ALIASES[raw]
    if raw in table:
        return table[raw]
    if len(raw) >= 5:
        prefixed = [name for low, name in table.items() if low.startswith(raw)]
        if len(prefixed) == 1:
            return prefixed[0]
    return None


def _match_reagent(text: str) -> str | None:
    lowered = text.lower()
    for name, patterns in _PRECIP:
        for pattern in patterns:
            if re.search(pattern, lowered, re.I):
                return name
    return None


def _parse_ligand(text: str, ligands: dict[str, Any]) -> str | None:
    if not text or not text.strip():
        return None
    key = _match_ligand(text)
    if key == "no ligand" or (key is not None and key in ligands):
        return key
    return text.strip()


def _parse_salts(text: str) -> list[str] | None:
    names: list[str] = []
    for token in _split_list(text):
        key = _SALT_ALIASES.get(token.lower())
        if key is None:
            return None
        if key not in names:
            names.append(key)
    return names or None


def _parse_reagent(text: str, reagents: dict[str, Any]) -> str | None:
    found = _match_reagent(text) if text else None
    if found is None and text:
        for name in reagents:
            if text.lower() == name.lower():
                found = name
                break
    if found is None or found not in reagents:
        return None
    return found


def _parse_metals(text: str) -> list[str] | None:
    table = {name.lower(): name for name in _METALS}
    names: list[str] = []
    for token in _split_list(text):
        key = table.get(token.lower())
        if key is None:
            return None
        if key not in names:
            names.append(key)
    return names or None


def _parse_field(field: str, text: str, ligands: dict[str, Any], reagents: dict[str, Any]) -> Any:
    if field == "ligand":
        return _parse_ligand(text, ligands)
    if field == "salts":
        return _parse_salts(text)
    if field == "reagent":
        return _parse_reagent(text, reagents)
    return _parse_metals(text)


def _collect_inputs(
    ligands: dict[str, Any],
    reagents: dict[str, Any],
    given: dict[str, str | None],
) -> dict[str, Any]:
    """Use a flag when it is set. Ask for each missing flag. A bad flag exits."""
    parsed: dict[str, Any] = {}
    for field, question in _FIELDS:
        text = given.get(field)
        if text is not None:
            value = _parse_field(field, text, ligands, reagents)
            if value is None:
                print(f"please provide correct value for {field} field", flush=True)
                raise SystemExit(2)
            parsed[field] = value
            continue
        while True:
            value = _parse_field(field, _ask(question), ligands, reagents)
            if value is not None:
                parsed[field] = value
                break
            print(f"please provide correct value for {field} field", flush=True)
    return parsed


def _mentions_metal(text: str, metal: str) -> bool:
    if metal == "Co":
        return re.search(r"Co(?![3Oa-z])", text) is not None
    return re.search(rf"{re.escape(metal)}(?![a-z])", text) is not None


def _parse_dat(text: str) -> dict[str, list[dict[str, Any]]]:
    section = ""
    species: list[dict[str, Any]] = []
    phases: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = stripped.split()[0]
        if not raw.startswith((" ", "\t")) and head in _SECTIONS and "=" not in stripped:
            section = head
            current = None
            continue
        if section == "SOLUTION_SPECIES":
            if not raw.startswith((" ", "\t")) and "=" in stripped:
                current = {"reaction": stripped, "log_k": None, "delta_h_kJ_mol": None}
                species.append(current)
                continue
        elif section == "PHASES":
            if not raw.startswith((" ", "\t")) and "=" not in stripped:
                current = {
                    "phase": stripped,
                    "reaction": None,
                    "log_k": None,
                    "delta_h_kJ_mol": None,
                }
                phases.append(current)
                continue
            if current is not None and current["reaction"] is None and "=" in stripped:
                current["reaction"] = stripped
                continue
        else:
            continue
        if current is None:
            continue
        low = stripped.lower()
        if low.startswith("log_k"):
            current["log_k"] = float(stripped.split()[1])
        elif low.startswith("delta_h"):
            current["delta_h_kJ_mol"] = float(stripped.split()[1])
    return {"species": species, "phases": phases}


_SIT_CACHE: dict[str, list[dict[str, Any]]] | None = None


def _sit() -> dict[str, list[dict[str, Any]]]:
    global _SIT_CACHE
    if _SIT_CACHE is None:
        _SIT_CACHE = _parse_dat(SIT_DAT.read_text(encoding="utf-8", errors="replace"))
    return _SIT_CACHE


def _row_metal(row: dict[str, Any]) -> str | None:
    metal = row.get("metal")
    if isinstance(metal, str) and metal:
        return metal
    note = str(row.get("note") or "")
    match = _METAL_NOTE.search(note)
    return match.group(1) if match else None


def _keep_ligand_row(row: dict[str, Any], metals: set[str]) -> bool:
    metal = _row_metal(row)
    if metal is None:
        reaction = str(row.get("reaction") or "")
        if reaction and reaction.split("=")[0].strip() == reaction.split("=")[-1].strip():
            return False
        return True
    return metal in metals or metal == "H"


def _sit_ligand_rows(token: str, metals: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _sit()["species"]:
        reaction = row["reaction"]
        if token not in reaction:
            continue
        hit = [metal for metal in metals if _mentions_metal(reaction, metal)]
        cations = re.findall(r"([A-Za-z][A-Za-z0-9()]*)\+\d*", reaction)
        foreign = [
            name
            for name in cations
            if name not in {"H", "H2O"} and not any(_mentions_metal(name, metal) for metal in hit)
        ]
        acid = "H+" in reaction and not hit and not foreign
        if foreign or (not hit and not acid):
            continue
        rows.append(
            {
                "reaction": reaction,
                "log_k": row["log_k"],
                "delta_h_kJ_mol": row["delta_h_kJ_mol"],
                "metal": hit[0] if len(hit) == 1 else (hit or "H"),
                "source": "sit.dat",
            }
        )
    return rows


def _overlay_text() -> tuple[str, Path]:
    if OVERLAY_DAT.is_file():
        return OVERLAY_DAT.read_text(encoding="utf-8"), OVERLAY_DAT
    raise FileNotFoundError(f"nist46_leachant_blocks.dat is missing from {_DATA}")


def _ligand_block(text: str, key: str) -> str | None:
    lines = text.splitlines()
    key_l = key.lower()
    start = None
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped.startswith(f"# --- {key_l}") or stripped.startswith(f"# {key_l}:"):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip().lower()
        if stripped.startswith("# --- ") or re.match(r"# [a-z0-9]+:", stripped):
            end = j
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def _parse_species(block: str) -> list[dict[str, Any]]:
    species: list[dict[str, Any]] = []
    pending = ""
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            pending = stripped.lstrip("#").strip()
            continue
        if stripped.startswith("log_k") and species:
            parts = stripped.split()
            species[-1]["log_k"] = float(parts[1])
            note = species[-1]["note"]
            species[-1]["estimated"] = "estimated" in note.lower()
            species[-1]["at_I_0_1"] = "i=0.1" in note.lower() or "i0.1" in note.lower()
            continue
        if stripped.startswith("-") or stripped.startswith("SOLUTION") or not stripped:
            continue
        if "=" in stripped and not line.startswith(" "):
            species.append(
                {
                    "reaction": stripped,
                    "log_k": None,
                    "note": pending,
                    "estimated": "estimated" in pending.lower(),
                    "at_I_0_1": "i=0.1" in pending.lower(),
                }
            )
            pending = ""
    return species


def _nist_db() -> Path | None:
    if NIST_DB.is_file():
        return NIST_DB
    return None


def _nist_rank(name: str, query: str) -> int | None:
    low = name.lower()
    word = re.compile(rf"\b{re.escape(query)}\b", re.I)
    commons = re.findall(r"\(([^)]*)\)", name)
    if low == query or any(part.strip().lower() == query for part in commons):
        return 0
    if any(word.search(part) for part in commons):
        return 1
    if word.search(name):
        return 2
    return None


def _nist_hits(query: str) -> list[dict[str, Any]]:
    db = _nist_db()
    if db is None:
        return []
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT ligandenID, name_ligand, formula
            FROM liganden
            WHERE instr(lower(name_ligand), ?) > 0
            """,
            (query,),
        ).fetchall()
    finally:
        conn.close()
    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        rank = _nist_rank(str(row["name_ligand"]), query)
        if rank is None:
            continue
        ranked.append(
            (
                rank,
                {
                    "ligand_id": row["ligandenID"],
                    "name_ligand": row["name_ligand"],
                    "formula": row["formula"],
                },
            )
        )
    if not ranked:
        return []
    best = min(rank for rank, _item in ranked)
    return [item for rank, item in ranked if rank == best]


def _nist_constants(ligand_id: int, metals: list[str]) -> list[dict[str, Any]]:
    db = _nist_db()
    if db is None:
        return []
    wanted = set(metals) | {"H"}
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.name_metal_pur AS metal,
                   bd.name_beta_definition AS beta,
                   v.ionicstrength AS ionic,
                   v.constant AS constant
            FROM verkn_ligand_metal AS v
            JOIN metal AS m ON v.metalNr = m.metalID
            JOIN constanttyp AS ct ON v.constanttypNr = ct.constanttypID
            LEFT JOIN beta_definition AS bd ON v.beta_definitionNr = bd.beta_definitionID
            WHERE v.ligandenNr = ?
              AND v.temperature = '25'
              AND ct.constanttypID = 3
            """,
            (ligand_id,),
        ).fetchall()
    finally:
        conn.close()
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        metal = str(row["metal"])
        if metal not in wanted and not any(
            metal.startswith(name) and metal[len(name) :].isdigit() for name in wanted
        ):
            continue
        raw = str(row["constant"] or "").strip()
        estimated = raw.startswith("(") and raw.endswith(")")
        token = raw[1:-1] if estimated else raw
        try:
            value = float(token)
        except ValueError:
            continue
        beta = str(row["beta"] or "")
        groups.setdefault((metal, beta), []).append(
            {
                "log_k": value,
                "ionic": str(row["ionic"] or ""),
                "estimated": estimated,
            }
        )
    found: list[dict[str, Any]] = []
    for (metal, beta), group in groups.items():
        chosen = [item for item in group if item["ionic"] == "0.0"]
        ionic = "0.0"
        if not chosen:
            chosen = [item for item in group if item["ionic"] == "0.1"]
            ionic = "0.1"
        if not chosen:
            continue
        picked = chosen[0]
        found.append(
            {
                "reaction": beta or None,
                "log_k": picked["log_k"],
                "metal": metal,
                "source": "NIST_SRD_46_ported.db",
                "note": f"I={ionic}",
                "at_I_0_1": ionic == "0.1",
                "estimated": picked["estimated"],
            }
        )
    return found


def _nist_ligand(query: str, metals: list[str]) -> dict[str, Any]:
    db = _nist_db()
    if db is None:
        return {
            "ligand": query,
            "source": "NIST_SRD_46_ported.db",
            "species": [],
            "found": False,
            "matches": [],
            "note": "NIST SRD 46 database is missing.",
        }
    hits = _nist_hits(query.lower())
    if not hits:
        return {
            "ligand": query,
            "source": "NIST_SRD_46_ported.db",
            "species": [],
            "found": False,
            "matches": [],
            "note": "No ligand in NIST SRD 46.",
        }
    if len(hits) > 1:
        return {
            "ligand": query,
            "source": "NIST_SRD_46_ported.db",
            "species": [],
            "found": False,
            "matches": hits[:12],
            "note": "More than one NIST ligand matches this name.",
        }
    hit = hits[0]
    rows = _nist_constants(int(hit["ligand_id"]), metals)
    return {
        "ligand": query,
        "nist_name": hit["name_ligand"],
        "ligand_id": hit["ligand_id"],
        "formula": hit["formula"],
        "phreeqc_master": None,
        "phreeqc_species": None,
        "source": "NIST_SRD_46_ported.db",
        "found": True,
        "matches": [hit],
        "at_I_0_1": any(bool(row.get("at_I_0_1")) for row in rows),
        "species": rows,
        "note": None if rows else "NIST SRD 46 has this ligand and no 25 C constant for these metals.",
    }


def _ligand_species(ligand: str, metals: list[str]) -> dict[str, Any]:
    if ligand in {"none", "no ligand"}:
        return {
            "ligand": "no ligand",
            "source": "none",
            "species": [],
            "note": "No ligand. No ligand constants were read.",
        }
    if ligand not in LIGANDS:
        return _nist_ligand(ligand, metals)
    meta = LIGANDS[ligand]
    wanted = set(metals)
    text, overlay_path = _overlay_text()
    block = _ligand_block(text, ligand)
    rows: list[dict[str, Any]] = []
    source: str | None = None
    if block and "SOLUTION_SPECIES" in block:
        rows = [row for row in _parse_species(block) if _keep_ligand_row(row, wanted)]
        source = "nist46_leachant_blocks.dat"
    elif block and "sit.dat native" in block.lower():
        rows = _sit_ligand_rows(str(meta["phreeqc_species"]), metals)
        source = "sit.dat"
    if not rows and meta.get("sit_native"):
        rows = _sit_ligand_rows(str(meta["phreeqc_species"]), metals)
        source = "sit.dat"
    return {
        "ligand": ligand,
        "phreeqc_master": meta["phreeqc_master"],
        "phreeqc_species": meta["phreeqc_species"],
        "source": source,
        "overlay_path": str(overlay_path),
        "at_I_0_1": any(bool(row.get("at_I_0_1")) for row in rows),
        "species": rows,
    }


def _salt_species(salts: list[str], metals: list[str]) -> list[dict[str, Any]]:
    parsed = _sit()
    found: list[dict[str, Any]] = []
    for salt in salts:
        token = _SALT_TOKENS[salt]
        for row in parsed["species"]:
            reaction = row["reaction"]
            if token.search(reaction) is None:
                continue
            hit = [metal for metal in metals if _mentions_metal(reaction, metal)]
            if not hit:
                continue
            found.append(
                {
                    "salt": salt,
                    "kind": "aqueous",
                    "reaction": reaction,
                    "log_k": row["log_k"],
                    "delta_h_kJ_mol": row["delta_h_kJ_mol"],
                    "metals": hit,
                    "source": "sit.dat",
                }
            )
        for row in parsed["phases"]:
            reaction = row["reaction"] or ""
            blob = f"{row['phase']} {reaction}"
            if token.search(blob) is None:
                continue
            hit = [metal for metal in metals if _mentions_metal(blob, metal)]
            if not hit:
                continue
            found.append(
                {
                    "salt": salt,
                    "kind": "phase",
                    "phase": row["phase"],
                    "reaction": row["reaction"],
                    "log_k": row["log_k"],
                    "delta_h_kJ_mol": row["delta_h_kJ_mol"],
                    "metals": hit,
                    "source": "sit.dat",
                }
            )
    return found


def _deck_phases() -> dict[str, dict[str, Any]]:
    """PHASES the screen writes in the deck header. These override sit.dat names."""
    text = PHREEQC_IO.read_text(encoding="utf-8")
    found: dict[str, dict[str, Any]] = {}
    pattern = re.compile(
        r"(?m)^([A-Za-z][A-Za-z0-9_().:+-]*)\r?\n"
        r"[ \t]+(\S.*=.*)\r?\n"
        r"[ \t]+log_k\s+(-?\d+(?:\.\d+)?)"
    )
    for name, reaction, log_k in pattern.findall(text):
        found[name] = {
            "phase": name,
            "reaction": reaction.strip(),
            "log_k": float(log_k),
            "source": "data/phreeqc_io.py",
        }
    return found


def _reagent_thermo(name: str, metals: list[str]) -> dict[str, Any]:
    reagent = REAGENTS[name]
    blob = SIT_DAT.read_text(encoding="utf-8", errors="replace")
    if OVERLAY_DAT.is_file():
        blob += "\n" + OVERLAY_DAT.read_text(encoding="utf-8", errors="replace")
    haystack = blob + "\n" + "\n".join(_DECK_DEFINED)
    missing = [item for item in reagent.requires if item not in haystack]
    enabled = not missing
    phases = {row["phase"]: row for row in _sit()["phases"]}
    deck = _deck_phases()
    phase_rows = []
    for phase in reagent.phases:
        row = phases.get(phase)
        deck_row = deck.get(phase)
        reaction = None if row is None else row["reaction"]
        if deck_row is not None:
            reaction = deck_row["reaction"]
        blob_phase = phase if reaction is None else f"{phase} {reaction}"
        named = [metal for metal in _METALS if _mentions_metal(blob_phase, metal)]
        hit = [metal for metal in named if metal in metals]
        if named and not hit:
            continue
        if deck_row is not None:
            log_k = deck_row["log_k"]
            source = deck_row["source"]
            note = None
        elif row is not None:
            log_k = row["log_k"]
            source = "sit.dat"
            note = None
        else:
            log_k = None
            source = None
            note = "Phase name is not in sit.dat or the deck header."
        item: dict[str, Any] = {
            "phase": phase,
            "reaction": reaction,
            "log_k": log_k,
            "delta_h_kJ_mol": None if row is None else row["delta_h_kJ_mol"],
            "metals": hit,
            "source": source,
            "note": note,
        }
        if row is not None and deck_row is not None and row["log_k"] != deck_row["log_k"]:
            item["sit_log_k"] = row["log_k"]
        phase_rows.append(item)
    return {
        "name": reagent.name,
        "enabled": enabled,
        "missing": missing,
        "reaction": reagent.reaction,
        "companion": reagent.companion,
        "phases": phase_rows,
        "source": reagent.source,
    }


def _metals_in(row: dict[str, Any]) -> list[str]:
    metal = row.get("metal", row.get("metals"))
    if metal is None or metal == "H":
        return []
    if isinstance(metal, str):
        return [metal]
    return [name for name in metal if name != "H"]


def _null_ligand(metal: str) -> dict[str, Any]:
    return {
        "reaction": None,
        "log_k": None,
        "delta_h_kJ_mol": None,
        "metal": metal,
        "source": None,
        "note": "No stored ligand constant for this metal.",
    }


def _null_salt(salt: str, metal: str) -> dict[str, Any]:
    return {
        "salt": salt,
        "kind": None,
        "phase": None,
        "reaction": None,
        "log_k": None,
        "delta_h_kJ_mol": None,
        "metals": [metal],
        "source": None,
        "note": "No stored salt constant for this metal.",
    }


def _null_phase(metal: str) -> dict[str, Any]:
    return {
        "phase": None,
        "reaction": None,
        "log_k": None,
        "delta_h_kJ_mol": None,
        "metals": [metal],
        "source": None,
        "note": "No stored reagent phase for this metal.",
    }


def _fill_gaps(
    ligand_doc: dict[str, Any],
    salt_rows: list[dict[str, Any]],
    reagent_doc: dict[str, Any],
    metals: list[str],
    salts: list[str],
) -> dict[str, Any]:
    unavailable: dict[str, list[Any]] = {"ligand": [], "salts": [], "reagent": []}
    if ligand_doc.get("found") is False:
        unavailable["ligand"].append(ligand_doc.get("note"))
    elif ligand_doc.get("ligand") not in {"none", "no ligand"}:
        found = {name for row in ligand_doc["species"] for name in _metals_in(row) if row.get("log_k") is not None}
        for metal in metals:
            if metal in found:
                continue
            ligand_doc["species"].append(_null_ligand(metal))
            unavailable["ligand"].append(metal)
    for salt in salts:
        for metal in metals:
            found = any(
                row.get("salt") == salt and metal in row.get("metals", []) and row.get("log_k") is not None
                for row in salt_rows
            )
            if found:
                continue
            salt_rows.append(_null_salt(salt, metal))
            unavailable["salts"].append({"salt": salt, "metal": metal})
    covered: set[str] = set()
    for row in reagent_doc["phases"]:
        if row.get("log_k") is None:
            unavailable["reagent"].append(
                {"phase": row.get("phase"), "metals": row.get("metals") or []}
            )
            continue
        covered.update(row.get("metals") or [])
    for metal in metals:
        if metal in covered:
            continue
        if any(metal in (row.get("metals") or []) and row.get("log_k") is None for row in reagent_doc["phases"]):
            continue
        reagent_doc["phases"].append(_null_phase(metal))
        unavailable["reagent"].append({"phase": None, "metals": [metal]})
    for name in reagent_doc.get("missing") or []:
        unavailable["reagent"].append({"missing_name": name})
    return unavailable


def collect(ligand: str, salts: list[str], reagent: str, metals: list[str]) -> dict[str, Any]:
    ligand_doc = _ligand_species(ligand, metals)
    salt_rows = _salt_species(salts, metals)
    reagent_doc = _reagent_thermo(reagent, metals)
    unavailable = _fill_gaps(ligand_doc, salt_rows, reagent_doc, metals, salts)
    return {
        "query": {
            "ligand": ligand,
            "salts": salts,
            "reagent": reagent,
            "metals": metals,
        },
        "ligand": ligand_doc,
        "salts": salt_rows,
        "reagent": reagent_doc,
        "unavailable": unavailable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print thermo JSON for one ligand screen query.")
    parser.add_argument("--ligand", "-l", help="Ligand name. Asked if omitted.")
    parser.add_argument("--salts", "-s", help="Salt names, comma-separated. Asked if omitted.")
    parser.add_argument("--reagent", "-r", help="Precipitation reagent. Asked if omitted.")
    parser.add_argument("--metals", "-m", help="Metal ions, comma-separated. Asked if omitted.")
    args = parser.parse_args(argv)
    answers = _collect_inputs(
        LIGANDS,
        REAGENTS,
        {
            "ligand": args.ligand,
            "salts": args.salts,
            "reagent": args.reagent,
            "metals": args.metals,
        },
    )
    ligand = answers["ligand"]
    salts = answers["salts"]
    reagent = answers["reagent"]
    metals = answers["metals"]
    payload = collect(ligand, salts, reagent, metals)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
