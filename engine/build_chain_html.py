# -*- coding: utf-8 -*-
"""Build one offline HTML page of the AgentLoopDemo_Pipeline step chain for one run.

    python -B build_chain_html.py                      # run from run/latest_run.txt
    python -B build_chain_html.py --dir run/20261012_144554 --out C:/tmp/chain.html

Reads (read only): pipeline.md, RUN/exit_codes.json, RUN/logs, RUN/loops,
RUN/csv, RUN/verdicts, RUN/demo_search. Counts the files in engine/
at run time. Writes one new HTML file only.
The default output is agentloop_chain.html next to the run/ folder.
The script stops if that file exists (use --force).
The page shows one stage at a time. Run properties sit in a header menu.
Side arrows change the loop. Each stage shows thinking, input, and output.
NIST, SIT, and Pitzer share one database-search tab.
The title line states the final conclusion. A short play cycle shows the config
and the result for each loop.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
LEAF = HERE
DEMO = HERE / "engine"

# (key in exit_codes.json, title, kind, file:function, notes)
STEPS = [
    ("hash_audit_before", "Hash audit (before)", "py", "hash_audit.py:main", []),
    ("ask_thermo_nist_csv", "NIST thermo extract (csv)", "py", "ask_thermo_nist.py:collect", ["Reads NIST SRD 46 sqlite db."]),
    ("ask_thermo_nist_json", "NIST thermo extract (json)", "py", "ask_thermo_nist.py:collect", []),
    ("ask_thermo_sit_csv", "SIT thermo extract (csv)", "py", "ask_thermo_sit.py:collect", ["Reads engine/data/sit.dat."]),
    ("ask_thermo_sit_json", "SIT thermo extract (json)", "py", "ask_thermo_sit.py:collect", []),
    ("ask_thermo_pitzer_csv", "Pitzer thermo extract (csv)", "py", "ask_thermo_pitzer.py:collect", ["Reads script/data/pitzer.dat."]),
    ("ask_thermo_pitzer_json", "Pitzer thermo extract (json)", "py", "ask_thermo_pitzer.py:collect", []),
    ("local_paper_search_agent", "Local paper search agent", "ai", "local_paper_search_agent.py:main",
     ["detect: metal-ligand pair in sit.dat (default Ni-glycinate, token Gly).",
      "pdftotext on sources PDFs, regex windows, nomic-embed:latest ranking (AI call only when windows exist).",
      "Rows are PAPER_CANDIDATE_UNVERIFIED. Never fed into .dat, NIST CSV, decks or PHREEQC.",
      "No paper hit: runs web fallback missing_data_search_agent.py:main. Wikipedia is dropped. A local model must accept the page. Kept rows are MODEL_ACCEPTED with a reference. -> demo_search/."]),
    ("run_loop_wrapper", "Config loop (loop 0 + model loops)", "ai", "run_loop_wrapper.py:main / run_loops",
     ["Loop 0: baseline sequence, no model call.",
      "Loop 1..N: openbmb/minicpm5-2b:2b via Ollama /api/chat with format JSON schema (schema_ask_model).",
      "Guard: ask() rejects missing config and duplicates; rcl.apply_changes checks fields.",
      "Reject or sequence fail: error.json, config restored (SHA-256 check), retry up to MAX_FIX = 5."]),
    ("source_fact_checker", "Fact check", "ai", "source_fact_checker.py:main",
     ["Rule checks R1-R7 on the loop proposal. A model verdict is recorded only when the checker calls the model.",
      "pdftotext page quote check. Code rules override the model."]),
    ("compare_loops", "Loop comparison plot", "py", "compare_loops.py:write_plot",
     ["Plot Ni, Co, and Mn pH90 and the pH90 gap for every loop.",
      "Writes image/loop_comparison.png and image/loop_comparison.csv."]),
    ("loop_summary_agent", "Loop summary", "ai", "loop_summary_agent.py:main",
     ["Last agent. Reads every loop pH90, gap, and score.",
      "The model in engine/config.py writes logs/loop_summary.md.",
      "When the model call fails, the same numbers are summarized in code."]),
    ("build_pipeline_md", "Rebuild pipeline.md / index.md", "py", "build_pipeline_md.py", []),
    ("hash_audit_after", "Hash audit (after full run)", "py", "hash_audit.py:main", []),
]

# Sub-steps of each sequence run (run_sequence._sequence), with open items.
SEQ = [
    ("01 feed_panel", "py", "01_feed_panel/feed_panel.py", []),
    ("02 ask_thermo", "py", "02_ask_thermo/ask_thermo.py:collect", []),
    ("03 make_phreeqc_deck", "py", "03_make_phreeqc_deck/make_phreeqc_deck.py:run", []),
    ("04 PHREEQC", "ext", "04_run_phreeqc/run_phreeqc_deck.py:run_deck -> phreeqc.exe 3.8.6",
     [("open", "Database used: sit.dat. Do not mix log K across databases."),
      ("open", "Redox basis: not chosen by Ray."),
      ("open", "Glycine n_H fix: not applied. It needs Ray approval.")]),
    ("05 S01 analyze", "py", "05_S01/S01_ligand_panel_analysis.py:analyze", []),
    ("Score (S05 three cuts)", "py", "s01_cuts.py:three_cuts (via run_loop_wrapper.add_s05_cuts)",
     [("warn", "FACT CHECKER finding: in vl_lib.py the q values carry metal names but are stored in cut order. "
               "A label can show the wrong metal.")]),
]


def read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def pipeline_md_steps(path: Path) -> list[str]:
    if not path.is_file():
        return []
    heads = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^#{2,3}\s+(.+)$", line)
        if m:
            heads.append(m.group(1).strip())
    return heads


def hash_audit(run: Path) -> dict[str, str]:
    """Per-label result. Prefer hash_audit.jsonl; fall back to lines of hash_audit.md."""
    out: dict[str, str] = {}
    jl = run / "logs" / "hash_audit.jsonl"
    if jl.is_file():
        for line in jl.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            label = str(row.get("label", ""))
            bits = [f"{k}={row[k]}" for k in row if k not in ("label", "time", "files", "hashes") and not isinstance(row[k], (dict, list))]
            for k in ("changed", "added", "removed", "files"):
                if isinstance(row.get(k), (list, dict)):
                    bits.append(f"{k}={len(row[k])}")
            out[label] = ", ".join(f"{k}={row[k]}" for k in ("baseline_files", "current_files", "changed", "missing", "new") if k in row)
    md = run / "logs" / "hash_audit.md"
    if not out and md.is_file():
        label = None
        for line in md.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^#{2,3}\s+([A-Za-z0-9_]+)", line)
            if m:
                label = m.group(1)
            elif label and line.strip() and label not in out:
                out[label] = line.strip()
    return out


CLIP = 4500
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".html", ".htm"}


def clip(text: str, limit: int = CLIP) -> str:
    text = str(text).replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n… truncated ({len(text)} characters)"


def read_clip(path: Path, limit: int = CLIP) -> str:
    if not path.is_file():
        return ""
    return clip(path.read_text(encoding="utf-8", errors="replace"), limit)


def text_or(text: str, missing: str = "No text recorded.") -> str:
    text = str(text or "").strip()
    return text or missing


def show_files(paths: list[Path], root: Path) -> str:
    chunks = []
    for path in paths:
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if path.suffix.lower() in SKIP_EXT:
            chunks.append(f"=== {rel} ===\n[file omitted: {path.suffix}]")
            continue
        body = read_clip(path) or "(empty file)"
        chunks.append(f"=== {rel} ===\n{body}")
    return "\n\n".join(chunks)


def files_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file())


def fmt_num(value) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


CONFIG_FIELDS = ("ANION", "LIGAND", "REAGENT", "AMOUNT", "ADD", "STEPS", "STAGE", "DATABASE")


def config_fields(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    found = {}
    for name in CONFIG_FIELDS:
        match = re.search(rf"(?m)^{name}\s*(?::[^=]+)?=\s*(.+)$", text)
        if not match:
            continue
        raw = match.group(1).split("#", 1)[0].strip().rstrip(",")
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
            raw = raw[1:-1]
        found[name] = raw
    return found


def _stage_score(stage: dict) -> float | None:
    """Chosen cut score. Older files store it on the stage. Current files store it under three_cuts_S05."""
    chosen = stage.get("chosen") if isinstance(stage.get("chosen"), dict) else {}
    if isinstance(chosen.get("score"), (int, float)):
        return float(chosen["score"])
    cuts = stage.get("three_cuts_S05")
    if not isinstance(cuts, dict):
        return None
    for grid_name in ("grid_s05", "grid_naoh_ext"):
        grid = cuts.get(grid_name)
        if not isinstance(grid, dict):
            continue
        picked = grid.get("chosen") if isinstance(grid.get("chosen"), dict) else {}
        if isinstance(picked.get("score"), (int, float)):
            return float(picked["score"])
        if isinstance(grid.get("score"), (int, float)):
            return float(grid["score"])
    return None


def _fact_text(fact: dict) -> str:
    verdict = str(fact.get("final_verdict") or "")
    rules = str(fact.get("rule_checks") or "")
    if verdict and rules and rules not in verdict:
        return f"{verdict} ({rules})"
    return verdict or rules


def ph90_metrics(path: Path) -> dict:
    data = read_json(path)
    out = {"ligand": "", "ph": {}, "gap": None, "score": None}
    if not isinstance(data, dict):
        return out
    out["ligand"] = str(data.get("ligand") or "")
    for stage in data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for metal in ("Ni", "Co", "Mn"):
            row = (stage.get("metals") or {}).get(metal)
            if isinstance(row, dict) and isinstance(row.get("pH90"), (int, float)):
                out["ph"][metal] = float(row["pH90"])
        if len(out["ph"]) >= 2:
            out["gap"] = max(out["ph"].values()) - min(out["ph"].values())
        score = _stage_score(stage)
        if score is not None:
            out["score"] = score
        if out["ph"] or out["score"] is not None:
            break
    return out


def _cell(name: str, value: str, changed: bool) -> dict:
    return {"name": name, "value": value, "changed": changed}


def loop_story(run: Path, rows: list[dict], fact_for) -> dict:
    raw = []
    for row in rows:
        loop_id = row.get("loop")
        folder = run / "loops" / f"loop{loop_id}"
        fact = fact_for(loop_id) or {}
        raw.append({
            "loop": loop_id,
            "status": str(row.get("status") or "not recorded"),
            "config": config_fields(folder / "config.py"),
            "metrics": ph90_metrics(folder / "05_S01" / "ph90_95.json"),
            "fact": _fact_text(fact),
            "fe": clip(str(row.get("fe_valence") or ""), 120),
        })
    frames = []
    for i, item in enumerate(raw):
        prev = raw[i - 1] if i else None
        config_rows = []
        names = [name for name in CONFIG_FIELDS if name in item["config"] or (prev and name in prev["config"])]
        for name in names:
            value = item["config"].get(name, "not recorded")
            before = prev["config"].get(name) if prev else value
            config_rows.append(_cell(name, value, bool(prev) and before != value))
        result_rows = []

        def add(name: str, value: str, prev_value: str | None) -> None:
            result_rows.append(_cell(name, value, prev_value is not None and prev_value != value))

        metrics = item["metrics"]
        prev_m = prev["metrics"] if prev else None
        add("status", item["status"], prev["status"] if prev else None)
        add("ligand", metrics["ligand"] or "not recorded", (prev_m["ligand"] or "not recorded") if prev_m else None)
        for metal in ("Ni", "Co", "Mn"):
            value = fmt_num(metrics["ph"][metal]) if metal in metrics["ph"] else "not recorded"
            before = None
            if prev_m is not None:
                before = fmt_num(prev_m["ph"][metal]) if metal in prev_m["ph"] else "not recorded"
            add(f"{metal} pH90", value, before)
        gap = fmt_num(metrics["gap"]) if metrics["gap"] is not None else "not recorded"
        before_gap = None if prev_m is None else (fmt_num(prev_m["gap"]) if prev_m["gap"] is not None else "not recorded")
        add("pH90 gap", gap, before_gap)
        score = fmt_num(metrics["score"]) if metrics["score"] is not None else "not recorded"
        before_score = None if prev_m is None else (fmt_num(prev_m["score"]) if prev_m["score"] is not None else "not recorded")
        add("score", score, before_score)
        if any(entry["fact"] for entry in raw):
            add("fact check", item["fact"] or "not recorded", (prev["fact"] or "not recorded") if prev else None)
        if item["fe"] or (prev and prev["fe"]):
            add("Fe valence", item["fe"] or "not recorded", prev["fe"] if prev else None)
        frames.append({"loop": item["loop"], "config": config_rows, "result": result_rows})
    return {"conclusion": story_conclusion(raw), "frames": frames}


def story_conclusion(raw: list[dict]) -> str:
    if not raw:
        return "No loop was recorded."
    last = raw[-1]
    first = raw[0]
    bits = [f"Final loop is loop {last['loop']} ({last['status']})."]
    if any(item["loop"] == 0 for item in raw):
        bits.append("Loop 0 is UNVERIFIED.")
    changes = []
    for name in CONFIG_FIELDS:
        a = first["config"].get(name)
        b = last["config"].get(name)
        if a is None or b is None or a == b:
            continue
        changes.append(f"{name} {a} to {b}")
    if changes:
        bits.append("Config change: " + "; ".join(changes) + ".")
    else:
        bits.append("The recorded config fields did not change.")
    g0 = first["metrics"]["gap"]
    g1 = last["metrics"]["gap"]
    if g0 is not None and g1 is not None:
        bits.append(f"Ni/Co/Mn pH90 gap {fmt_num(g0)} to {fmt_num(g1)}.")
    scored = [item for item in raw if item["metrics"]["gap"] is not None]
    if scored:
        best = max(scored, key=lambda item: item["metrics"]["gap"])
        mark = " UNVERIFIED" if best["loop"] == 0 else ""
        bits.append(f"Largest gap is loop {best['loop']}{mark} at {fmt_num(best['metrics']['gap'])}.")
    facts = [f"loop {item['loop']} {item['fact']}" for item in raw if item["fact"]]
    if facts:
        bits.append("Fact check: " + ", ".join(facts) + ".")
    return " ".join(bits)


def summarize_ph90(path: Path) -> str:
    data = read_json(path)
    if not isinstance(data, dict):
        return read_clip(path, 2500)
    lines = [f"ligand: {data.get('ligand', '')}", f"file: {path}"]
    for stage in data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        metals = stage.get("metals") or {}
        ph = []
        for name, row in metals.items():
            if isinstance(row, dict) and row.get("pH90") is not None:
                ph.append(f"{name} pH90={fmt_num(row['pH90'])}")
        lines.append(f"stage {stage.get('stage')} {stage.get('reagent')}: " + ", ".join(ph))
        chosen = stage.get("chosen") if isinstance(stage.get("chosen"), dict) else {}
        if not chosen:
            continue
        lines.append(f"  chosen {chosen.get('assignment')} score={fmt_num(chosen.get('score'))}")
        for metal, row in (chosen.get("metals") or {}).items():
            if isinstance(row, dict):
                lines.append(
                    f"  {metal}: cut {row.get('cut')}, purity {fmt_num(row.get('purity'))}, "
                    f"recovery {fmt_num(row.get('recovery'))}, q {fmt_num(row.get('q'))}"
                )
    return "\n".join(lines)


def loop_rows(run: Path, loops: list) -> list[dict]:
    rows = [r for r in loops if isinstance(r, dict) and "loop" in r]
    if rows:
        return rows
    folder = run / "loops"
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("loop*")):
        if path.is_dir() and path.name[4:].isdigit():
            found.append({"loop": int(path.name[4:]), "status": "not recorded"})
    return found


def pane(thinking: str, inp: str, output: str, image: str = "") -> dict:
    row = {
        "thinking": text_or(thinking),
        "input": text_or(inp),
        "output": text_or(output),
    }
    if image:
        row["image"] = image
    return row


def stage_plot(folder: Path) -> Path | None:
    """PNG written by S01_ligand_panel_analysis.write_plot for this loop."""
    data = read_json(folder / "05_S01" / "ph90_95.json")
    if isinstance(data, dict):
        for stage in data.get("stages") or []:
            if isinstance(stage, dict) and stage.get("plot"):
                path = Path(str(stage["plot"]))
                if path.is_file():
                    return path
    image_dir = folder / "05_S01" / "image"
    if image_dir.is_dir():
        found = sorted(image_dir.glob("stage*.png"))
        if found:
            return found[0]
    return None


def image_src(page_dir: Path, path: Path | None) -> str:
    """Path of the PNG relative to the HTML file."""
    if path is None or not path.is_file():
        return ""
    return Path(os.path.relpath(path.resolve(), page_dir.resolve())).as_posix()


def kind_label(kind: str) -> str:
    return {"py": "Python", "ai": "AI call", "ext": "external tool"}.get(kind, kind)


def short_nav(key: str, title: str) -> str:
    names = {
        "hash_audit_before": "Hash before",
        "hash_audit_after": "Hash after",
        "local_paper_search_agent": "Paper search",
        "source_fact_checker": "Fact check",
        "compare_loops": "Compare",
        "loop_summary_agent": "Loop summary",
        "build_pipeline_md": "pipeline.md",
    }
    return names.get(key, title)


def loop_meta(row: dict) -> str:
    bits = [
        f"status={row.get('status')}",
        f"attempts={row.get('attempts', '')}",
        f"Fe valence={row.get('fe_valence', '')}",
    ]
    if row.get("error"):
        bits.append(f"error={row.get('error')}")
    return "\n".join(bits)


SEQ_NAV = {
    "01 feed_panel": "Feed",
    "02 ask_thermo": "Thermo",
    "03 make_phreeqc_deck": "Deck",
    "04 PHREEQC": "PHREEQC",
    "05 S01 analyze": "Analyze",
    "Score (S05 three cuts)": "Score",
}

SEQ_THINK = {
    "01 feed_panel": "Build the black-mass feed table for this loop from the current config.",
    "02 ask_thermo": "Collect thermo rows for the feed and the selected database.",
    "03 make_phreeqc_deck": "Write the PHREEQC deck and the Fe valence record from the thermo table.",
    "04 PHREEQC": "Run phreeqc.exe on the deck and write the stage tables.",
    "05 S01 analyze": "Read the PHREEQC tables and write pH90, pH95, purity, recovery, and q.",
    "Score (S05 three cuts)": "Score three pH cuts. q values in vl_lib.py carry metal names but are stored in cut order. A label can show the wrong metal.",
}


def loop_stages(run: Path, rows: list[dict], exit_code, where: str, page_dir: Path) -> list[dict]:
    stages = []
    config_views = {}
    seq_views = {name: {} for name, *_ in SEQ}
    for row in rows:
        loop_id = row.get("loop")
        folder = run / "loops" / f"loop{loop_id}"
        reason = ""
        if loop_id == 0:
            reason = "Loop 0 runs the baseline sequence. There is no model call. Loop 0 outputs are UNVERIFIED."
        else:
            reason = read_clip(run / "logs" / f"loop{loop_id}_reasoning.log", 2500)
        diff = read_clip(run / "logs" / f"loop{loop_id}_config_diff.txt", 2500)
        prev = row.get("result_in") or "Baseline config. No previous score file."
        config_views[str(loop_id)] = pane(
            reason,
            str(prev),
            text_or(diff, "No config diff.") + "\n\n" + loop_meta(row),
        )
        brief = clip(reason, 700)
        mapping = {
            "01 feed_panel": (files_in(folder / "01_feed_panel"), [folder / "config.py"]),
            "02 ask_thermo": (files_in(folder / "02_ask_thermo"), files_in(folder / "01_feed_panel")),
            "03 make_phreeqc_deck": (files_in(folder / "03_make_phreeqc_deck"), files_in(folder / "02_ask_thermo")),
            "04 PHREEQC": (
                [p for p in files_in(folder / "04_run_phreeqc") if p.suffix.lower() != ".out"]
                + [p for p in files_in(folder / "04_run_phreeqc") if p.suffix.lower() == ".out"][:1],
                [p for p in files_in(folder / "03_make_phreeqc_deck") if p.suffix.lower() == ".phr"],
            ),
            "05 S01 analyze": ([folder / "05_S01" / "ph90_95.json"], [p for p in files_in(folder / "04_run_phreeqc") if p.suffix.lower() == ".csv"]),
        }
        for name, kind, w2, items in SEQ:
            think = SEQ_THINK.get(name, f"Run {w2}.")
            if items:
                think += "\n" + "\n".join(
                    f"{'OPEN' if flag == 'open' else 'WARNING'}: {text}" for flag, text in items
                )
            if brief and name != "Score (S05 three cuts)":
                think += "\n\nModel reason for this loop:\n" + brief
            if name == "Score (S05 three cuts)":
                score_path = folder / "05_S01" / "ph90_95.json"
                seq_views[name][str(loop_id)] = pane(
                    think,
                    summarize_ph90(score_path),
                    loop_meta(row),
                    image_src(page_dir, stage_plot(folder)),
                )
                continue
            if name == "05 S01 analyze":
                outs, ins = mapping[name]
                seq_views[name][str(loop_id)] = pane(
                    think,
                    show_files(ins, run) or "No PHREEQC csv.",
                    summarize_ph90(folder / "05_S01" / "ph90_95.json"),
                )
                continue
            outs, ins = mapping[name]
            seq_views[name][str(loop_id)] = pane(think, show_files(ins, run), show_files(outs, run))
    stages.append({
        "nav": "Config",
        "title": "Config change",
        "kind": "AI call",
        "where": where,
        "exit": exit_code,
        "loop": True,
        "views": config_views,
    })
    for name, kind, w2, _items in SEQ:
        stages.append({
            "nav": SEQ_NAV.get(name, name),
            "title": name,
            "kind": kind_label(kind),
            "where": w2,
            "exit": None,
            "loop": True,
            "views": seq_views[name],
        })
    return stages


def fact_stage(run: Path, rows: list[dict], fact_for, kind: str, where: str, notes, exit_code, audit: str) -> dict:
    views = {}
    note = "\n".join(f"- {line}" for line in notes)
    ids = [r.get("loop") for r in rows] or [None]
    for loop_id in ids:
        doc = fact_for(loop_id) if loop_id is not None else None
        if not doc:
            views[str(loop_id)] = pane(
                note,
                "No proposal for this loop.",
                "No fact-check file for this loop.\n\n" + audit,
            )
            continue
        passages = doc.get("passages") or []
        passage = ""
        if passages and isinstance(passages[0], dict):
            passage = clip(str(passages[0].get("text") or ""), 1200)
        thinking = "\n".join([
            note,
            f"model verdict: {doc.get('model_verdict', '')}",
            f"quote check: {doc.get('quote_check', '')}",
            f"quote: {doc.get('quote', '')}",
            passage,
        ])
        views[str(loop_id)] = pane(
            thinking,
            text_or(str(doc.get("proposal") or doc.get("rule_checks") or ""), "No proposal text."),
            "\n".join([
                f"final: {doc.get('final_verdict', '')}",
                f"rules: {doc.get('rule_checks', '')}",
                f"score: {doc.get('score', '')}",
                f"file: {doc.get('file', '')} page {doc.get('page', '')}",
                audit,
            ]),
        )
    return {
        "nav": "Fact check",
        "title": "Fact check",
        "kind": kind_label(kind),
        "where": where,
        "exit": exit_code,
        "loop": True,
        "views": views,
    }


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent loop chain</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; color: #111; background: #f3f4f6; font: 15px/1.45 "Segoe UI", sans-serif; }
  header, nav, main { padding-left: 28px; padding-right: 28px; }
  header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-height: 56px; border-bottom: 1px solid #e5e7eb; position: sticky; top: 0; background: #fff; z-index: 3; }
  .titleblock { min-width: 0; }
  h1 { margin: 0; font-size: 16px; font-weight: 600; }
  .loopname { color: #555; font-size: 13px; margin-left: 12px; font-weight: 400; }
  details { position: relative; }
  summary { cursor: pointer; list-style: none; border: 1px solid #ddd; padding: 6px 12px; font-size: 13px; }
  summary::-webkit-details-marker { display: none; }
  .menu { position: absolute; right: 0; top: calc(100% + 8px); width: min(420px, 70vw); max-height: 70vh; overflow: auto; background: #fff; border: 1px solid #e6e6e6; padding: 12px 14px; z-index: 4; }
  .menu p { margin: 0 0 10px; }
  .menu b { display: block; font-size: 12px; color: #555; font-weight: 600; }
  .menu span { white-space: pre-wrap; word-break: break-word; }
  nav { display: flex; gap: 8px; overflow-x: auto; border-bottom: 1px solid #e5e7eb; position: sticky; top: var(--head, 56px); background: #f3f4f6; z-index: 2; padding-top: 10px; padding-bottom: 10px; }
  nav button { border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; color: #333; padding: 8px 12px; cursor: pointer; white-space: nowrap; font: inherit; }
  nav button.on { background: #e7eef8; border-color: #c5d4ea; color: #111; }
  main { padding-top: 28px; padding-bottom: 72px; }
  .meta { color: #555; font-size: 13px; margin: 0 0 8px; }
  h2 { margin: 0 0 18px; font-size: 22px; font-weight: 600; }
  .cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; align-items: start; }
  .stack { display: flex; flex-direction: column; gap: 14px; }
  section { margin: 0; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px; }
  section h3 { margin: 0 0 8px; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; color: #666; font-weight: 600; }
  pre { margin: 0; white-space: pre-wrap; word-break: break-word; font: 13px/1.45 Consolas, ui-monospace, monospace; }
  #figure { margin-top: 12px; }
  #figure img { max-width: 100%; height: auto; display: block; }
  .arrow { width: 48px; height: 72px; border: 1px solid #e5e7eb; border-radius: 8px; background: #fff; cursor: pointer; font-size: 22px; color: #111; }
  .arrow:disabled { color: #bbb; cursor: default; }
  .stage-bar { display: grid; grid-template-columns: 48px minmax(0, 1fr) 48px; gap: 12px; align-items: center; margin-bottom: 16px; }
  .stage-bar h2 { margin: 0; }
  .summary { margin: 12px 28px 0; background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; position: static; }
  .summary > summary { list-style: disclosure-triangle; border: 0; padding: 10px 14px; font-weight: 600; }
  .summary > summary::-webkit-details-marker { display: revert; }
  .conclusion { margin: 0 14px 12px; color: #333; font-size: 14px; }
  .story { margin: 0; padding: 0 14px 14px; }
  .story-head { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  #play { border: 1px solid #e5e7eb; background: #fff; border-radius: 8px; padding: 4px 10px; cursor: pointer; font: inherit; }
  .dots { display: flex; gap: 6px; }
  .dot { width: 9px; height: 9px; border: 0; border-radius: 50%; background: #d1d5db; padding: 0; cursor: pointer; }
  .dot.on { background: #1d4ed8; }
  .story-cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
  .story-cols > div { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 14px; }
  .story-cols h3 { margin: 0 0 8px; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; color: #666; font-weight: 600; }
  .kv p { display: flex; justify-content: space-between; gap: 12px; margin: 0; padding: 4px 6px; border-radius: 4px; }
  .kv b { font-weight: 600; }
  .enter { animation: rise .45s ease; }
  .chg { animation: flash 1.2s ease; }
  @keyframes rise { from { opacity: .15; transform: translateY(8px); } to { opacity: 1; transform: none; } }
  @keyframes flash { 0% { background: #fde68a; } 100% { background: transparent; } }
  @media (prefers-reduced-motion: reduce) { .enter, .chg { animation: none; } }
</style>
</head>
<body>
<header id="topbar">
  <div class="titleblock">
    <h1 id="title">Agent loop</h1>
  </div>
  <details>
    <summary>Run details</summary>
    <div class="menu" id="menu"></div>
  </details>
</header>
<details class="summary">
  <summary>Summary</summary>
  <p id="conclusion" class="conclusion"></p>
  <div class="story">
    <div class="story-head">
      <button id="play" type="button">Play</button>
      <div class="dots" id="dots"></div>
      <span id="story-loop"></span>
    </div>
    <div class="story-cols">
      <div><h3>Config</h3><div class="kv" id="config-view"></div></div>
      <div><h3>Result</h3><div class="kv" id="result-view"></div></div>
    </div>
  </div>
</details>
<nav id="nav" aria-label="Stages"></nav>
<main>
  <div class="stage-bar">
    <button class="arrow" id="prev" type="button" aria-label="Previous loop">&#8592;</button>
    <div>
      <p class="meta" id="meta"></p>
      <h2 id="stage"></h2>
    </div>
    <button class="arrow" id="next" type="button" aria-label="Next loop">&#8594;</button>
  </div>
  <div class="cols">
    <section><h3>Thinking</h3><pre id="thinking"></pre></section>
    <div class="stack">
      <section><h3>Input</h3><pre id="input"></pre></section>
      <section><h3>Output</h3><pre id="output"></pre><div id="figure" hidden></div></section>
    </div>
  </div>
</main>
<script>
const DATA = __DATA__;
const stageIndex = { value: 0 };
const loopIndex = { value: 0 };
const title = document.getElementById("title");
const menu = document.getElementById("menu");
const nav = document.getElementById("nav");
const meta = document.getElementById("meta");
const stageEl = document.getElementById("stage");
const thinking = document.getElementById("thinking");
const input = document.getElementById("input");
const output = document.getElementById("output");
const figure = document.getElementById("figure");
const prev = document.getElementById("prev");
const next = document.getElementById("next");

title.textContent = DATA.title;
DATA.props.forEach(function (row) {
  const p = document.createElement("p");
  const b = document.createElement("b");
  const s = document.createElement("span");
  b.textContent = row[0];
  s.textContent = row[1];
  p.appendChild(b);
  p.appendChild(s);
  menu.appendChild(p);
});
DATA.stages.forEach(function (stage, i) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = stage.nav;
  btn.addEventListener("click", function () {
    stageIndex.value = i;
    draw();
  });
  nav.appendChild(btn);
});

function currentLoop() {
  return DATA.loops[loopIndex.value] || null;
}

function paneFor(stage) {
  if (!stage.loop) return stage;
  const loop = currentLoop();
  const key = loop ? String(loop.id) : "";
  return (stage.views && stage.views[key]) || { thinking: "No text recorded.", input: "No text recorded.", output: "No text recorded." };
}

function draw() {
  const stage = DATA.stages[stageIndex.value];
  const pane = paneFor(stage);
  const buttons = nav.querySelectorAll("button");
  buttons.forEach(function (btn, i) { btn.classList.toggle("on", i === stageIndex.value); });
  const loop = currentLoop();
  const loopText = loop ? ("Loop " + loop.id + (loop.note ? " " + loop.note : "") + " · " + loop.status) : "No loops";
  let line = stage.kind + " · " + stage.where;
  if (stage.exit !== null && stage.exit !== undefined) line += " · exit " + stage.exit;
  line += " · " + loopText;
  if (!stage.loop) line += " · same for every loop";
  meta.textContent = line;
  stageEl.textContent = stage.title;
  thinking.textContent = pane.thinking;
  input.textContent = pane.input;
  output.textContent = pane.output;
  figure.replaceChildren();
  if (pane.image) {
    const img = document.createElement("img");
    img.src = pane.image;
    img.alt = "Loop plot";
    figure.hidden = false;
    figure.appendChild(img);
  } else {
    figure.hidden = true;
  }
  const last = Math.max(DATA.loops.length - 1, 0);
  prev.disabled = loopIndex.value <= 0;
  next.disabled = loopIndex.value >= last;
  const mark = title.querySelector(".loopname");
  if (mark) mark.textContent = loopText;
}

const mark = document.createElement("span");
mark.className = "loopname";
title.appendChild(mark);
const conclusion = document.getElementById("conclusion");
const play = document.getElementById("play");
const dots = document.getElementById("dots");
const storyLoop = document.getElementById("story-loop");
const configView = document.getElementById("config-view");
const resultView = document.getElementById("result-view");
const topbar = document.getElementById("topbar");
const story = DATA.story || { conclusion: "", frames: [] };
let storyIndex = 0;
let timer = null;
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
conclusion.textContent = story.conclusion || "No conclusion recorded.";
story.frames.forEach(function (frame, i) {
  const dot = document.createElement("button");
  dot.type = "button";
  dot.className = "dot";
  dot.setAttribute("aria-label", "Loop " + frame.loop);
  dot.addEventListener("click", function () {
    stopPlay();
    const found = DATA.loops.findIndex(function (loop) { return loop.id === frame.loop; });
    if (found >= 0) loopIndex.value = found;
    storyIndex = i;
    draw();
    paintStory();
  });
  dots.appendChild(dot);
});
function fillKv(el, rows) {
  el.replaceChildren();
  (rows || []).forEach(function (row) {
    const p = document.createElement("p");
    if (row.changed) p.className = "chg";
    const b = document.createElement("b");
    const s = document.createElement("span");
    b.textContent = row.name;
    s.textContent = row.value;
    p.appendChild(b);
    p.appendChild(s);
    el.appendChild(p);
  });
}
function pulse(el) {
  el.classList.remove("enter");
  void el.offsetWidth;
  el.classList.add("enter");
}
function paintStory() {
  const frame = story.frames[storyIndex];
  dots.querySelectorAll("button").forEach(function (dot, i) { dot.classList.toggle("on", i === storyIndex); });
  if (!frame) {
    storyLoop.textContent = "No loops";
    return;
  }
  storyLoop.textContent = "Loop " + frame.loop;
  fillKv(configView, frame.config);
  fillKv(resultView, frame.result);
  if (!reduceMotion) { pulse(configView); pulse(resultView); }
}
function stopPlay() {
  if (timer) clearInterval(timer);
  timer = null;
  play.textContent = "Play";
}
function tick() {
  if (!story.frames.length) return;
  storyIndex = (storyIndex + 1) % story.frames.length;
  paintStory();
}
function startPlay() {
  if (story.frames.length < 2 || reduceMotion) return;
  stopPlay();
  play.textContent = "Pause";
  timer = setInterval(tick, 2200);
}
play.addEventListener("click", function () {
  if (timer) stopPlay();
  else startPlay();
});
function pinNav() {
  document.documentElement.style.setProperty("--head", topbar.offsetHeight + "px");
}
window.addEventListener("resize", pinNav);
function shiftLoop(step) {
  const last = Math.max(DATA.loops.length - 1, 0);
  const nextIndex = loopIndex.value + step;
  if (nextIndex < 0 || nextIndex > last) return;
  stopPlay();
  loopIndex.value = nextIndex;
  draw();
  syncStory();
}
prev.addEventListener("click", function () { shiftLoop(-1); });
next.addEventListener("click", function () { shiftLoop(1); });
function syncStory() {
  const loop = currentLoop();
  if (!loop) return;
  const found = story.frames.findIndex(function (frame) { return frame.loop === loop.id; });
  if (found >= 0) storyIndex = found;
  paintStory();
}
document.addEventListener("keydown", function (event) {
  if (event.key === "ArrowLeft") prev.click();
  if (event.key === "ArrowRight") next.click();
});
draw();
pinNav();
paintStory();
if (!reduceMotion && story.frames.length > 1) startPlay();
</script>
</body>
</html>
"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path)
    p.add_argument("--out", type=Path)
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if a.dir is None:
        pointer = LEAF / "run" / "latest_run.txt"
        if not pointer.is_file():
            pointer = LEAF.parent / "run" / "latest_run.txt"
        rel = pointer.read_text(encoding="utf-8").strip()
        a.dir = pointer.parent.parent / rel
    run = a.dir.resolve()
    out = (a.out or LEAF / "agentloop_chain.html").resolve()
    if out.exists() and not a.force:
        print(f"ERROR: {out} exists. Use --force or --out.", file=sys.stderr)
        return 2
    codes = read_json(run / "exit_codes.json") or {}
    loops = (read_json(run / "logs" / "loop_results.json") or {}).get("loops", [])
    audit = hash_audit(run)
    pipeline_md = LEAF / "pipeline.md"
    if not pipeline_md.is_file():
        pipeline_md = LEAF.parent / "pipeline.md"
    heads = pipeline_md_steps(pipeline_md)
    demo_files = [f for f in DEMO.rglob("*") if f.is_file()] if DEMO.is_dir() else []
    demo_src = [f for f in demo_files if not ({"run", "__pycache__"} & set(f.relative_to(DEMO).parts))]
    verdicts = []
    vcsv = run / "verdicts" / "fact_check_verdicts.csv"
    if vcsv.is_file():
        with vcsv.open(encoding="utf-8", errors="replace", newline="") as fh:
            verdicts = list(csv.DictReader(fh))

    rows = loop_rows(run, loops)

    def audit_line(key: str) -> str:
        label = {"hash_audit_before": "before", "hash_audit_after": "after_full_run"}.get(key, f"after_{key}")
        rc_key = key if key in ("hash_audit_before", "hash_audit_after") else f"hash_audit_after_{key}"
        rc = codes.get(rc_key)
        tag = "" if rc is None else (" PASS" if rc == 0 else f" FAIL (exit {rc})")
        detail = audit.get(label, "no entry")
        return f"hash audit [{label}]{tag}: {detail}"

    def notes_text(notes) -> str:
        return "\n".join(f"- {line}" for line in notes)

    def thermo_files(key: str) -> list[Path]:
        base, ext = key.rsplit("_", 1)
        folder = run / "csv"
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.glob(f"{base}*.{ext}") if p.is_file())

    def fact_for(loop_id) -> dict | None:
        doc = read_json(run / "verdicts" / f"loop{loop_id}_fact_check.json")
        if isinstance(doc, list):
            return doc[0] if doc and isinstance(doc[0], dict) else None
        if isinstance(doc, dict):
            return doc
        for row in verdicts:
            if str(row.get("loop")) == str(loop_id):
                return row
        return None

    stages = []
    for key, title, kind, where, notes in STEPS:
        if key == "run_loop_wrapper":
            stages.extend(loop_stages(run, rows, codes.get(key), where, out.parent))
            continue
        if key == "loop_summary_agent":
            summary = read_clip(run / "logs" / "loop_summary.md", 4000) or "No loop summary."
            table = read_clip(run / "image" / "loop_comparison.csv", 2000) or "No comparison table."
            stages.append({
                "nav": short_nav(key, title),
                "title": title,
                "kind": kind_label(kind),
                "where": where,
                "exit": codes.get(key),
                "loop": False,
                **pane(notes_text(notes), table, summary, image_src(out.parent, run / "image" / "loop_comparison.png")),
            })
            continue
        if key == "source_fact_checker":
            stages.append(fact_stage(run, rows, fact_for, kind, where, notes, codes.get(key), audit_line(key)))
            continue
        if key.startswith("ask_thermo_"):
            if any(s.get("nav") == "DB search" for s in stages):
                continue
            think_parts, in_parts, out_parts, exits, wheres = [], [], [], [], []
            for tkey, ttitle, _tkind, twhere, tnotes in STEPS:
                if not tkey.startswith("ask_thermo_"):
                    continue
                rc = codes.get(tkey)
                exits.append(f"{tkey.removeprefix('ask_thermo_')}={rc if rc is not None else 'not recorded'}")
                if twhere not in wheres:
                    wheres.append(twhere)
                think_parts.append(f"{ttitle}\n{notes_text(tnotes) or 'Extract thermo rows for this database.'}")
                log = read_clip(run / "logs" / f"{tkey}.stderr.log", 800)
                in_parts.append(f"{ttitle}\n{log or 'No stderr log.'}")
                produced = show_files(thermo_files(tkey), run) or "No thermo file for this step."
                out_parts.append(f"{ttitle}\n{produced}\n\n{audit_line(tkey)}")
            stages.append({
                "nav": "DB search",
                "title": "Database search (NIST, SIT, Pitzer)",
                "kind": "Python",
                "where": ", ".join(wheres),
                "exit": ", ".join(exits),
                "loop": False,
                **pane("\n\n".join(think_parts), "\n\n".join(in_parts), "\n\n".join(out_parts)),
            })
            continue
        thinking = notes_text(notes) or f"Run {where}."
        log_in = read_clip(run / "logs" / f"{key}.stderr.log", 1500)
        log_out = read_clip(run / "logs" / f"{key}.stdout.log", 2500)
        if key == "local_paper_search_agent":
            search = run / "demo_search"
            produced = show_files([p for p in files_in(search) if p.suffix.lower() not in {".html", ".htm"}], run)
            inp = notes_text(notes)
            output = text_or(produced, "No demo_search files.")
            thinking = text_or(log_out, thinking)
        elif key == "build_pipeline_md":
            inp = "Run folder and the scripts that wrote it."
            output = "pipeline.md headings:\n" + ("\n".join(heads) or "pipeline.md not found")
        elif key.startswith("hash_audit"):
            inp = "Files in the run folder at this label."
            output = audit_line(key)
            thinking = "Compare SHA-256 hashes of the run files with the baseline."
        else:
            inp = text_or(log_in, "No separate input log.")
            output = text_or(log_out, audit_line(key))
        if key in ("hash_audit_before", "hash_audit_after", "build_pipeline_md"):
            extra = audit_line(key)
            if extra not in output:
                output = output + "\n\n" + extra
        stages.append({
            "nav": short_nav(key, title),
            "title": title,
            "kind": kind_label(kind),
            "where": where,
            "exit": codes.get(key),
            "loop": False,
            **pane(thinking, inp, output),
        })

    payload = {
        "title": f"AgentLoopDemo_Pipeline {run.name}",
        "story": loop_story(run, rows, fact_for),
        "loops": [
            {
                "id": r.get("loop"),
                "status": r.get("status") or "not recorded",
                "note": "UNVERIFIED" if r.get("loop") == 0 else "",
            }
            for r in rows
        ],
        "props": [
            ["Run", str(run)],
            ["Overall exit", "not recorded" if codes.get("overall") is None else str(codes.get("overall"))],
            ["Generated", datetime.now().isoformat(timespec="seconds")],
            ["Builder", "script/build_chain_html.py"],
            ["engine files", f"{len(demo_files)} total, {len(demo_src)} outside run/ and __pycache__/"],
            ["Hash audit labels", str(len(audit))],
            ["Stages", str(len(stages))],
            ["Loops", ", ".join(f"loop{r.get('loop')}={r.get('status')}" for r in rows) or "none"],
            ["Legend", "Python script, AI call, external tool"],
            ["pipeline.md", "\n".join(heads) or "pipeline.md not found"],
        ],
        "stages": stages,
    }
    page = PAGE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"))
    out.write_text(page, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
