# -*- coding: utf-8 -*-
"""Economic-loop report.

The page shell is engine/build_chain_html.py, next to this file.
This file fills the header, play cycle, stage tabs, and Thinking / Input /
Output panes with economic-loop data.
"""
from __future__ import annotations

import ast
import csv
import html
import importlib.util
import json
import re
from datetime import datetime
from pathlib import Path

CHAIN = Path(__file__).resolve().parent / "build_chain_html.py"
FIELDS = (
    "TEMP_C",
    "WATER_KG",
    "ANION",
    "BM_ADD",
    "BM_BATCHES",
    "STAGE",
    "REAGENT_1",
    "AMOUNT_1",
    "ADD_1",
    "STEPS_1",
    "REAGENT_2",
    "AMOUNT_2",
    "ADD_2",
    "STEPS_2",
)
SCORE_NAMES = ("net USD", "revenue", "cost", "Ni purity", "Co purity", "Mn purity")
METALS = ("Al", "Fe", "Cu", "Co", "Ni", "Mn", "Li")
# csv mole -> elemental mole for the sulfate feed file. Chloride columns are already elemental.
_SULFATE_ATOMS = {"Al": 2, "Fe": 1, "Cu": 1, "Co": 1, "Ni": 1, "Mn": 1, "Li": 2}
# formula, metal atoms in that formula. Matches the deck PHASES salts.
_SALT_FORMULA = {
    "sulfate": {
        "Al": ("Al2(SO4)3", 2),
        "Fe": ("Fe2(SO4)3", 2),
        "Cu": ("CuSO4", 1),
        "Co": ("CoSO4·7H2O", 1),
        "Ni": ("NiSO4·6H2O", 1),
        "Mn": ("MnSO4·H2O", 1),
        "Li": ("Li2SO4·H2O", 2),
    },
    "chloride": {
        "Al": ("AlCl3·6H2O", 1),
        "Fe": ("FeCl3·6H2O", 1),
        "Cu": ("CuCl2·2H2O", 1),
        "Co": ("CoCl2·6H2O", 1),
        "Ni": ("NiCl2·6H2O", 1),
        "Mn": ("MnCl2·4H2O", 1),
        "Li": ("LiCl·H2O", 1),
    },
}
# name, unit, explanation. Bounds come from the run config, not from this text.
PARAM_META = {
    "TEMP_C": (
        "Temperature",
        "°C",
        "Solution temperature. Hydroxide and carbonate log K move with delta_h. Heat above 25 °C adds an energy cost.",
    ),
    "WATER_KG": (
        "Water",
        "kg",
        "Water in SOLUTION. Salt moles stay absolute, so less water raises molality. The pH-energy penalty scales with this mass.",
    ),
    "ANION": (
        "Anion",
        "",
        "Background salt. Sulfate uses the feed recipe. Chloride keeps the same metal-atom count. Iron is Fe(III) in both decks.",
    ),
    "BM_ADD": (
        "Black-mass feed",
        "",
        "batch puts all black mass in at the start. staged splits it into BM_BATCHES doses. continuous co-feeds it with reagent 1.",
    ),
    "BM_BATCHES": (
        "Black-mass doses",
        "doses",
        "Used when BM_ADD is staged. Batch forces this to 1. Each dose adds the step penalty.",
    ),
    "STAGE": (
        "Stages",
        "",
        "1 adds reagent 1 only. 2 adds all of reagent 1, filters the cake, then adds reagent 2 to the filtrate.",
    ),
    "REAGENT_1": (
        "Reagent 1",
        "",
        "Stage-1 precipitant. The deck can use it only when a solid phase with log K exists for a feed metal.",
    ),
    "AMOUNT_1": (
        "Reagent 1 amount",
        "mol",
        "Total moles of reagent 1 added to the solution. This is not mol/kg.",
    ),
    "ADD_1": (
        "Reagent 1 addition",
        "",
        "once adds the full amount in one step. continuous splits it across STEPS_1.",
    ),
    "STEPS_1": (
        "Reagent 1 steps",
        "steps",
        "Titration steps for reagent 1 when addition is continuous. Each step adds the step penalty.",
    ),
    "REAGENT_2": (
        "Reagent 2",
        "",
        "Stage-2 precipitant, added after the stage-1 solids are removed. Inactive when STAGE is 1.",
    ),
    "AMOUNT_2": (
        "Reagent 2 amount",
        "mol",
        "Total moles of reagent 2. Inactive when STAGE is 1.",
    ),
    "ADD_2": (
        "Reagent 2 addition",
        "",
        "once or continuous, same rule as reagent 1. Inactive when STAGE is 1.",
    ),
    "STEPS_2": (
        "Reagent 2 steps",
        "steps",
        "Titration steps for reagent 2 when addition is continuous. Inactive when STAGE is 1.",
    ),
}
_PARAM_GROUPS = (
    ("Leachate", ("TEMP_C", "WATER_KG", "ANION")),
    ("Black-mass feed", ("BM_ADD", "BM_BATCHES")),
    ("Stage 1", ("REAGENT_1", "AMOUNT_1", "ADD_1", "STEPS_1")),
    ("Stage 2", ("STAGE", "REAGENT_2", "AMOUNT_2", "ADD_2", "STEPS_2")),
)
_STAGE2_FIELDS = ("REAGENT_2", "AMOUNT_2", "ADD_2", "STEPS_2")
_PHASE_NOTES = {
    "Fe(OH)3(s)": "Deck log K 2.78. delta_h is estimated. sit.dat Ferrihydrite has the same log K and no delta_h.",
    "Cu(OH)2(s)": "Deck log K 8.34. sit.dat Cu(OH)2(s) is 8.64. delta_h is from sit.dat.",
    "Co(OH)2(s)": "Deck log K 13.11. delta_h is from sit.dat Co(OH)2(s,rose2).",
    "Ni(OH)2(s)": "Deck log K 13.30. sit.dat Ni(OH)2(s) is 11.03. delta_h is from sit.dat.",
    "Mn(OH)2(s)": "Deck log K 15.28. delta_h is estimated. sit.dat Pyrochroite is 15.3 and has no delta_h.",
    "Li2CO3": "Not in sit.dat. The deck writes log K -2.9 and no delta_h, so this phase stays at its 25 °C value.",
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _guard_reads(chain) -> None:
    """A cloud-only case file must not stop the report."""
    read_clip = getattr(chain, "read_clip", None)
    files_in = getattr(chain, "files_in", None)

    def safe_read(path, limit=4500):
        try:
            return read_clip(path, limit)
        except OSError:
            return "(file could not be read)"

    def safe_files(folder):
        try:
            return files_in(folder)
        except OSError:
            return []

    if callable(read_clip):
        chain.read_clip = safe_read
    if callable(files_in):
        chain.files_in = safe_files


def _chain():
    """Load the AgentLoopDemo page builder.

    OneDrive can leave build_chain_html.py as a cloud placeholder. open()
    then raises OSError 22 and no report is written. The local fallback
    uses the same page shell.
    """
    try:
        return _load_module(CHAIN, "agentloop_chain_html")
    except OSError as exc:
        if exc.errno != 22:
            raise
        fallback = Path(__file__).with_name("chain_fallback.py")
        print(f"CHAIN_HTML unreadable ({exc}). Using {fallback.name}.")
        return _load_module(fallback, "econ_chain_fallback")


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _page_dir(run_dir: Path) -> Path:
    """Folder that holds the report. New runs use agent/. Older runs use the run root."""
    agent = run_dir / "agent"
    if (agent / "run_log.json").is_file() or (agent / "history.jsonl").is_file():
        return agent
    return run_dir


def _rows(run_dir: Path) -> tuple[dict, list[dict]]:
    store = _page_dir(run_dir)
    log = _read(store / "run_log.json")
    if not log:
        log = _read(run_dir / "run_log.json")
    rows = [row for row in (log.get("loops") or []) if isinstance(row, dict) and row.get("status") != "rejected"]
    if rows or log:
        return log, _number_rows(rows)
    history = store / "history.jsonl"
    if not history.is_file():
        history = run_dir / "history.jsonl"
    if not history.is_file():
        return log, []
    loaded = []
    for line in history.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict) and row.get("status") != "rejected":
            loaded.append(row)
    return log, _number_rows(loaded)


def _number_rows(rows: list[dict]) -> list[dict]:
    """Show scored cases as loop 0, 1, 2. History uses a line count, so those ids skip."""
    shown = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["history_loop"] = row.get("loop")
        item["loop"] = index
        shown.append(item)
    return shown


def _case_dir(run_dir: Path, loop_id, last_id) -> Path | None:
    saved = run_dir / "loops" / f"loop{loop_id}"
    if (saved / "05_S01").is_dir() or (saved / "01_feed_panel").is_dir():
        return saved
    for folder in (run_dir / f"loop{loop_id}" / "sim", run_dir / f"loop{loop_id}"):
        if (folder / "05_S01").is_dir() or (folder / "01_feed_panel").is_dir():
            return folder
    if loop_id == last_id and (run_dir / "05_S01").is_dir():
        return run_dir
    return None


def _num(chain, value) -> str:
    if isinstance(value, (int, float)):
        return chain.fmt_num(float(value))
    return "not recorded"


def _purity(row: dict, metal: str):
    metals = row.get("metals") or {}
    item = metals.get(metal) if isinstance(metals, dict) else None
    if isinstance(item, dict):
        return item.get("purity")
    return None


def _score_text(chain, row: dict) -> str:
    costs = row.get("costs") or {}
    lines = [
        f"status: {row.get('status')}",
        f"net_profit_usd: {_num(chain, row.get('net_profit_usd'))}",
        f"revenue_usd: {_num(chain, row.get('revenue_usd'))}",
        f"total_cost_usd: {_num(chain, costs.get('total_cost_usd'))}",
    ]
    if row.get("error"):
        lines.append(f"error: {row.get('error')}")
    metals = row.get("metals") or {}
    order = ("Al", "Fe", "Cu", "Co", "Ni", "Mn", "Li")
    names = [name for name in order if isinstance(metals, dict) and isinstance(metals.get(name), dict)]
    if isinstance(metals, dict):
        names.extend(name for name in metals if name not in names and isinstance(metals.get(name), dict))
    for name in names:
        item = metals[name]
        cut = item.get("pH")
        if cut is None:
            cut = item.get("cut_pH")
        if cut is None:
            where = item.get("source") or "no solid cut"
            cut_text = f"no pH cut ({where})"
        else:
            cut_text = f"cut pH {_num(chain, cut)}"
        target = ""
        if item.get("target_pH") is not None:
            target = f", target pH {_num(chain, item.get('target_pH'))}"
        lines.append(
            f"{name}: {cut_text}{target}, "
            f"purity {_num(chain, item.get('purity'))}, "
            f"recovery {_num(chain, item.get('recovery'))}, usable {item.get('usable')}"
        )
    return "\n".join(lines)


def _money(row: dict, key: str):
    if key == "cost":
        costs = row.get("costs") or {}
        value = costs.get("total_cost_usd") if isinstance(costs, dict) else None
    else:
        value = row.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _best(rows: list[dict]) -> dict | None:
    scored = [row for row in rows if isinstance(row.get("net_profit_usd"), (int, float))]
    if not scored:
        return None
    return max(scored, key=lambda row: row["net_profit_usd"])


def _config_text(config: dict) -> str:
    lines = []
    for name in FIELDS:
        if name in config:
            lines.append(f"{name}: {config[name]}")
    return "\n".join(lines)


def _series(rows: list[dict]) -> list[dict]:
    points = []
    for row in rows:
        revenue = _money(row, "revenue_usd")
        cost = _money(row, "cost")
        net = _money(row, "net_profit_usd")
        if net is None and revenue is not None and cost is not None:
            net = revenue - cost
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        points.append(
            {
                "loop": row.get("loop"),
                "revenue": revenue,
                "cost": cost,
                "net": net,
                "params": {name: config[name] for name in FIELDS if name in config},
            }
        )
    return points


def _story(chain, rows: list[dict], summary: str) -> dict:
    frames = []
    for index, row in enumerate(rows):
        prev = rows[index - 1] if index else None
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        prev_config = prev.get("config") if prev and isinstance(prev.get("config"), dict) else {}
        config_rows = []
        for name in FIELDS:
            if name not in config and name not in prev_config:
                continue
            value = config.get(name, "not recorded")
            before = prev_config.get(name, value) if prev else value
            config_rows.append(chain._cell(name, str(value), bool(prev) and before != value))
        result_values = [
            _num(chain, row.get("net_profit_usd")),
            _num(chain, row.get("revenue_usd")),
            _num(chain, (row.get("costs") or {}).get("total_cost_usd")),
            _num(chain, _purity(row, "Ni")),
            _num(chain, _purity(row, "Co")),
            _num(chain, _purity(row, "Mn")),
        ]
        prev_values = []
        if prev:
            prev_values = [
                _num(chain, prev.get("net_profit_usd")),
                _num(chain, prev.get("revenue_usd")),
                _num(chain, (prev.get("costs") or {}).get("total_cost_usd")),
                _num(chain, _purity(prev, "Ni")),
                _num(chain, _purity(prev, "Co")),
                _num(chain, _purity(prev, "Mn")),
            ]
        result_rows = []
        for name, value in zip(SCORE_NAMES, result_values):
            before = prev_values[SCORE_NAMES.index(name)] if prev_values else None
            result_rows.append(chain._cell(name, value, before is not None and before != value))
        frames.append({"loop": row.get("loop"), "config": config_rows, "result": result_rows})
    ok = [row for row in rows if isinstance(row.get("net_profit_usd"), (int, float))]
    if not ok:
        conclusion = summary or "No scored loop was recorded."
    else:
        best = _best(ok)
        config = best.get("config") if isinstance(best.get("config"), dict) else {}
        conclusion = (
            f"Optimized loop {best.get('loop')} net profit {float(best['net_profit_usd']):.4f} USD. "
            f"Revenue {_num(chain, best.get('revenue_usd'))} USD. "
            f"Cost {_num(chain, _money(best, 'cost'))} USD. "
            + _config_text(config).replace("\n", "; ")
            + " "
            + (summary or "")
        ).strip()
    return {"conclusion": conclusion, "frames": frames}


def _file_stage(chain, run_dir: Path, rows: list[dict], last_id, nav: str, title: str, folder_name: str, think: str) -> dict:
    views = {}
    for row in rows:
        loop_id = row.get("loop")
        case = _case_dir(run_dir, loop_id, last_id)
        if case is None:
            views[str(loop_id)] = chain.pane(
                think,
                "No case files for this loop.",
                "This run keeps one case folder. Files on disk belong to the last loop.",
            )
            continue
        folder = case / folder_name
        views[str(loop_id)] = chain.pane(
            think,
            f"Case folder: {case}",
            chain.show_files(chain.files_in(folder), run_dir) or f"No files in {folder_name}.",
        )
    return {
        "nav": nav,
        "title": title,
        "kind": "Python",
        "where": folder_name,
        "exit": None,
        "loop": True,
        "views": views,
    }


def _speech(payload) -> str:
    if not isinstance(payload, dict):
        return "No text."
    lines = []
    for key in ("claim", "evidence", "attack", "risk"):
        value = payload.get(key)
        if value:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) if lines else "No text."


def _debate_records(run_dir: Path) -> list[dict]:
    """Debates from the run log and from each loop folder, without duplicate lines."""
    import sys

    engine = Path(__file__).resolve().parents[1] / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    import proposals

    seen = set()
    records = []
    for record in proposals._load_debates(run_dir):
        key = json.dumps(record, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def _debate_block(record: dict, index: int) -> str:
    parts = [
        f"Debate {index} after history loop {record.get('loop')} verdict {record.get('verdict')}",
        f"proposer ({record.get('propose_model')})",
        _speech(record.get("proposer")),
        f"adversary ({record.get('adversary_model')})",
        _speech(record.get("adversary")),
    ]
    if any(record.get(name) for name in ("propose_model_2", "adversary_model_2", "proposer_2", "adversary_2")):
        parts.extend(
            [
                f"proposer 2 ({record.get('propose_model_2')})",
                _speech(record.get("proposer_2")),
                f"adversary 2 ({record.get('adversary_model_2')})",
                _speech(record.get("adversary_2")),
            ]
        )
    parts.extend(
        [
            f"verdict ({record.get('verdict_model')})",
            str(record.get("reason") or "No reason."),
        ]
    )
    return "\n".join(parts)


def _same_config(left, right) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)


def _loop_name(row: dict) -> str:
    display = row.get("loop")
    history = row.get("history_loop", display)
    if history == display:
        return f"Loop {display}"
    return f"Loop {display} (history loop {history})"


def _debate_views(chain, run_dir: Path, rows: list[dict], records: list[dict], models_text: str) -> dict:
    """One debate pane per loop.

    The first part is the verdict that chose this loop's config.
    The second part is the verdict recorded after this loop's result.
    """
    views = {}
    for row in rows:
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        history_id = row.get("history_loop", row.get("loop"))
        produced = [
            record
            for record in records
            if _same_config(record.get("candidate"), config)
        ]
        kept = [record for record in produced if record.get("verdict") == "keep"]
        chosen = kept or produced
        produced_keys = {id(record) for record in chosen}
        after = [
            record
            for record in records
            if record.get("loop") == history_id and id(record) not in produced_keys
        ]
        parts = [_loop_name(row)]
        if kept:
            parts.append("Debate that set this config")
            parts.extend(_debate_block(record, index) for index, record in enumerate(kept, 1))
        elif produced:
            parts.append("A debate names this config. The verdict did not keep it.")
            parts.extend(_debate_block(record, index) for index, record in enumerate(produced, 1))
        else:
            parts.append("No debate set this config.")
        if after:
            parts.append("Debate after this result")
            parts.extend(_debate_block(record, index) for index, record in enumerate(after, 1))
        else:
            parts.append("No further debate was recorded after this result.")
        views[str(row.get("loop"))] = chain.pane(
            "This pane is the selected loop. The first debate chose this config. Later debates follow this result.\n"
            + models_text,
            _config_text(config) or "No config recorded.",
            "\n\n".join(parts),
        )
    return views


def _summary_views(chain, rows: list[dict], records: list[dict], history_html: str) -> dict:
    """One summary pane per loop: rank, money, parameters, and the argument for that config."""
    scored = [row for row in rows if isinstance(row.get("net_profit_usd"), (int, float))]
    order = sorted(scored, key=lambda row: (-float(row["net_profit_usd"]), row.get("loop") or 0))
    rank = {row.get("loop"): index for index, row in enumerate(order, 1)}
    views = {}
    for row in rows:
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        loop_id = row.get("loop")
        place = rank.get(loop_id)
        rank_line = (
            f"Rank {place} of {len(scored)} by net profit."
            if place is not None
            else "This loop has no net profit."
        )
        output = "\n".join(
            [
                _loop_name(row),
                rank_line,
                f"Revenue {_usd(row.get('revenue_usd'))}.",
                f"Cost {_usd(_money(row, 'cost'))}.",
                f"Profit {_usd(row.get('net_profit_usd'))}.",
                f"Status {row.get('status') or 'not recorded'}.",
            ]
        )
        profit = row.get("net_profit_usd")
        if isinstance(profit, (int, float)):
            tone = "good" if profit >= 0 else "bad"
        else:
            tone = ""
        output_html = (
            f"<p><b>{_e(_loop_name(row))}.</b> {_e(rank_line)}</p>"
            "<div class=\"cards\">"
            f"<div class=\"card\"><b>Revenue</b><span>{_usd(row.get('revenue_usd'))}</span></div>"
            f"<div class=\"card\"><b>Cost</b><span>{_usd(_money(row, 'cost'))}</span></div>"
            f"<div class=\"card {tone}\"><b>Profit</b><span>{_usd(profit)}</span></div>"
            f"<div class=\"card\"><b>Status</b><span>{_e(row.get('status') or 'not recorded')}</span></div>"
            "</div>"
            + _cost_table(row, config)
            + "<h4 class=\"block\">Parameters</h4>"
            + _config_chip_html(config)
            + "<h4 class=\"block\">Argument for this config</h4>"
            + _argument_html(config, records)
            + _metal_table(row)
            + "<details class=\"fold\"><summary>History top and bottom</summary>"
            + history_html
            + "</details>"
        )
        view = chain.pane(
            f"{_loop_name(row)}. {rank_line}",
            _config_text(config) or "No parameters recorded.",
            output,
        )
        view["input_html"] = _config_chip_html(config) or "<p>No parameters recorded.</p>"
        view["output_html"] = output_html
        views[str(loop_id)] = view
    return views


def _history_summary_text(run_dir: Path, rows: list[dict]) -> str:
    import sys

    engine = Path(__file__).resolve().parents[1] / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    import proposals

    view = proposals.history_view(rows, _page_dir(run_dir))
    if view is None:
        return "History summary waits until HISTORY_SUMMARY_AFTER scored runs."
    lines = [
        f"History summary after {view['after']} scored runs. "
        f"Kept the top {view['each']} and the bottom {view['each']}. Omitted {view['omitted']}."
    ]
    for label, group in (("Top", view["top"]), ("Bottom", view["bottom"])):
        lines.append(label)
        if not group:
            lines.append("None.")
            continue
        for row in group:
            lines.append(
                f"loop {row.get('loop')} rank {row.get('rank')} net {row.get('net_profit_usd')} "
                f"revenue {row.get('revenue_usd')} cost {row.get('total_cost_usd')}"
            )
            lines.append(str(row.get("argument") or "No argument recorded."))
    return "\n".join(lines)


def _case_score(row: dict, case: Path | None) -> dict:
    """Use the case econ_result.json so the page can show each cut pH."""
    shown = dict(row)
    if case is None:
        return shown
    data = _read(case / "05_S01" / "econ_result.json")
    metals = data.get("metals")
    if isinstance(metals, dict) and metals:
        shown["metals"] = metals
    if data.get("purity_threshold") is not None:
        shown["purity_threshold"] = data["purity_threshold"]
    return shown


_BARS = None


def _bars(result: dict, dest: Path) -> None:
    global _BARS
    if _BARS is None:
        import importlib.util

        path = Path(__file__).resolve().parents[1] / "engine" / "05_S01" / "S01_econ_analysis.py"
        spec = importlib.util.spec_from_file_location("econ_s01_bars", path)
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _BARS = module
    _BARS.plot_metal_bars(result, dest)


def _score_images(chain, run_dir: Path, case: Path | None, scored: dict) -> list[str]:
    if case is None:
        return []
    image_dir = case / "05_S01" / "image"
    page_dir = _page_dir(run_dir)
    pics = []
    for name in ("stage1.png", "stage2.png"):
        src = chain.image_src(page_dir, image_dir / name)
        if src:
            pics.append(src)
    if isinstance(scored.get("metals"), dict) and scored["metals"]:
        dest = image_dir / "metals.png"
        _bars(scored, dest)
        src = chain.image_src(page_dir, dest)
        if src:
            pics.append(src)
    return pics


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _show(value) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    return _e(value)


def _usd(value) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    number = float(value)
    sign = "-" if number < 0 else ""
    mag = abs(number)
    if mag and mag < 0.0001:
        return f"{sign}${mag:.2e}"
    return f"{sign}${mag:.4f}"


def _num_or(value, digits: int = 4) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):.{digits}f}"


def _run_consts(run_dir: Path) -> dict:
    """Literal assignments from this run's config. Bounds in the page follow the run."""
    for path in (
        run_dir / "loops" / "loop0" / "config.py",
        run_dir / "config.py",
        run_dir / "loops" / "loop" / "config.py",
    ):
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        out = {}
        for node in tree.body:
            target = None
            value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target = node.targets[0].id
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                target = node.target.id
                value = node.value
            if target is None or value is None:
                continue
            try:
                out[target] = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                continue
        if out:
            return out
    return {}


def _rng(consts: dict, lo_name: str, hi_name: str, lo_default, hi_default, unit: str) -> str:
    lo = consts.get(lo_name, lo_default)
    hi = consts.get(hi_name, hi_default)
    try:
        return f"{float(lo):g} to {float(hi):g} {unit}".strip()
    except (TypeError, ValueError):
        return f"{lo} to {hi} {unit}".strip()


def _param_bounds(consts: dict) -> dict[str, str]:
    amount_hi = consts.get("AMOUNT_MAX", 30)
    steps_hi = consts.get("STEPS_MAX", 200)
    return {
        "TEMP_C": _rng(consts, "TEMP_C_MIN", "TEMP_C_MAX", 15, 85, "°C"),
        "WATER_KG": _rng(consts, "WATER_KG_MIN", "WATER_KG_MAX", 0.05, 20, "kg"),
        "ANION": "sulfate or chloride",
        "BM_ADD": "batch, staged, or continuous",
        "BM_BATCHES": f"1 to {consts.get('BM_BATCHES_MAX', 10)} doses",
        "STAGE": "1 or 2",
        "REAGENT_1": "usable reagent with a solid log K",
        "AMOUNT_1": _rng(consts, "AMOUNT_1_MIN", "AMOUNT_1_MAX", 0.05, amount_hi, "mol"),
        "ADD_1": "once or continuous",
        "STEPS_1": _rng(consts, "STEPS_1_MIN", "STEPS_1_MAX", 1, steps_hi, "steps"),
        "REAGENT_2": "usable reagent with a solid log K",
        "AMOUNT_2": _rng(consts, "AMOUNT_2_MIN", "AMOUNT_2_MAX", 0.05, amount_hi, "mol"),
        "ADD_2": "once or continuous",
        "STEPS_2": _rng(consts, "STEPS_2_MIN", "STEPS_2_MAX", 1, steps_hi, "steps"),
    }


def _price_table(title: str, prices, unit: str) -> str:
    if not isinstance(prices, dict) or not prices:
        return ""
    rows = []
    for name in sorted(prices, key=lambda item: str(item)):
        rows.append(f"<tr><td>{_e(name)}</td><td>{_usd(prices[name]) if isinstance(prices[name], (int, float)) else _show(prices[name])}</td></tr>")
    return (
        f"<h4 class=\"block\">{_e(title)}</h4>"
        "<table class=\"data\"><thead><tr>"
        f"<th scope=\"col\">Name</th><th scope=\"col\">{_e(unit)}</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _param_reference_html(consts: dict) -> str:
    bounds = _param_bounds(consts)
    body = []
    for group, names in _PARAM_GROUPS:
        body.append(f"<h4 class=\"block\">{_e(group)}</h4>")
        body.append(
            "<table class=\"data\"><thead><tr>"
            "<th scope=\"col\">Parameter</th><th scope=\"col\">Bounds</th><th scope=\"col\">Meaning</th>"
            "</tr></thead><tbody>"
        )
        for name in names:
            label, unit, text = PARAM_META[name]
            unit_bit = f" ({unit})" if unit else ""
            body.append(
                "<tr>"
                f"<th scope=\"row\">{_e(label)}{_e(unit_bit)}<br><span class=\"muted\">{_e(name)}</span></th>"
                f"<td>{_e(bounds.get(name, ''))}</td>"
                f"<td>{_e(text)}</td>"
                "</tr>"
            )
        body.append("</tbody></table>")
    gate = consts.get("PURITY_THRESHOLD", 0.70)
    step = consts.get("STEP_PENALTY_USD", 0.01)
    acid = consts.get("NEUTRALIZE_ACID_USD_PER_MOL_H", 0.008)
    ph_pen = consts.get("PH_ENERGY_USD_PER_PH_KG", 0.02)
    filt = consts.get("STAGE2_FILTRATION_FIXED_COST_USD", 0.05)
    heat = consts.get("ENERGY_USD_PER_KJ", 2.8e-5)
    body.append("<h4 class=\"block\">Economic rules for this run</h4>")
    body.append("<table class=\"data\"><tbody>")
    body.append(
        f"<tr><th scope=\"row\">Purity gate</th><td>A cut below {float(gate):.2f} purity has zero revenue.</td></tr>"
        f"<tr><th scope=\"row\">Neutralization</th><td>{_usd(acid)} per mol H+ to bring leftover alkali back to pH 7.</td></tr>"
        f"<tr><th scope=\"row\">Heating</th><td>{_usd(heat)} per kJ above 25 °C. Water heat capacity is 4.184 kJ/kg/K.</td></tr>"
        f"<tr><th scope=\"row\">pH energy</th><td>{_usd(ph_pen)} per pH unit per kg water, from the feed pH to the final pH.</td></tr>"
        f"<tr><th scope=\"row\">Step penalty</th><td>{_usd(step)} for each black-mass dose and each reagent dose.</td></tr>"
        f"<tr><th scope=\"row\">Stage-2 filter</th><td>{_usd(filt)} when STAGE is 2.</td></tr>"
    )
    body.append("</tbody></table>")
    body.append(_price_table("Metal prices", consts.get("METAL_PRICE_USD_PER_KG"), "USD / kg"))
    body.append(_price_table("Reagent prices", consts.get("REAGENT_PRICE_USD_PER_MOL"), "USD / mol"))
    return (
        "<details class=\"param-ref\">"
        "<summary>Parameter and economic reference</summary>"
        + "".join(body)
        + "</details>"
    )


def _stage_is_one(config: dict) -> bool:
    try:
        return int(config.get("STAGE", 1)) == 1
    except (TypeError, ValueError):
        return False


def _config_table_html(config: dict, prev: dict, consts: dict) -> str:
    bounds = _param_bounds(consts)
    dormant = _stage_is_one(config)
    parts = [
        "<table class=\"data\"><thead><tr>",
        "<th scope=\"col\">Parameter</th><th scope=\"col\">Value</th>",
        "<th scope=\"col\">Change</th><th scope=\"col\">Bounds</th><th scope=\"col\">Meaning</th>",
        "</tr></thead><tbody>",
    ]
    for group, names in _PARAM_GROUPS:
        parts.append(f"<tr class=\"group\"><th scope=\"colgroup\" colspan=\"5\">{_e(group)}</th></tr>")
        for name in names:
            label, unit, text = PARAM_META[name]
            value = config.get(name, "—")
            before = prev.get(name, value) if prev else value
            changed = bool(prev) and before != value
            if not prev:
                delta = "seed"
            elif changed:
                delta = f"{_show(before)} → {_show(value)}"
            else:
                delta = "—"
            classes = []
            if dormant and name in _STAGE2_FIELDS:
                classes.append("dim")
            if changed:
                classes.append("on")
            unit_bit = f" {unit}" if unit else ""
            parts.append(
                f"<tr class=\"{' '.join(classes)}\">"
                f"<th scope=\"row\">{_e(label)}</th>"
                f"<td>{_show(value)}{_e(unit_bit)}</td>"
                f"<td>{delta if delta in {'seed', '—'} else delta}</td>"
                f"<td>{_e(bounds.get(name, ''))}</td>"
                f"<td>{_e(text)}</td>"
                "</tr>"
            )
    parts.append("</tbody></table>")
    if dormant:
        parts.append("<p class=\"note\">STAGE is 1. Reagent 2 fields are stored and are not used.</p>")
    return "".join(parts)


def _config_diff_html(config: dict, prev: dict) -> str:
    if not prev:
        return "<p class=\"note\">Loop 0 is the seed. There is no previous scored loop.</p>"
    rows = []
    for name in FIELDS:
        if name not in config and name not in prev:
            continue
        if prev.get(name) == config.get(name):
            continue
        label = PARAM_META.get(name, (name, "", ""))[0]
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{_e(label)}</th>"
            f"<td>{_show(prev.get(name))}</td>"
            f"<td>{_show(config.get(name))}</td>"
            "</tr>"
        )
    if not rows:
        return "<p class=\"note\">No MODIFY field changed from the previous scored loop.</p>"
    return (
        "<table class=\"data\"><caption>Fields that changed</caption><thead><tr>"
        "<th scope=\"col\">Parameter</th><th scope=\"col\">Previous</th><th scope=\"col\">Current</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _config_views(chain, rows: list[dict], consts: dict) -> dict:
    views = {}
    for index, row in enumerate(rows):
        loop_id = row.get("loop")
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        prev = rows[index - 1].get("config") if index and isinstance(rows[index - 1].get("config"), dict) else {}
        changes = [
            f"{name}: {prev.get(name)} -> {config.get(name)}"
            for name in FIELDS
            if prev and prev.get(name) != config.get(name)
        ]
        view = chain.pane(
            "Loop 0 is the seed config. Later loops use a proposal that the verdict kept.",
            _config_text(config) or "No config recorded.",
            "\n".join(changes) or "No MODIFY field changed from the previous scored loop.",
        )
        view["input_html"] = _config_table_html(config, prev if index else {}, consts)
        view["output_html"] = _config_diff_html(config, prev if index else {})
        views[str(loop_id)] = view
    return views


def _csv_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _f(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _elemental_from_csv(row: dict, anion: str) -> dict[str, float]:
    out = {}
    for metal in METALS:
        if anion == "chloride":
            raw = row.get(f"{metal}_chloride_mol")
            number = _f(raw)
            if number is not None:
                out[metal] = number
            continue
        key = "Li_salt_mol" if metal == "Li" else f"{metal}_sulfate_mol"
        number = _f(row.get(key))
        if number is not None:
            out[metal] = number * _SULFATE_ATOMS[metal]
    return out


def _feed_files(case: Path, anion: str) -> tuple[Path, Path]:
    folder = case / "01_feed_panel"
    named = folder / f"blackmass_compositions_{anion}.csv"
    if named.is_file():
        return named, case / "04_run_phreeqc" / "feed_mol.json"
    found = sorted(folder.glob("blackmass_compositions_*.csv")) if folder.is_dir() else []
    return (found[0] if found else named), case / "04_run_phreeqc" / "feed_mol.json"


def _stat_rows(rows: list[dict], anion: str) -> str:
    if len(rows) < 2:
        return ""
    elemental = [_elemental_from_csv(row, anion) for row in rows]
    lines = []
    for metal in METALS:
        vals = [item[metal] for item in elemental if metal in item]
        if not vals:
            continue
        lines.append(
            "<tr>"
            f"<th scope=\"row\">{metal}</th>"
            f"<td>{_num_or(min(vals))}</td><td>{_num_or(sum(vals) / len(vals))}</td><td>{_num_or(max(vals))}</td>"
            "</tr>"
        )
    for label, key in (("NMC mol", "nmc_total_mol"), ("Li / NMC", "li_ratio")):
        vals = [number for number in (_f(row.get(key)) for row in rows) if number is not None]
        if not vals:
            continue
        lines.append(
            "<tr>"
            f"<th scope=\"row\">{label}</th>"
            f"<td>{_num_or(min(vals))}</td><td>{_num_or(sum(vals) / len(vals))}</td><td>{_num_or(max(vals))}</td>"
            "</tr>"
        )
    if not lines:
        return ""
    return (
        f"<h4 class=\"block\">Panel draws ({len(rows)} rows, recipe plus {len(rows) - 1} draws)</h4>"
        "<table class=\"data\"><caption>Min, mean, and max across every panel row. The score uses the recipe row only.</caption><thead><tr>"
        "<th scope=\"col\">Metal</th><th scope=\"col\">Min</th><th scope=\"col\">Mean</th><th scope=\"col\">Max</th>"
        "</tr></thead><tbody>"
        + "".join(lines)
        + "</tbody></table>"
    )


def _feed_html(case: Path, config: dict) -> tuple[str, str, str]:
    anion = str(config.get("ANION") or "sulfate")
    water = _f(config.get("WATER_KG"))
    if water is None or water <= 0:
        water = 1.0
    csv_path, mol_path = _feed_files(case, anion)
    rows = _csv_rows(csv_path)
    feed = _read(mol_path).get("feed_mol") if mol_path.is_file() else None
    if not isinstance(feed, dict):
        feed = _elemental_from_csv(rows[0], anion) if rows else {}
    recipe = rows[0] if rows else {}
    metals = []
    for metal in METALS:
        number = _f(feed.get(metal))
        if number is not None:
            metals.append((metal, number))
    total = sum(amount for _metal, amount in metals)
    nmc = sum(amount for metal, amount in metals if metal in {"Ni", "Co", "Mn"})
    li = next((amount for metal, amount in metals if metal == "Li"), 0.0)
    li_ratio = (li / nmc) if nmc else _f(recipe.get("li_ratio"))
    salt_ref = str(recipe.get("salt_reference") or "")
    formulas = _SALT_FORMULA.get(anion, {})
    input_html = (
        "<table class=\"data\"><tbody>"
        f"<tr><th scope=\"row\">Anion</th><td>{_e(anion)}</td></tr>"
        f"<tr><th scope=\"row\">Water</th><td>{_show(config.get('WATER_KG'))} kg</td></tr>"
        f"<tr><th scope=\"row\">Black-mass feed</th><td>{_show(config.get('BM_ADD'))}, {_show(config.get('BM_BATCHES'))} dose(s)</td></tr>"
        f"<tr><th scope=\"row\">Panel rows</th><td>{len(rows)}</td></tr>"
        f"<tr><th scope=\"row\">Salt reference</th><td>{_e(salt_ref or 'not recorded')}</td></tr>"
        "</tbody></table>"
    )
    cards = (
        "<div class=\"cards\">"
        f"<div class=\"card\"><b>Total metal</b><span>{_num_or(total)} mol</span></div>"
        f"<div class=\"card\"><b>NMC</b><span>{_num_or(nmc)} mol</span></div>"
        f"<div class=\"card\"><b>Li / NMC</b><span>{_num_or(li_ratio) if li_ratio is not None else '—'}</span></div>"
        f"<div class=\"card\"><b>Anion</b><span>{_e(anion)}</span></div>"
        "</div>"
    )
    body = []
    for metal, amount in metals:
        formula, atoms = formulas.get(metal, ("—", 1))
        formula_mol = amount / atoms if atoms else amount
        share = (100.0 * amount / total) if total else 0.0
        body.append(
            "<tr>"
            f"<th scope=\"row\">{metal}</th>"
            f"<td>{_e(formula)}</td>"
            f"<td>{_num_or(formula_mol)}</td>"
            f"<td>{_num_or(amount)}</td>"
            f"<td>{_num_or(share, 1)}%</td>"
            f"<td>{_num_or(amount / water)}</td>"
            "</tr>"
        )
    output_html = (
        "<h4 class=\"block\">Recipe used by PHREEQC (comp_id 1)</h4>"
        + cards
        + "<table class=\"data\"><thead><tr>"
        "<th scope=\"col\">Metal</th><th scope=\"col\">Deck formula</th><th scope=\"col\">Formula mol</th>"
        "<th scope=\"col\">Element mol</th><th scope=\"col\">Share</th><th scope=\"col\">mol/kg water</th>"
        "</tr></thead><tbody>"
        + ("".join(body) or "<tr><td colspan=\"6\">No feed moles recorded.</td></tr>")
        + "</tbody></table>"
        + _stat_rows(rows, anion)
    )
    plain = (
        f"anion {anion}, water {config.get('WATER_KG')} kg, panel rows {len(rows)}\n"
        f"total metal {_num_or(total)} mol, NMC {_num_or(nmc)} mol, Li/NMC {_num_or(li_ratio) if li_ratio is not None else '—'}"
    )
    return input_html, output_html, plain


def _feed_stage(chain, run_dir: Path, rows: list[dict], last_id) -> dict:
    views = {}
    think = (
        "Row 1 is the fixed recipe that PHREEQC dissolves. "
        "Later rows are seeded draws around that recipe. The score uses row 1 only."
    )
    for row in rows:
        loop_id = row.get("loop")
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        case = _case_dir(run_dir, loop_id, last_id)
        if case is None:
            views[str(loop_id)] = chain.pane(think, "No case files for this loop.", "No feed file for this loop.")
            continue
        try:
            input_html, output_html, plain = _feed_html(case, config)
        except OSError:
            views[str(loop_id)] = chain.pane(think, "The feed file could not be read.", "The feed file could not be read.")
            continue
        view = chain.pane(think, f"Case folder: {case}", plain)
        view["input_html"] = input_html
        view["output_html"] = output_html
        views[str(loop_id)] = view
    return {
        "nav": "Feed",
        "title": "01 feed",
        "kind": "Python",
        "where": "01_feed_panel",
        "exit": None,
        "loop": True,
        "views": views,
    }


def _dh(value) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{float(value):.3g}"


def _phase_note(phase: dict) -> str:
    name = str(phase.get("phase") or "")
    bits = []
    if name in _PHASE_NOTES:
        bits.append(_PHASE_NOTES[name])
    sit = phase.get("sit_log_k")
    if isinstance(sit, (int, float)) and phase.get("log_k") not in (None, sit):
        bits.append(f"sit.dat log K is {float(sit):.4g}.")
    source = phase.get("source")
    if source:
        bits.append(str(source))
    note = phase.get("note")
    if note:
        bits.append(str(note))
    return " ".join(bits) or "—"


def _reagent_table(doc: dict, stage: str) -> str:
    reagent = doc.get("reagent") if isinstance(doc.get("reagent"), dict) else {}
    name = str(reagent.get("name") or "—")
    phases = reagent.get("phases") if isinstance(reagent.get("phases"), list) else []
    rows = []
    missing = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        metals = [str(metal) for metal in (phase.get("metals") or [])]
        label = ", ".join(metals) if metals else "—"
        if not phase.get("phase"):
            missing.append(f"{label}: {phase.get('note') or 'no solid phase'}")
            continue
        rows.append(
            "<tr>"
            f"<td>{_e(stage)}</td><td>{_e(name)}</td><td>{_e(label)}</td>"
            f"<td>{_e(phase.get('phase'))}</td><td>{_e(phase.get('reaction') or '—')}</td>"
            f"<td>{_dh(phase.get('log_k')) if not isinstance(phase.get('log_k'), (int, float)) else f'{float(phase.get("log_k")):.4g}'}</td>"
            f"<td>{_dh(phase.get('delta_h_kJ_mol'))}</td>"
            f"<td>{_e(_phase_note(phase))}</td>"
            "</tr>"
        )
    callout = ""
    if missing:
        callout = "<p class=\"note\">" + _e("; ".join(missing)) + "</p>"
    if not rows and not callout:
        return f"<p class=\"note\">No reagent phases recorded for stage { _e(stage) }.</p>"
    table = ""
    if rows:
        table = (
            "<table class=\"data\"><thead><tr>"
            "<th scope=\"col\">Stage</th><th scope=\"col\">Reagent</th><th scope=\"col\">Metal</th>"
            "<th scope=\"col\">Solid</th><th scope=\"col\">Reaction</th><th scope=\"col\">log K</th>"
            "<th scope=\"col\">dH kJ/mol</th><th scope=\"col\">Source</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    return table + callout


def _salt_table(rows: list[dict], kind: str, title: str) -> str:
    body = []
    for row in rows:
        if row.get("kind") != kind or not row.get("reaction"):
            continue
        metals = ", ".join(str(metal) for metal in (row.get("metals") or [])) or "—"
        phase = row.get("phase") or row.get("reaction")
        body.append(
            "<tr>"
            f"<td>{_e(metals)}</td><td>{_e(phase)}</td><td>{_e(row.get('reaction') or '—')}</td>"
            f"<td>{_dh(row.get('log_k')) if not isinstance(row.get('log_k'), (int, float)) else f'{float(row.get("log_k")):.4g}'}</td>"
            f"<td>{_dh(row.get('delta_h_kJ_mol'))}</td><td>{_e(row.get('source') or '—')}</td>"
            "</tr>"
        )
    if not body:
        return ""
    return (
        f"<h4 class=\"block\">{_e(title)}</h4>"
        "<table class=\"data\"><thead><tr>"
        "<th scope=\"col\">Metal</th><th scope=\"col\">Species</th><th scope=\"col\">Reaction</th>"
        "<th scope=\"col\">log K</th><th scope=\"col\">dH kJ/mol</th><th scope=\"col\">Source</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _thermo_html(case: Path, config: dict) -> tuple[str, str, str]:
    folder = case / "02_ask_thermo"
    doc1 = _read(folder / "thermo_1.json")
    doc2 = _read(folder / "thermo_2.json")
    anion = str(config.get("ANION") or (doc1.get("query") or {}).get("salts") or "—")
    metals = (doc1.get("query") or {}).get("metals") or list(METALS)
    reagent_1 = config.get("REAGENT_1") or (doc1.get("reagent") or {}).get("name")
    reagent_2 = config.get("REAGENT_2") if not _stage_is_one(config) else None
    input_html = (
        "<table class=\"data\"><tbody>"
        "<tr><th scope=\"row\">Database</th><td>sit.dat, plus deck hydroxide overrides</td></tr>"
        f"<tr><th scope=\"row\">Anion</th><td>{_e(anion if isinstance(anion, str) else ', '.join(anion))}</td></tr>"
        f"<tr><th scope=\"row\">Stage 1 reagent</th><td>{_show(reagent_1)}</td></tr>"
        f"<tr><th scope=\"row\">Stage 2 reagent</th><td>{_show(reagent_2) if reagent_2 else 'not used'}</td></tr>"
        f"<tr><th scope=\"row\">Metals</th><td>{_e(', '.join(str(metal) for metal in metals))}</td></tr>"
        "</tbody></table>"
    )
    salts = doc1.get("salts") if isinstance(doc1.get("salts"), list) else []
    gaps = []
    for row in salts:
        if isinstance(row, dict) and not row.get("reaction") and row.get("note"):
            metals = ", ".join(str(metal) for metal in (row.get("metals") or [])) or "metal"
            gaps.append(f"{metals}: {row.get('note')}")
    salt_html = _salt_table(salts, "aqueous", "Aqueous complexes") + _salt_table(salts, "phase", "Salt minerals")
    if gaps:
        salt_html += "<p class=\"note\">" + _e("; ".join(gaps)) + "</p>"
    fold = ""
    if salt_html:
        fold = (
            "<details class=\"fold\"><summary>Background salt species and minerals (sit.dat)</summary>"
            + salt_html
            + "</details>"
        )
    output = (
        "<h4 class=\"block\">Precipitation phases</h4>"
        + (_reagent_table(doc1, "1") if doc1 else "<p class=\"note\">No thermo_1.json for this loop.</p>")
        + (_reagent_table(doc2, "2") if doc2 else "")
        + fold
    )
    plain = f"stage 1 {reagent_1}; stage 2 {reagent_2 or 'off'}; anion {anion}"
    return input_html, output, plain


def _thermo_stage(chain, run_dir: Path, rows: list[dict], last_id) -> dict:
    views = {}
    think = (
        "sit.dat supplies the salt complexes and minerals. "
        "The deck overrides hydroxide log K and delta_h so temperature can move those solids. "
        "A metal with no reagent phase stays dissolved."
    )
    for row in rows:
        loop_id = row.get("loop")
        config = row.get("config") if isinstance(row.get("config"), dict) else {}
        case = _case_dir(run_dir, loop_id, last_id)
        if case is None:
            views[str(loop_id)] = chain.pane(think, "No case files for this loop.", "No thermo file for this loop.")
            continue
        try:
            input_html, output_html, plain = _thermo_html(case, config)
        except OSError:
            views[str(loop_id)] = chain.pane(think, "The thermo file could not be read.", "The thermo file could not be read.")
            continue
        view = chain.pane(think, f"Case folder: {case}", plain)
        view["input_html"] = input_html
        view["output_html"] = output_html
        views[str(loop_id)] = view
    return {
        "nav": "Thermo",
        "title": "02 thermo",
        "kind": "Python",
        "where": "02_ask_thermo",
        "exit": None,
        "loop": True,
        "views": views,
    }


def _argument_html(config: dict, records: list[dict]) -> str:
    if not isinstance(config, dict):
        return "<p>No argument recorded.</p>"
    key = json.dumps(config, sort_keys=True, default=str)
    matches = [
        record
        for record in records
        if isinstance(record.get("candidate"), dict) and json.dumps(record["candidate"], sort_keys=True, default=str) == key
    ]
    if not matches:
        return "<p>No argument recorded.</p>"
    kept = [record for record in matches if record.get("verdict") == "keep"]
    record = kept[-1] if kept else matches[-1]
    items = []
    for label, payload, field in (
        ("Proposer", record.get("proposer"), "claim"),
        ("Adversary", record.get("adversary"), "attack"),
        ("Proposer 2", record.get("proposer_2"), "claim"),
        ("Adversary 2", record.get("adversary_2"), "attack"),
    ):
        if isinstance(payload, dict) and payload.get(field):
            items.append(f"<li><b>{label}.</b> {_e(payload.get(field))}</li>")
    if record.get("reason"):
        items.append(f"<li><b>Verdict.</b> {_e(record.get('reason'))}</li>")
    if not items:
        return "<p>No argument recorded.</p>"
    return "<ul class=\"args\">" + "".join(items) + "</ul>"


def _split_argument(text: str) -> str:
    parts = [part.strip() for part in str(text or "").split(" | ") if part.strip()]
    if not parts:
        return "<p>No argument recorded.</p>"
    return "<ul class=\"args\">" + "".join(f"<li>{_e(part)}</li>" for part in parts) + "</ul>"


def _history_summary_html(run_dir: Path, rows: list[dict]) -> str:
    import sys

    engine = Path(__file__).resolve().parents[1] / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    import proposals

    view = proposals.history_view(rows, _page_dir(run_dir))
    if view is None:
        return "<p>History summary waits until HISTORY_SUMMARY_AFTER scored runs.</p>"
    parts = [
        f"<p>After {view['after']} scored runs the models see the top {view['each']} and the bottom {view['each']}. "
        f"Omitted {view['omitted']}.</p>"
    ]
    for label, group in (("Top", view["top"]), ("Bottom", view["bottom"])):
        parts.append(f"<h4 class=\"block\">{label}</h4>")
        if not group:
            parts.append("<p>None.</p>")
            continue
        parts.append(
            "<table class=\"data\"><thead><tr>"
            "<th scope=\"col\">Loop</th><th scope=\"col\">Profit</th><th scope=\"col\">Revenue</th><th scope=\"col\">Cost</th>"
            "</tr></thead><tbody>"
        )
        for row in group:
            parts.append(
                "<tr>"
                f"<td>{_show(row.get('loop'))}</td>"
                f"<td>{_usd(row.get('net_profit_usd'))}</td>"
                f"<td>{_usd(row.get('revenue_usd'))}</td>"
                f"<td>{_usd(row.get('total_cost_usd'))}</td>"
                "</tr>"
            )
            config = row.get("config") if isinstance(row.get("config"), dict) else {}
            parts.append(
                f"<tr><td colspan=\"4\">{_config_chip_html(config)}{_split_argument(row.get('argument'))}</td></tr>"
            )
        parts.append("</tbody></table>")
    return "".join(parts)


def _config_chip_html(config: dict) -> str:
    if not config:
        return ""
    stage2 = not _stage_is_one(config)
    chips = [
        f"{_show(config.get('TEMP_C'))} °C",
        f"{_show(config.get('WATER_KG'))} kg",
        "Cl" if config.get("ANION") == "chloride" else "SO4" if config.get("ANION") == "sulfate" else _show(config.get("ANION")),
        f"{_show(config.get('BM_ADD'))} ({_show(config.get('BM_BATCHES'))})",
        f"S1 {_show(config.get('REAGENT_1'))} {_show(config.get('AMOUNT_1'))} mol / {_show(config.get('STEPS_1'))}",
    ]
    if stage2:
        chips.append(f"S2 {_show(config.get('REAGENT_2'))} {_show(config.get('AMOUNT_2'))} mol / {_show(config.get('STEPS_2'))}")
    else:
        chips.append("Stage 1 only")
    return "<p class=\"chips\">" + "".join(f"<span class=\"chip\">{_e(chip)}</span>" for chip in chips) + "</p>"


def _cost_table(row: dict, config: dict) -> str:
    """Cost lines that add to total_cost_usd, with the sum on the last row."""
    costs = row.get("costs") if isinstance(row.get("costs"), dict) else {}
    reagent_1 = config.get("REAGENT_1") or "reagent 1"
    reagent_2 = config.get("REAGENT_2") or "reagent 2"
    lines = [
        (
            "Reagent",
            costs.get("reagent_cost_usd"),
            f"{_show(costs.get('reagent_1_mol'))} mol {_show(reagent_1)}, {_show(costs.get('reagent_2_mol'))} mol {_show(reagent_2)}",
        ),
        (
            "Neutralization",
            costs.get("neutralize_cost_usd"),
            f"{_show(costs.get('neutralize_eq'))} mol H+ back to pH 7",
        ),
        (
            "Heating",
            costs.get("heat_cost_usd"),
            "Water heated above 25 °C",
        ),
        (
            "pH energy",
            costs.get("ph_energy_cost_usd"),
            f"pH {_show(costs.get('ph_init'))} to {_show(costs.get('ph_final'))}",
        ),
        (
            "Step penalty",
            costs.get("step_penalty_usd"),
            (
                f"{_show(costs.get('n_bm_steps'))} black-mass, "
                f"{_show(costs.get('n_reagent_1_steps'))} reagent 1, "
                f"{_show(costs.get('n_reagent_2_steps'))} reagent 2"
            ),
        ),
        (
            "Stage-2 filter",
            costs.get("filtration_cost_usd"),
            "Cake filter after stage 1" if not _stage_is_one(config) else "Not charged when STAGE is 1",
        ),
    ]
    body = []
    recorded = []
    for label, amount, basis in lines:
        if isinstance(amount, (int, float)):
            recorded.append(float(amount))
        body.append(
            "<tr>"
            f"<th scope=\"row\">{_e(label)}</th>"
            f"<td>{_usd(amount)}</td>"
            f"<td>{_e(basis)}</td>"
            "</tr>"
        )
    total = sum(recorded) if recorded else None
    stored = costs.get("total_cost_usd")
    if isinstance(stored, (int, float)) and total is not None and abs(float(stored) - total) > 5e-5:
        note = f" Recorded total is {_usd(stored)}."
    else:
        note = ""
    body.append(
        "<tr class=\"sum\">"
        "<th scope=\"row\">Sum</th>"
        f"<td>{_usd(total)}</td>"
        f"<td>{_e('Reagent + neutralization + heating + pH energy + step penalty + filter.' + note)}</td>"
        "</tr>"
    )
    return (
        "<h4 class=\"block\">Cost breakdown</h4>"
        "<table class=\"data\"><thead><tr>"
        "<th scope=\"col\">Cost</th><th scope=\"col\">USD</th><th scope=\"col\">Basis</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _metal_table(row: dict) -> str:
    metals = row.get("metals") if isinstance(row.get("metals"), dict) else {}
    if not metals:
        return ""
    body = []
    recorded = []
    order = [name for name in METALS if name in metals]
    order.extend(name for name in metals if name not in order)
    for name in order:
        item = metals.get(name)
        if not isinstance(item, dict):
            continue
        usable = item.get("usable")
        amount = item.get("revenue_usd")
        if isinstance(amount, (int, float)):
            recorded.append(float(amount))
        body.append(
            "<tr>"
            f"<th scope=\"row\">{_e(name)}</th>"
            f"<td>{_num_or(item.get('purity'))}</td>"
            f"<td>{_num_or(item.get('recovery'))}</td>"
            f"<td>{'yes' if usable else 'no'}</td>"
            f"<td>{_usd(amount)}</td>"
            f"<td>{_num_or(item.get('pH'), 2) if isinstance(item.get('pH'), (int, float)) else '—'}</td>"
            "</tr>"
        )
    if not body:
        return ""
    total = sum(recorded) if recorded else None
    stored = row.get("revenue_usd")
    label = "Sum"
    if isinstance(stored, (int, float)) and total is not None and abs(float(stored) - total) > 5e-5:
        label = f"Sum (recorded {_usd(stored)})"
    body.append(
        "<tr class=\"sum\">"
        f"<th scope=\"row\">{_e(label)}</th>"
        "<td>—</td><td>—</td><td>—</td>"
        f"<td>{_usd(total)}</td>"
        "<td>—</td>"
        "</tr>"
    )
    return (
        "<h4 class=\"block\">Metals</h4><table class=\"data\"><thead><tr>"
        "<th scope=\"col\">Metal</th><th scope=\"col\">Purity</th><th scope=\"col\">Recovery</th>"
        "<th scope=\"col\">Usable</th><th scope=\"col\">Revenue</th><th scope=\"col\">pH</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def build(run_dir) -> Path:
    """Write report.html in the run folder and return that path."""
    run_dir = Path(run_dir)
    chain = _chain()
    _guard_reads(chain)
    log, rows = _rows(run_dir)
    if not rows:
        raise SystemExit(f"No scored loops in {run_dir}.")
    best = _best(rows)
    best_id = None if best is None else best.get("loop")
    last_id = rows[-1].get("loop")
    summary = str(log.get("summary") or "")
    stop = log.get("stop_reason")
    if stop:
        summary = (summary + f" Stop: {stop}").strip()
    consts = _run_consts(run_dir)
    config_views = _config_views(chain, rows, consts)
    score_views = {}
    for row in rows:
        loop_id = row.get("loop")
        case = _case_dir(run_dir, loop_id, last_id)
        scored = _case_score(row, case)
        pics = _score_images(chain, run_dir, case, scored)
        view = chain.pane(
            "Score 7 metals. A cut below the purity gate has zero revenue. The objective is net profit.",
            "econ_result.json for this loop. The plots are the stage curves and the purity and recovery bars.",
            _score_text(chain, scored),
            pics[0] if pics else "",
        )
        if pics:
            view["images"] = pics
        score_views[str(loop_id)] = view
    records = _debate_records(run_dir)
    models_text = "\n".join(
        [
            f"proposer {log.get('model')}",
            f"adversary {log.get('adversary_model')}",
            f"proposer 2 {log.get('propose_model_2') or 'off'}",
            f"adversary 2 {log.get('adversary_model_2') or 'off'}",
            f"verdict {log.get('verdict_model')}",
        ]
    )
    history_html = _history_summary_html(run_dir, rows)
    if summary:
        history_html += f"<p>{_e(summary)}</p>"
    stages = [
        {
            "nav": "Config",
            "title": "Config change",
            "kind": "AI call",
            "where": "history.jsonl",
            "exit": None,
            "loop": True,
            "views": config_views,
        },
        _feed_stage(chain, run_dir, rows, last_id),
        _thermo_stage(chain, run_dir, rows, last_id),
        _file_stage(chain, run_dir, rows, last_id, "Deck", "03 deck", "03_make_phreeqc_deck", "Write the PHREEQC deck. There is no ligand."),
        _file_stage(chain, run_dir, rows, last_id, "PHREEQC", "04 PHREEQC", "04_run_phreeqc", "Run phreeqc.exe. Selected-output totals are moles."),
        {
            "nav": "Score",
            "title": "Economic score",
            "kind": "Python",
            "where": "05_S01/econ_result.json",
            "exit": None,
            "loop": True,
            "views": score_views,
        },
        {
            "nav": "Debate",
            "title": "Profit debate",
            "kind": "AI call",
            "where": "debate.jsonl",
            "exit": None,
            "loop": True,
            "views": _debate_views(chain, run_dir, rows, records, models_text),
        },
        {
            "nav": "Summary",
            "title": "Run summary",
            "kind": "Python",
            "where": "run_log.json",
            "exit": None,
            "loop": True,
            "views": _summary_views(chain, rows, records, history_html),
        },
    ]
    series = _series(rows)
    payload = {
        "title": f"EconAgentLoop {run_dir.name}",
        "story": _story(chain, rows, summary),
        "series": series,
        "best_loop": best_id,
        "kpis": _kpis(run_dir, log, rows, best, best_id),
        "loops": [
            {
                "id": row.get("loop"),
                "status": row.get("status") or "not recorded",
                "note": "best" if row.get("loop") == best_id else "",
            }
            for row in rows
        ],
        "props": [
            ["Run", str(run_dir)],
            ["Generated", datetime.now().isoformat(timespec="seconds")],
            ["Builder", "engine/build_chain_html.py"],
            ["Model", str(log.get("model") or "")],
            ["Verdict", str(log.get("verdict_model") or "")],
            ["Optimized loop", str(best_id)],
            ["Optimized net USD", "" if best is None else f"{float(best['net_profit_usd']):.4f}"],
            ["Loops", "\n".join(f"Loop {row.get('loop')}: {row.get('status')}" for row in rows)],
            ["Legend", "Python script, AI call, external tool"],
        ],
        "stages": stages,
    }
    if best is not None:
        record = {
            "loop": best_id,
            "net_profit_usd": best.get("net_profit_usd"),
            "revenue_usd": best.get("revenue_usd"),
            "total_cost_usd": _money(best, "cost"),
            "config": best.get("config") if isinstance(best.get("config"), dict) else {},
        }
        (_page_dir(run_dir) / "optimized.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    page = chain.PAGE.replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"))
    page = page.replace('<details class="summary">', '<details class="summary" open>', 1)
    page = page.replace(
        '<nav id="nav" aria-label="Stages"></nav>',
        '<nav id="loops" aria-label="Loops"></nav>\n<nav id="nav" aria-label="Stages"></nav>',
        1,
    )
    page = page.replace(
        "nav button.on { background: #e7eef8; border-color: #c5d4ea; color: #111; }",
        "nav button.on { background: #e7eef8; border-color: #c5d4ea; color: #111; }\n"
        "  #loops button.on { background: #111; color: #fff; }",
        1,
    )
    page = page.replace(
        "  nav.appendChild(btn);\n});",
        "  nav.appendChild(btn);\n});\n"
        "const loopNav = document.getElementById(\"loops\");\n"
        "DATA.loops.forEach(function (loop, i) {\n"
        "  const btn = document.createElement(\"button\");\n"
        "  btn.type = \"button\";\n"
        "  btn.textContent = \"Loop \" + loop.id + (loop.note === \"best\" ? \" best\" : \"\");\n"
        "  btn.addEventListener(\"click\", function () {\n"
        "    selectTrend(i);\n"
        "  });\n"
        "  loopNav.appendChild(btn);\n"
        "});",
        1,
    )
    page = page.replace(
        "  buttons.forEach(function (btn, i) { btn.classList.toggle(\"on\", i === stageIndex.value); });",
        "  buttons.forEach(function (btn, i) { btn.classList.toggle(\"on\", i === stageIndex.value); });\n"
        "  if (loopNav) loopNav.querySelectorAll(\"button\").forEach(function (btn, i) { btn.classList.toggle(\"on\", i === loopIndex.value); });",
        1,
    )
    page = _add_trend(page)
    page = _show_plots(page)
    page = _apply_shell(page, _param_reference_html(consts))
    path = _page_dir(run_dir) / "report.html"
    path.write_text(page, encoding="utf-8")
    return path


def _kpis(run_dir: Path, log: dict, rows: list[dict], best: dict | None, best_id) -> list[dict]:
    profit = None if best is None else best.get("net_profit_usd")
    tone = ""
    if isinstance(profit, (int, float)):
        tone = "good" if profit >= 0 else "bad"
    best_text = "none" if not isinstance(profit, (int, float)) else f"Loop {best_id}  {_usd(profit)}"
    scored = sum(1 for row in rows if row.get("status") == "ok")
    return [
        {"label": "Run", "value": run_dir.name, "tone": ""},
        {"label": "Best", "value": best_text, "tone": tone},
        {"label": "Scored", "value": str(scored), "tone": ""},
        {"label": "Propose", "value": str(log.get("model") or "—"), "tone": ""},
        {"label": "Verdict", "value": str(log.get("verdict_model") or "—"), "tone": ""},
    ]


def _once(page: str, old: str, new: str, label: str) -> str:
    found = page.count(old)
    if found != 1:
        raise SystemExit(f"Report page anchor missing: {label} ({found})")
    return page.replace(old, new, 1)


def _apply_shell(page: str, reference: str) -> str:
    """Stack Thinking with Input, widen Output, and pin the loop bar above the stage bar."""
    page = _once(
        page,
        """<header id="topbar">
  <div class="titleblock">
    <h1 id="title">Agent loop</h1>
  </div>
  <details>
    <summary>Run details</summary>
    <div class="menu" id="menu"></div>
  </details>
</header>""",
        """<header id="topbar">
  <div class="titleblock">
    <h1 id="title">Agent loop</h1>
    <p class="loopname" id="loopname"></p>
  </div>
  <div class="kpis" id="kpis"></div>
  <details class="run-details">
    <summary>Run details</summary>
    <div class="menu" id="menu"></div>
  </details>
</header>""",
        "header",
    )
    page = _once(
        page,
        """  <div class="cols">
    <section><h3>Thinking</h3><pre id="thinking"></pre></section>
    <div class="stack">
      <section><h3>Input</h3><pre id="input"></pre></section>
      <section><h3>Output</h3><div id="figure" hidden></div><pre id="output"></pre></section>
    </div>
  </div>""",
        """  <div class="cols">
    <div class="stack">
      <section><h3>Thinking</h3><pre id="thinking"></pre></section>
      <section><h3>Input</h3><div id="input-html" hidden></div><pre id="input"></pre></section>
    </div>
    <section class="output-pane"><h3>Output</h3><div id="figure" hidden></div><div id="output-html" hidden></div><pre id="output"></pre></section>
  </div>""",
        "columns",
    )
    page = _once(
        page,
        '<p id="conclusion" class="conclusion"></p>',
        '<p id="conclusion" class="conclusion"></p>\n' + reference,
        "conclusion",
    )
    page = _once(
        page,
        "function draw() {",
        """function showRich(pre, box, text, html) {
  if (!pre) return;
  if (box && html) {
    box.innerHTML = html;
    box.hidden = false;
    pre.hidden = true;
    return;
  }
  if (box) {
    box.replaceChildren();
    box.hidden = true;
  }
  pre.hidden = false;
  pre.textContent = text || "";
}
function draw() {""",
        "draw",
    )
    page = _once(
        page,
        """  thinking.textContent = pane.thinking;
  input.textContent = pane.input;
  output.textContent = pane.output;""",
        """  thinking.textContent = pane.thinking;
  showRich(input, document.getElementById("input-html"), pane.input, pane.input_html);
  showRich(output, document.getElementById("output-html"), pane.output, pane.output_html);""",
        "pane text",
    )
    page = _once(
        page,
        """  const mark = title.querySelector(".loopname");
  if (mark) mark.textContent = loopText;""",
        """  const loopName = document.getElementById("loopname");
  if (loopName) loopName.textContent = loopText;""",
        "loop name",
    )
    page = _once(
        page,
        """const mark = document.createElement("span");
mark.className = "loopname";
title.appendChild(mark);
""",
        "",
        "loop mark",
    )
    page = _once(
        page,
        "title.textContent = DATA.title;",
        """title.textContent = DATA.title;
(function paintKpis() {
  const box = document.getElementById("kpis");
  if (!box) return;
  (DATA.kpis || []).forEach(function (item) {
    const pill = document.createElement("span");
    pill.className = "kpi" + (item.tone ? " " + item.tone : "");
    const label = document.createElement("b");
    label.textContent = item.label;
    pill.appendChild(label);
    pill.appendChild(document.createTextNode(item.value));
    box.appendChild(pill);
  });
})();""",
        "kpis",
    )
    page = _once(
        page,
        """function pinNav() {
  document.documentElement.style.setProperty("--head", topbar.offsetHeight + "px");
}""",
        """function pinNav() {
  const head = topbar ? topbar.offsetHeight : 56;
  document.documentElement.style.setProperty("--head", head + "px");
  const loops = document.getElementById("loops");
  document.documentElement.style.setProperty("--loops-h", (loops ? loops.offsetHeight : 0) + "px");
}""",
        "pin",
    )
    style = """
  header { flex-wrap: wrap; align-items: center; }
  .titleblock { display: flex; flex-direction: column; gap: 2px; }
  #loopname { margin: 2px 0 0; display: block; color: #555; font-size: 13px; font-weight: 600; }
  .kpis { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; flex: 1 1 280px; }
  .kpi { border: 1px solid #e5e7eb; background: #f8fafc; border-radius: 999px; padding: 4px 10px; font-size: 12px; }
  .kpi b { color: #555; font-weight: 600; margin-right: 4px; }
  .kpi.good { background: #ecfdf3; border-color: #bbf7d0; color: #166534; }
  .kpi.bad { background: #fef2f2; border-color: #fecaca; color: #991b1b; }
  #loops { top: var(--head, 56px); z-index: 2; background: #f3f4f6; }
  #nav { top: calc(var(--head, 56px) + var(--loops-h, 52px)); }
  .cols { grid-template-columns: minmax(260px, 1fr) minmax(0, 2fr); }
  #input-html, #output-html { overflow-x: auto; }
  .data { width: 100%; border-collapse: collapse; margin: 0 0 12px; font-size: 13px; }
  .data th, .data td { border-bottom: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; vertical-align: top; }
  .data th { font-size: 11px; letter-spacing: .03em; text-transform: uppercase; color: #666; font-weight: 600; }
  .data caption { caption-side: top; text-align: left; font-weight: 600; margin: 0 0 6px; }
  .data tr.dim td, .data tr.dim th { color: #888; }
  .data tr.on td, .data tr.on th { background: #e7eef8; }
  .data tr.group th, .data tr.group td, #param-table tr.group td { background: #f8fafc; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: #666; font-weight: 700; }
  .data tr.sum th, .data tr.sum td { border-top: 2px solid #111; font-weight: 700; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 0 0 12px; }
  .card { border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px 10px; background: #f8fafc; }
  .card b { display: block; font-size: 11px; letter-spacing: .04em; text-transform: uppercase; color: #666; font-weight: 600; }
  .card span { font-size: 16px; font-weight: 650; }
  .card.good span, td.good, .good { color: #166534; }
  .card.bad span, td.bad, .bad { color: #991b1b; }
  .note { margin: 0 0 12px; padding: 8px 10px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; }
  h4.block { margin: 14px 0 6px; font-size: 13px; }
  details.param-ref, details.fold { position: static; margin: 0 14px 12px; }
  details.fold { margin: 8px 0; border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px 10px; background: #fff; }
  details.param-ref > summary, details.fold > summary { list-style: disclosure-triangle; border: 0; padding: 4px 0; font-weight: 600; }
  details.param-ref > summary::-webkit-details-marker, details.fold > summary::-webkit-details-marker { display: revert; }
  .muted { color: #666; font-weight: 400; font-size: 11px; text-transform: none; letter-spacing: 0; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; margin: 0; }
  .chip { display: inline-block; background: #f3f4f6; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 400; letter-spacing: 0; text-transform: none; color: #111; }
  .chip-on { background: #fef3c7; font-weight: 700; }
  ul.args { margin: 6px 0 0; padding-left: 18px; }
  ul.args li { margin: 0 0 6px; }
  @media (max-width: 900px) {
    .cols, .story-cols { grid-template-columns: 1fr; }
    .kpis { justify-content: flex-start; }
    .story-head { flex-wrap: wrap; }
  }
"""
    if page.count("</style>") != 1:
        raise SystemExit("Report page has no style block.")
    return page.replace("</style>", style + "</style>", 1)


def _show_plots(page: str) -> str:
    """Show every S01 image above the score text. The chain page stores one image."""
    page = page.replace(
        '<pre id="output"></pre><div id="figure" hidden></div>',
        '<div id="figure" hidden></div><pre id="output"></pre>',
        1,
    )
    if "pane.images" in page:
        return page
    old = (
        "  figure.replaceChildren();\n"
        "  if (pane.image) {\n"
        "    const img = document.createElement(\"img\");\n"
        "    img.src = pane.image;\n"
        "    img.alt = \"Loop plot\";\n"
        "    figure.hidden = false;\n"
        "    figure.appendChild(img);\n"
        "  } else {\n"
        "    figure.hidden = true;\n"
        "  }"
    )
    new = (
        "  figure.replaceChildren();\n"
        "  const sources = pane.images || (pane.image ? [pane.image] : []);\n"
        "  sources.forEach(function (src) {\n"
        "    const img = document.createElement(\"img\");\n"
        "    img.src = src;\n"
        "    img.alt = \"Loop plot\";\n"
        "    figure.appendChild(img);\n"
        "  });\n"
        "  figure.hidden = sources.length === 0;"
    )
    if old not in page:
        raise SystemExit("Report page has no single-image plot block.")
    return page.replace(old, new, 1)


def _add_trend(page: str) -> str:
    """Draw revenue, cost, and profit, with a loop slider and the best and worst loops."""
    old_shift = (
        "function shiftLoop(step) {\n"
        "  const last = Math.max(DATA.loops.length - 1, 0);\n"
        "  const nextIndex = loopIndex.value + step;\n"
        "  if (nextIndex < 0 || nextIndex > last) return;\n"
        "  stopPlay();\n"
        "  loopIndex.value = nextIndex;\n"
        "  draw();\n"
        "  syncStory();\n"
        "}"
    )
    new_shift = (
        "function shiftLoop(step) {\n"
        "  selectTrend(loopIndex.value + step);\n"
        "}"
    )
    if page.count(old_shift) != 1:
        raise SystemExit("Report page has no loop step control.")
    page = page.replace(old_shift, new_shift, 1)
    old_dot = (
        "  dot.addEventListener(\"click\", function () {\n"
        "    stopPlay();\n"
        "    const found = DATA.loops.findIndex(function (loop) { return loop.id === frame.loop; });\n"
        "    if (found >= 0) loopIndex.value = found;\n"
        "    storyIndex = i;\n"
        "    draw();\n"
        "    paintStory();\n"
        "  });"
    )
    new_dot = (
        "  dot.addEventListener(\"click\", function () {\n"
        "    const found = DATA.loops.findIndex(function (loop) { return loop.id === frame.loop; });\n"
        "    if (found >= 0) selectTrend(found);\n"
        "  });"
    )
    if page.count(old_dot) != 1:
        raise SystemExit("Report page has no story-dot loop control.")
    page = page.replace(old_dot, new_dot, 1)
    old_tick = (
        "function tick() {\n"
        "  if (!story.frames.length) return;\n"
        "  storyIndex = (storyIndex + 1) % story.frames.length;\n"
        "  paintStory();\n"
        "}"
    )
    new_tick = (
        "function tick() {\n"
        "  if (!DATA.loops.length) return;\n"
        "  const last = DATA.loops.length - 1;\n"
        "  const next = loopIndex.value >= last ? 0 : loopIndex.value + 1;\n"
        "  selectTrend(next, true);\n"
        "}"
    )
    if page.count(old_tick) != 1:
        raise SystemExit("Report page has no story tick.")
    page = page.replace(old_tick, new_tick, 1)
    old_keys = (
        "document.addEventListener(\"keydown\", function (event) {\n"
        "  if (event.key === \"ArrowLeft\") prev.click();\n"
        "  if (event.key === \"ArrowRight\") next.click();\n"
        "});"
    )
    new_keys = (
        "document.addEventListener(\"keydown\", function (event) {\n"
        "  if (event.target && event.target.id === \"loop-slider\") return;\n"
        "  if (event.key === \"ArrowLeft\") prev.click();\n"
        "  if (event.key === \"ArrowRight\") next.click();\n"
        "});"
    )
    if page.count(old_keys) != 1:
        raise SystemExit("Report page has no loop arrow keys.")
    page = page.replace(old_keys, new_keys, 1)
    style = (
        "  .trend-row { display: flex; align-items: flex-start; gap: 16px; margin: 0 28px 16px; }\n"
        "  #trend { flex: 1 1 auto; width: auto; min-width: 0; height: auto; aspect-ratio: 720 / 444; margin: 0; "
        "background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; display: block; cursor: pointer; }\n"
        "  .trend-title { margin: 4px 28px 8px; font-size: 12px; letter-spacing: .04em; "
        "text-transform: uppercase; color: #666; font-weight: 600; }\n"
        "  .trend-slider { display: flex; align-items: center; gap: 12px; margin: 0 28px 8px; "
        "font-size: 14px; font-weight: 600; }\n"
        "  #loop-slider { flex: 1; height: 32px; margin: 0; cursor: pointer; }\n"
        "  #trend-numbers { margin: 0 28px 8px; font-size: 14px; font-weight: 600; }\n"
        "  #param-table { flex: 1 1 420px; margin: 0; border-collapse: collapse; width: auto; min-width: 280px; max-width: 48%; font-size: 13px; }\n"
        "  #param-table caption { caption-side: top; text-align: left; font-weight: 600; margin: 0 0 6px; }\n"
        "  #param-table th, #param-table td { border: 1px solid #e5e7eb; padding: 4px 8px; text-align: left; }\n"
        "  #param-table tr.on td { background: #e7eef8; }\n"
        "  #param-table td.params { font-size: 12px; color: #333; }\n"
        "  #param-table tbody tr { cursor: pointer; }\n"
        "  @media (max-width: 860px) {\n"
        "    .trend-row { flex-direction: column; }\n"
        "    #trend { width: 100%; height: auto; aspect-ratio: 720 / 444; flex-basis: auto; }\n"
        "    #param-table { width: 100%; max-width: none; flex-basis: auto; }\n"
        "  }\n"
    )
    page = page.replace("</style>", style + "</style>", 1)
    chart = (
        "    </div>\n"
        "  </div>\n"
        "</details>\n"
        '<h3 class="trend-title">Revenue and cost by loop</h3>\n'
        '<label class="trend-slider" for="loop-slider">Loop\n'
        '<input id="loop-slider" type="range" min="0" max="0" value="0" step="1" aria-label="Select loop">\n'
        '<span id="loop-slider-label"></span></label>\n'
        '<p id="trend-numbers"></p>\n'
        '<div class="trend-row">\n'
        '<svg id="trend" role="img" aria-label="Revenue, cost, and profit by loop. Each series has its own USD axis."></svg>\n'
        '<table id="param-table"></table>\n'
        '</div>\n'
    )
    anchor = "    </div>\n  </div>\n</details>\n"
    if page.count(anchor) != 1:
        raise SystemExit("Report page has no summary block for the trend chart.")
    page = page.replace(anchor, chart, 1)
    script = r"""
const TREND = { width: 720, height: 444, left: 58, right: 16, top: 4, bottom: 22 };
function trendMoney(value) {
  return typeof value === "number" ? value.toFixed(4) : "not recorded";
}
const TREND_BANDS = [
  { key: "revenue", label: "Revenue", color: "#1d4ed8" },
  { key: "cost", label: "Cost", color: "#b45309" },
  { key: "net", label: "Profit", color: "#15803d" }
];
function drawTrend() {
  const svg = document.getElementById("trend");
  const series = DATA.series || [];
  if (!svg || !series.length) return;
  const width = TREND.width;
  const height = TREND.height;
  const left = TREND.left;
  const right = TREND.right;
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  const span = Math.max(series.length - 1, 1);
  const picked = Math.max(0, Math.min(loopIndex.value, series.length - 1));
  const slot = (height - TREND.top - TREND.bottom) / TREND_BANDS.length;
  const labelH = 16;
  const plotH = slot - labelH;
  function xAt(index) {
    return left + (index / span) * (width - left - right);
  }
  function domain(key) {
    const vals = [];
    series.forEach(function (point) {
      if (typeof point[key] === "number") vals.push(point[key]);
    });
    if (!vals.length) return null;
    const dataLo = Math.min.apply(null, vals);
    const dataHi = Math.max.apply(null, vals);
    let lo = Math.min(0, dataLo);
    let hi = Math.max(0, dataHi);
    if (hi === lo) hi = lo + 1;
    const step = stepFor({ lo: lo, hi: hi });
    lo = Math.floor(lo / step + 1e-9) * step;
    hi = Math.ceil(hi / step - 1e-9) * step;
    if (dataHi > 0 && hi - dataHi < step * 0.12) hi += step;
    if (dataLo < 0 && dataLo - lo < step * 0.12) lo -= step;
    if (hi === lo) hi = lo + step;
    return { lo: lo, hi: hi, step: step };
  }
  function stepFor(dom) {
    const raw = (dom.hi - dom.lo) / 5;
    const pow = Math.pow(10, Math.floor(Math.log10(raw)));
    const frac = raw / pow;
    const nice = frac <= 1 ? 1 : frac <= 2 ? 2 : frac <= 5 ? 5 : 10;
    return nice * pow;
  }
  function tickDigits(step) {
    if (Math.abs(step) >= 1) return 0;
    let probe = Math.abs(step);
    let digits = 0;
    while (digits < 4 && Math.abs(probe - Math.round(probe)) > 1e-6) {
      probe *= 10;
      digits += 1;
    }
    return digits;
  }
  function tickText(value, step) {
    const digits = tickDigits(step);
    const rounded = Math.round(value / step) * step;
    const clean = Math.abs(rounded) < step * 0.01 ? 0 : rounded;
    return clean.toFixed(digits);
  }
  const ns = "http://www.w3.org/2000/svg";
  function add(name, attrs) {
    const node = document.createElementNS(ns, name);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    svg.appendChild(node);
    return node;
  }
  svg.replaceChildren();
  TREND_BANDS.forEach(function (band, bandIndex) {
    const dom = domain(band.key);
    if (!dom) return;
    const y0 = TREND.top + bandIndex * slot + labelH;
    const y1 = y0 + plotH;
    function yAt(value) {
      return y1 - ((value - dom.lo) / (dom.hi - dom.lo)) * plotH;
    }
    const title = add("text", {
      x: left,
      y: y0 - 4,
      fill: band.color,
      "font-size": "12",
      "font-weight": "700"
    });
    title.textContent = band.label + " USD";
    add("rect", {
      x: left,
      y: y0,
      width: width - left - right,
      height: plotH,
      fill: "#fff",
      stroke: "#e5e7eb"
    });
    const step = dom.step;
    const start = Math.ceil(dom.lo / step - 1e-9);
    const end = Math.floor(dom.hi / step + 1e-9);
    for (let i = start; i <= end; i += 1) {
      const value = i * step;
      const y = yAt(value);
      const zero = Math.abs(value) < step * 0.01;
      add("line", {
        x1: left,
        y1: y,
        x2: width - right,
        y2: y,
        stroke: zero ? "#cbd5e1" : "#f3f4f6"
      });
      const tick = add("text", {
        x: left - 6,
        y: Math.min(y1 - 2, Math.max(y0 + 10, y + 3)),
        fill: "#555",
        "font-size": "11",
        "text-anchor": "end"
      });
      tick.textContent = tickText(value, step);
    }
    add("line", {
      x1: xAt(picked),
      y1: y0,
      x2: xAt(picked),
      y2: y1,
      stroke: "#111",
      "stroke-width": "1.25"
    });
    let body = "";
    series.forEach(function (point, index) {
      if (typeof point[band.key] !== "number") return;
      body += (body ? " L " : "M ") + xAt(index).toFixed(1) + " " + yAt(point[band.key]).toFixed(1);
    });
    add("path", { d: body, fill: "none", stroke: band.color, "stroke-width": "2" });
    series.forEach(function (point, index) {
      if (typeof point[band.key] !== "number") return;
      const dot = add("circle", {
        cx: xAt(index),
        cy: yAt(point[band.key]),
        r: index === picked ? 4.5 : 2.4,
        fill: band.color
      });
      const tip = document.createElementNS(ns, "title");
      tip.textContent = "Loop " + point.loop + " " + band.label + " " + point[band.key].toFixed(4) + " USD";
      dot.appendChild(tip);
    });
    if (band.key === "net") {
      const point = series[picked];
      if (point && typeof point.net === "number") {
        const anchor = picked === 0 ? "start" : (picked === series.length - 1 ? "end" : "middle");
        const label = add("text", {
          x: xAt(picked),
          y: Math.max(y0 + 14, yAt(point.net) - 8),
          fill: "#15803d",
          "font-size": "12",
          "font-weight": "700",
          "text-anchor": anchor
        });
        label.textContent = point.net.toFixed(4);
      }
    }
  });
  add("text", { x: left, y: height - 6, fill: "#555", "font-size": "11" }).textContent = "Loop " + series[0].loop;
  add("text", { x: width - 78, y: height - 6, fill: "#555", "font-size": "11" }).textContent = "Loop " + series[series.length - 1].loop;
}
function trendDiff(fields, base, names) {
  if (!base) return false;
  return names.some(function (name) {
    return String(base[name]) !== String(fields[name]);
  });
}
function trendChip(text, on) {
  const chip = document.createElement("span");
  chip.className = on ? "chip chip-on" : "chip";
  chip.textContent = text;
  return chip;
}
function trendChips(fields, base) {
  const wrap = document.createElement("div");
  wrap.className = "chips";
  const p = fields || {};
  const anion = p.ANION === "chloride" ? "Cl" : (p.ANION === "sulfate" ? "SO4" : String(p.ANION || "?"));
  wrap.appendChild(trendChip(
    (p.TEMP_C != null ? p.TEMP_C : "?") + " C · " + (p.WATER_KG != null ? p.WATER_KG : "?") + " kg · " + anion + " · " + (p.BM_ADD || "?") + " (" + (p.BM_BATCHES != null ? p.BM_BATCHES : "?") + ")",
    trendDiff(p, base, ["TEMP_C", "WATER_KG", "ANION", "BM_ADD", "BM_BATCHES"])
  ));
  wrap.appendChild(trendChip(
    "S1 " + (p.REAGENT_1 || "?") + " " + (p.AMOUNT_1 != null ? p.AMOUNT_1 : "?") + " mol / " + (p.STEPS_1 != null ? p.STEPS_1 : "?") + " " + (p.ADD_1 || ""),
    trendDiff(p, base, ["REAGENT_1", "AMOUNT_1", "ADD_1", "STEPS_1"])
  ));
  if (String(p.STAGE) === "2") {
    wrap.appendChild(trendChip(
      "S2 " + (p.REAGENT_2 || "?") + " " + (p.AMOUNT_2 != null ? p.AMOUNT_2 : "?") + " mol / " + (p.STEPS_2 != null ? p.STEPS_2 : "?"),
      trendDiff(p, base, ["STAGE", "REAGENT_2", "AMOUNT_2", "ADD_2", "STEPS_2"])
    ));
  } else {
    wrap.appendChild(trendChip("Stage 1 only", trendDiff(p, base, ["STAGE"])));
  }
  return wrap;
}
function paintTrend() {
  const series = DATA.series || [];
  const last = Math.max(series.length - 1, 0);
  const index = Math.max(0, Math.min(loopIndex.value, last));
  const point = series[index] || null;
  const slider = document.getElementById("loop-slider");
  const sliderLabel = document.getElementById("loop-slider-label");
  const numbers = document.getElementById("trend-numbers");
  if (slider) slider.value = String(index);
  if (sliderLabel) sliderLabel.textContent = point ? ("Loop " + point.loop) : "No loop";
  if (numbers) {
    numbers.textContent = point
      ? ("Loop " + point.loop + "  Revenue " + trendMoney(point.revenue) + " USD  Cost " + trendMoney(point.cost) + " USD  Profit " + trendMoney(point.net) + " USD")
      : "No scored loop.";
  }
  const conclusion = document.getElementById("conclusion");
  if (conclusion) {
    const base = DATA.story && DATA.story.conclusion ? DATA.story.conclusion : "";
    conclusion.textContent = point
      ? ("Loop " + point.loop + ". Revenue " + trendMoney(point.revenue) + " USD. Cost " + trendMoney(point.cost) + " USD. Profit " + trendMoney(point.net) + " USD. " + base)
      : base;
  }
  const table = document.getElementById("param-table");
  if (!table) return;
  const ranked = series.filter(function (item) { return typeof item.net === "number"; });
  ranked.sort(function (a, b) { return b.net - a.net || Number(a.loop) - Number(b.loop); });
  const top = ranked.slice(0, 3);
  const used = {};
  top.forEach(function (item) { used[item.loop] = true; });
  const bottom = ranked.filter(function (item) { return !used[item.loop]; }).slice(-3).reverse();
  const shown = [];
  top.forEach(function (item, i) { shown.push(["Top " + (i + 1), item]); });
  bottom.forEach(function (item, i) { shown.push(["Bottom " + (i + 1), item]); });
  if (point && !used[point.loop] && !bottom.some(function (item) { return item.loop === point.loop; })) {
    shown.push(["Selected", point]);
  }
  table.replaceChildren();
  const caption = document.createElement("caption");
  caption.textContent = "Top 3 and bottom 3 by net profit. A gold chip differs from loop 0.";
  table.appendChild(caption);
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Rank", "Loop", "Revenue USD", "Cost USD", "Profit USD"].forEach(function (name) {
    const cell = document.createElement("th");
    cell.textContent = name;
    headRow.appendChild(cell);
  });
  head.appendChild(headRow);
  table.appendChild(head);
  const body = document.createElement("tbody");
  if (!shown.length) {
    const empty = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "No scored loop.";
    empty.appendChild(cell);
    body.appendChild(empty);
  }
  const seedPoint = series.filter(function (item) { return item.loop === 0; })[0] || series[0] || {};
  const seedParams = seedPoint.params || null;
  function groupRow(text) {
    const row = document.createElement("tr");
    row.className = "group";
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = text;
    row.appendChild(cell);
    return row;
  }
  let sawTop = false;
  let sawBottom = false;
  shown.forEach(function (entry) {
    const label = entry[0];
    const item = entry[1];
    if (label.indexOf("Top") === 0 && !sawTop) {
      sawTop = true;
      body.appendChild(groupRow("Top 3"));
    }
    if (label.indexOf("Bottom") === 0 && !sawBottom) {
      sawBottom = true;
      body.appendChild(groupRow("Bottom 3"));
    }
    const picked = series.findIndex(function (candidate) { return candidate.loop === item.loop; });
    const selected = point && item.loop === point.loop;
    const line = document.createElement("tr");
    if (selected) line.className = "on";
    [label, item.loop, trendMoney(item.revenue), trendMoney(item.cost), trendMoney(item.net)].forEach(function (text, col) {
      const td = document.createElement("td");
      td.textContent = String(text);
      if (col === 4 && typeof item.net === "number") td.className = item.net >= 0 ? "good" : "bad";
      line.appendChild(td);
    });
    const params = document.createElement("tr");
    if (selected) params.className = "on";
    const paramCell = document.createElement("td");
    paramCell.colSpan = 5;
    paramCell.className = "params";
    const base = seedParams && item.loop !== seedPoint.loop ? seedParams : null;
    if (item.params && Object.keys(item.params).length) paramCell.appendChild(trendChips(item.params, base));
    else paramCell.textContent = "No parameters recorded.";
    params.appendChild(paramCell);
    function choose() {
      if (picked >= 0) selectTrend(picked);
    }
    line.addEventListener("click", choose);
    params.addEventListener("click", choose);
    body.appendChild(line);
    body.appendChild(params);
  });
  table.appendChild(body);
}
function selectTrend(index, fromPlay) {
  const series = DATA.series || [];
  const last = Math.max((DATA.loops || []).length - 1, 0);
  let next = Number(index);
  if (!isFinite(next)) next = 0;
  next = Math.max(0, Math.min(last, next));
  if (!fromPlay) stopPlay();
  loopIndex.value = next;
  const slider = document.getElementById("loop-slider");
  if (slider) {
    slider.max = String(Math.max(series.length - 1, 0));
    slider.value = String(next);
  }
  draw();
  syncStory();
  drawTrend();
  paintTrend();
}
function bindTrend() {
  const slider = document.getElementById("loop-slider");
  const svg = document.getElementById("trend");
  if (slider && !slider.dataset.bound) {
    slider.dataset.bound = "1";
    slider.addEventListener("input", function () { selectTrend(slider.value); });
    slider.addEventListener("change", function () { selectTrend(slider.value); });
  }
  if (svg && !svg.dataset.bound) {
    svg.dataset.bound = "1";
    svg.addEventListener("click", function (event) {
      const series = DATA.series || [];
      if (!series.length) return;
      const rect = svg.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const scale = Math.min(rect.width / TREND.width, rect.height / TREND.height);
      const padX = (rect.width - TREND.width * scale) / 2;
      const x = (event.clientX - rect.left - padX) / scale;
      const span = Math.max(series.length - 1, 1);
      const plot = TREND.width - TREND.left - TREND.right;
      let picked = Math.round(((x - TREND.left) / plot) * span);
      if (series.length === 1) picked = 0;
      selectTrend(picked);
    });
  }
}
const bestIndex = DATA.loops.findIndex(function (loop) { return loop.note === "best"; });
if (bestIndex >= 0) {
  loopIndex.value = bestIndex;
  const bestLoop = DATA.loops[bestIndex];
  const bestFrame = story.frames.findIndex(function (frame) { return frame.loop === bestLoop.id; });
  if (bestFrame >= 0) storyIndex = bestFrame;
}
const loopSlider = document.getElementById("loop-slider");
if (loopSlider) {
  const count = (DATA.series || []).length;
  loopSlider.min = "0";
  loopSlider.max = String(Math.max(count - 1, 0));
  loopSlider.value = String(loopIndex.value);
}
bindTrend();
drawTrend();
paintTrend();
"""
    target = "draw();\npinNav();\npaintStory();"
    if target not in page:
        raise SystemExit("Report page has no draw call for the trend chart.")
    return page.replace(target, script + "draw();\npinNav();\npaintStory();", 1)
