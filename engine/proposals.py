# -*- coding: utf-8 -*-
"""Shared proposal schema, Ollama call, and fallback configs for both loops."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import config as cfg
import config_rules as rules
from ask_thermo_loader import usable_reagent_names

USABLE = usable_reagent_names()


def schema() -> dict:
    def num(lo, hi):
        return {"type": "number", "minimum": lo, "maximum": hi}

    def integer(lo, hi):
        return {"type": "integer", "minimum": lo, "maximum": hi}

    def choices(name: str, default: list[str] | tuple[str, ...]) -> list[str]:
        raw = getattr(cfg, f"{name}_CHOICES", None)
        if raw is None:
            return list(default)
        picked = [str(item) for item in raw]
        if name.startswith("REAGENT"):
            picked = [item for item in picked if item in USABLE]
        return picked or list(default)

    def amount_span(name: str) -> tuple[float, float]:
        lo = getattr(cfg, f"{name}_MIN", 0.05)
        hi = getattr(cfg, f"{name}_MAX", cfg.AMOUNT_MAX)
        return float(lo), float(hi)

    def step_span(name: str) -> tuple[int, int]:
        lo = int(getattr(cfg, f"{name}_MIN", 1))
        hi = int(getattr(cfg, f"{name}_MAX", cfg.STEPS_MAX))
        return lo, hi

    stage_choices = [int(item) for item in getattr(cfg, "STAGE_CHOICES", (1, 2))]
    amount_1 = amount_span("AMOUNT_1")
    amount_2 = amount_span("AMOUNT_2")
    steps_1 = step_span("STEPS_1")
    steps_2 = step_span("STEPS_2")
    config_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "TEMP_C": num(cfg.TEMP_C_MIN, cfg.TEMP_C_MAX),
            "WATER_KG": num(cfg.WATER_KG_MIN, cfg.WATER_KG_MAX),
            "ANION": {"type": "string", "enum": ["sulfate", "chloride"]},
            "BM_ADD": {"type": "string", "enum": choices("BM_ADD", ("batch", "staged", "continuous"))},
            "BM_BATCHES": integer(1, cfg.BM_BATCHES_MAX),
            "STAGE": integer(min(stage_choices), max(stage_choices)),
            "REAGENT_1": {"type": "string", "enum": choices("REAGENT_1", USABLE)},
            "AMOUNT_1": num(*amount_1),
            "ADD_1": {"type": "string", "enum": choices("ADD_1", ("once", "continuous"))},
            "STEPS_1": integer(*steps_1),
            "REAGENT_2": {"type": "string", "enum": choices("REAGENT_2", USABLE)},
            "AMOUNT_2": num(*amount_2),
            "ADD_2": {"type": "string", "enum": choices("ADD_2", ("once", "continuous"))},
            "STEPS_2": integer(*steps_2),
        },
        "required": list(cfg.MODIFY),
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stop": {"type": "boolean"},
            "reason": {"type": "string"},
            "config": config_schema,
        },
        "required": ["stop", "reason", "config"],
    }


def compact_result(result: dict) -> dict:
    metals = {
        name: {
            key: row.get(key)
            for key in ("purity", "recovery", "revenue_usd", "usable", "pH", "target_pH", "cut_pH")
        }
        for name, row in (result.get("metals") or {}).items()
    }
    return {
        "net_profit_usd": result.get("net_profit_usd"),
        "revenue_usd": result.get("revenue_usd"),
        "price_weighted_score": result.get("price_weighted_score"),
        "costs": result.get("costs"),
        "metals": metals,
        "lithium_source": (result.get("lithium") or {}).get("source"),
        "low_assignment": (result.get("windows") or {}).get("low", {}).get("assignment"),
        "high_assignment": (result.get("windows") or {}).get("high", {}).get("assignment"),
    }


def scored_rounds(history: list[dict]) -> int:
    """Cases that already have a score. Rejected proposals do not count."""
    return sum(1 for row in history if row.get("status") == "ok")


def rounds_before_stop(planned: int | None = None) -> int:
    """Scored cases required before a model stop can end the run.

    DEFAULT_ROUNDS is the plan. STOP_COOLDOWN_ROUNDS cannot cut that plan short.
    The runner scores the seed plus one case per planned round.
    """
    plan = int(cfg.DEFAULT_ROUNDS if planned is None else planned)
    cooldown = int(getattr(cfg, "STOP_COOLDOWN_ROUNDS", 3))
    return max(plan + 1, cooldown)


def stop_allowed(history: list[dict], planned: int | None = None) -> bool:
    """The proposer may set stop only after the planned rounds are scored."""
    return scored_rounds(history) >= rounds_before_stop(planned)


def _ok_profits(history: list[dict]) -> list[dict]:
    return [
        row for row in history
        if row.get("status") == "ok" and isinstance(row.get("net_profit_usd"), (int, float))
    ]


def flat_window(history: list[dict]) -> str | None:
    """Ask for a large change after each block of scored cases that did not improve enough."""
    every = int(getattr(cfg, "SHAKE_EVERY", 3))
    gain = float(getattr(cfg, "SHAKE_MIN_GAIN_USD", 0.05))
    ok = _ok_profits(history)
    if every < 1 or len(ok) < every or len(ok) % every != 0:
        return None
    best_window = max(row["net_profit_usd"] for row in ok[-every:])
    prior = ok[:-every]
    baseline = max(row["net_profit_usd"] for row in prior) if prior else ok[0]["net_profit_usd"]
    if best_window >= baseline + gain:
        return None
    return (
        f"The last {every} scored cases did not raise net profit by {gain:g} USD. "
        f"Best in that window is {best_window:.4f}. Previous best is {baseline:.4f}. "
        "Move temperature, anion, water, or a reagent amount to another legal value inside the bounds. "
        "A 2x jump that leaves the bounds is rejected."
    )


def is_shake(current: dict, candidate: dict) -> bool:
    """True when the candidate leaves the local step-count neighborhood."""
    big = ("ANION", "REAGENT_1", "REAGENT_2", "STAGE", "BM_ADD", "ADD_1")
    if any(current.get(name) != candidate.get(name) for name in big):
        return True
    try:
        if abs(float(candidate.get("TEMP_C", 0)) - float(current.get("TEMP_C", 0))) >= 20:
            return True
        water_now = float(current.get("WATER_KG", 1))
        water_next = float(candidate.get("WATER_KG", water_now))
        amount_now = float(current.get("AMOUNT_1", 1))
        amount_next = float(candidate.get("AMOUNT_1", amount_now))
    except (TypeError, ValueError):
        return False
    if water_now > 0 and water_next > 0 and (water_next / water_now >= 2 or water_now / water_next >= 2):
        return True
    if amount_now > 0 and amount_next > 0 and (amount_next / amount_now >= 2 or amount_now / amount_next >= 2):
        return True
    return False


def shake(fields: dict, seen: set[str]) -> dict | None:
    """First unused config that changes reagent, anion, stage, feed mode, or a large amount."""
    base = dict(fields)
    other = next((name for name in USABLE if name != base.get("REAGENT_1")), base.get("REAGENT_1"))
    hot = cfg.TEMP_C_MAX if float(base.get("TEMP_C", 25)) < 50 else cfg.TEMP_C_MIN
    water = 0.2 if float(base.get("WATER_KG", 1)) >= 1 else 8.0
    amount = min(cfg.AMOUNT_MAX, max(0.2, float(base.get("AMOUNT_1", 4)) * 2))
    ideas: list[dict] = [
        {"ANION": "chloride" if base.get("ANION") == "sulfate" else "sulfate", "TEMP_C": hot},
        {"REAGENT_1": other, "AMOUNT_1": amount, "ADD_1": "once" if base.get("ADD_1") == "continuous" else "continuous"},
        {"STAGE": 2, "REAGENT_2": other, "AMOUNT_2": 3.0, "ADD_2": "once", "STEPS_2": 8},
        {"BM_ADD": "continuous", "ADD_1": "continuous", "STEPS_1": 6, "WATER_KG": water},
        {"BM_ADD": "staged", "BM_BATCHES": 5, "ADD_1": "continuous", "STEPS_1": 5, "TEMP_C": hot},
        {"TEMP_C": hot, "WATER_KG": water, "AMOUNT_1": min(cfg.AMOUNT_MAX, 12.0)},
    ]
    for patch in ideas:
        merged = {**base, **patch}
        try:
            resolved = rules.resolved_fields(merged, USABLE)
        except rules.ConfigError:
            continue
        if not is_shake(base, resolved):
            continue
        key = json.dumps(resolved, sort_keys=True)
        if key in seen:
            continue
        return resolved
    return None


def record_shake(path, reason: str, candidate: dict) -> None:
    """Append one forced jump so the chain report can show it."""
    from pathlib import Path

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"reason": reason, "config": candidate}) + "\n")


def _clip_words(value: object, limit: int = 180) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _debate_files(root: Path | None) -> list[Path]:
    if root is None:
        return []
    folder = Path(root)
    if folder.is_file():
        return [folder]
    if not folder.is_dir():
        return []
    files = [folder / "debate.jsonl", folder / "agent" / "debate.jsonl"]
    files.extend(sorted(folder.glob("loop*/debate.jsonl")))
    files.extend(sorted(folder.glob("loops/loop*/debate.jsonl")))
    return [path for path in files if path.is_file()]


def _load_debates(root: Path | None) -> list[dict]:
    records = []
    for path in _debate_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append(row)
    return records


def _argument_summary(record: dict) -> str:
    parts = []
    for label, payload, key in (
        ("proposer", record.get("proposer"), "claim"),
        ("adversary", record.get("adversary"), "attack"),
        ("proposer 2", record.get("proposer_2"), "claim"),
        ("adversary 2", record.get("adversary_2"), "attack"),
    ):
        if isinstance(payload, dict) and payload.get(key):
            parts.append(f"{label}: {_clip_words(payload[key])}")
    if record.get("reason"):
        parts.append(f"verdict: {_clip_words(record['reason'])}")
    return " | ".join(parts) if parts else "No argument recorded."


def _argument_for_config(config: object, debates: list[dict]) -> str:
    if not isinstance(config, dict):
        return "No argument recorded."
    key = json.dumps(config, sort_keys=True)
    matches = [
        row for row in debates
        if isinstance(row.get("candidate"), dict) and json.dumps(row["candidate"], sort_keys=True) == key
    ]
    if not matches:
        return "No argument recorded."
    kept = [row for row in matches if row.get("verdict") == "keep"]
    return _argument_summary(kept[-1] if kept else matches[-1])


def _cost_usd(row: dict) -> object:
    costs = row.get("costs")
    if isinstance(costs, dict) and costs.get("total_cost_usd") is not None:
        return costs.get("total_cost_usd")
    return row.get("total_cost_usd")


def history_view(history: list[dict], debate_root: Path | None = None) -> dict | None:
    """Top and bottom scored runs after HISTORY_SUMMARY_AFTER. None before that cut."""
    after = max(0, int(getattr(cfg, "HISTORY_SUMMARY_AFTER", 6)))
    each = max(0, int(getattr(cfg, "HISTORY_SUMMARY_EACH", 2)))
    scored = _ok_profits(history)
    if len(scored) <= after:
        return None
    debates = _load_debates(debate_root)
    ranked = sorted(scored, key=lambda row: (float(row["net_profit_usd"]), row.get("loop") or 0))
    bottom_rows = ranked[:each]
    top_rows = list(reversed(ranked[-each:])) if each else []
    bottom_ids = {row.get("loop") for row in bottom_rows}
    top_ids = {row.get("loop") for row in top_rows}

    def brief(row: dict, rank: str) -> dict:
        loop_id = row.get("loop")
        if loop_id in bottom_ids and loop_id in top_ids:
            rank = "both"
        return {
            "rank": rank,
            "loop": loop_id,
            "net_profit_usd": row.get("net_profit_usd"),
            "revenue_usd": row.get("revenue_usd"),
            "total_cost_usd": _cost_usd(row),
            "config": row.get("config"),
            "argument": _argument_for_config(row.get("config"), debates),
        }

    top = [brief(row, "top") for row in top_rows]
    bottom = [brief(row, "bottom") for row in bottom_rows if row.get("loop") not in top_ids]
    kept = {row.get("loop") for row in top_rows + bottom_rows}
    view = {
        "scored_runs": len(scored),
        "after": after,
        "each": each,
        "omitted": len(scored) - len(kept),
        "top": top,
        "bottom": bottom,
    }
    if debate_root is not None and Path(debate_root).is_dir():
        dest = Path(debate_root) / "history_summary.json"
        dest.write_text(json.dumps(view, indent=2) + "\n", encoding="utf-8")
    return view


def prompt(
    history: list[dict],
    fields: dict,
    error: str | None,
    debate_root: Path | None = None,
    planned: int | None = None,
    extra: dict | None = None,
) -> str:
    limits = [
            "There is no ligand. Do not add one.",
            "Change at least one MODIFY field.",
            "Do not repeat a config already in history.",
            (
                "Stage 1 adds AMOUNT_1. Stage 2 adds all of AMOUNT_1, filters, then adds AMOUNT_2. "
                "Staged black mass needs BM_BATCHES >= 2."
            ),
            (
                "If Ni, Co, and Mn already reach solid fraction 1 during stage 1, and stage 2 "
                "adds no more solid, precipitation finished in stage 1. Propose STAGE 1 next. "
                "Set AMOUNT_1 to the stage-1 dose where the last of those metals finishes. "
                "STAGE 2 then pays for AMOUNT_2 and the stage-2 filter and recovers nothing."
            ),
            (
                f"Bounds: TEMP_C {cfg.TEMP_C_MIN:g} to {cfg.TEMP_C_MAX:g}, "
                f"WATER_KG {cfg.WATER_KG_MIN:g} to {cfg.WATER_KG_MAX:g} kg, "
                f"AMOUNT_1 {getattr(cfg, 'AMOUNT_1_MIN', 0.05):g} to {getattr(cfg, 'AMOUNT_1_MAX', cfg.AMOUNT_MAX):g} mol, "
                f"AMOUNT_2 {getattr(cfg, 'AMOUNT_2_MIN', 0.05):g} to {getattr(cfg, 'AMOUNT_2_MAX', cfg.AMOUNT_MAX):g} mol, "
                f"STEPS_1 {getattr(cfg, 'STEPS_1_MIN', 1):g} to {getattr(cfg, 'STEPS_1_MAX', cfg.STEPS_MAX):g}, "
                f"STEPS_2 {getattr(cfg, 'STEPS_2_MIN', 1):g} to {getattr(cfg, 'STEPS_2_MAX', cfg.STEPS_MAX):g}, "
                f"BM_BATCHES 1 to {cfg.BM_BATCHES_MAX}."
            ),
            "A failed simulation lists the PHREEQC error. Avoid that cause.",
            "Reply with the JSON schema only.",
        ]
    need = rounds_before_stop(planned)
    if scored_rounds(history) < need:
        limits.append(
            f"stop must be false. Stop is allowed only after {need} scored cases. "
            f"Scored cases so far: {scored_rounds(history)}."
        )
    payload = {
        "goal": (
            "Maximize net_profit_usd. Revenue is recovered metal mass times its price, "
            "and only when that cut's purity is at least the threshold (default 0.70). "
            "Cost is reagent, neutralization of leftover alkali, heating, pH-raising penalty, "
            "a penalty per black-mass step and per reagent step, and a stage-2 filter cost."
        ),
        "rules": limits,
        "usable_reagents": USABLE,
        "current_config": fields,
        "error": error,
    }
    summary = history_view(history, debate_root)
    if summary is None:
        payload["history"] = history[-6:]
    else:
        payload["history_summary"] = summary
        limits.append(
            "history_summary keeps the highest and lowest scored runs and a short argument for each. "
            "The other scored runs are omitted."
        )
    if extra:
        payload.update(extra)
    return json.dumps(payload)


def _parse_model_json(content: str) -> dict | None:
    """Read one JSON object. A reply cut off by the token limit is closed and kept."""
    text = str(content or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    body = text[start:]
    for candidate in (body, body[: body.rfind("}") + 1] if "}" in body else ""):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    repaired = _finish_json(body)
    if not repaired:
        return None
    try:
        parsed = json.loads(repaired)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _finish_json(body: str) -> str | None:
    """Close an object that stopped inside a string or before its last brace."""
    out: list[str] = []
    stack: list[str] = []
    in_str = False
    escape = False
    for ch in body:
        if in_str:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_str = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
        out.append(ch)
    if in_str:
        if escape:
            out.append("\\")
        out.append('"')
    text = "".join(out).rstrip()
    while True:
        stripped = text.rstrip()
        if stripped.endswith(","):
            text = stripped[:-1]
            continue
        if stripped.endswith(":"):
            cut = max(stripped.rfind(","), stripped.rfind("{"), stripped.rfind("["))
            if cut < 0:
                return None
            text = stripped[: cut + 1] if stripped[cut] in "{[" else stripped[:cut]
            continue
        text = stripped
        break
    for closer in reversed(stack):
        text += closer
    return text or None


def ask_model(
    model: str,
    user: str,
    timeout: int,
    system: str | None = None,
    response_schema: dict | None = None,
    temperature: float = 0.2,
    num_predict: int = 700,
    num_ctx: int | None = None,
) -> dict | None:
    options: dict = {"temperature": temperature, "num_predict": num_predict}
    if num_ctx is not None:
        options["num_ctx"] = int(num_ctx)
    body = json.dumps(
        {
            "model": model,
            "stream": False,
            "think": False,
            "format": schema() if response_schema is None else response_schema,
            "options": options,
            "messages": [
                {
                    "role": "system",
                    "content": system
                    or "You choose the next ligand-free PHREEQC config. Return one JSON object.",
                },
                {"role": "user", "content": user},
            ],
        }
    ).encode("utf-8")
    url = cfg.OLLAMA
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Ollama call failed: {exc}")
        return None
    if num_ctx is not None:
        print(f"prompt_eval_count {payload.get('prompt_eval_count')} num_ctx {int(num_ctx)}")
    message = payload.get("message") or {}
    content = message.get("content") or message.get("thinking") or ""
    parsed = _parse_model_json(content)
    if parsed is None:
        print(f"Model did not return JSON: {content[:300]}")
        return None
    return parsed


def fallback(fields: dict, seen: set[str]) -> dict | None:
    """First nearby config that is not in history and passes the same checks."""
    base = dict(fields)
    ideas: list[dict] = [
        {"TEMP_C": 60.0},
        {"TEMP_C": 40.0},
        {"WATER_KG": 0.5},
        {"WATER_KG": 2.0},
        {"ANION": "chloride" if base.get("ANION") == "sulfate" else "sulfate"},
        {"BM_ADD": "continuous", "ADD_1": "continuous", "STEPS_1": 12},
        {"BM_ADD": "staged", "BM_BATCHES": 4, "ADD_1": "continuous", "STEPS_1": 12},
        {"BM_ADD": "batch", "BM_BATCHES": 1},
        {"STAGE": 2, "REAGENT_2": "Na2CO3", "AMOUNT_2": 1.5, "STEPS_2": 10},
        {"STAGE": 1},
        {"AMOUNT_2": 4.0, "STAGE": 2},
        {"AMOUNT_2": 1.0, "STAGE": 2},
        {"AMOUNT_1": 6.0},
        {"AMOUNT_1": 2.5},
        {"STEPS_1": 8},
        {"ADD_1": "once", "BM_ADD": "batch", "BM_BATCHES": 1},
    ]
    for name in USABLE:
        ideas.append({"REAGENT_1": name})
        ideas.append({"REAGENT_2": name, "STAGE": 2})
    for patch in ideas:
        merged = {**base, **patch}
        try:
            resolved = rules.resolved_fields(merged, USABLE)
        except rules.ConfigError:
            continue
        key = json.dumps(resolved, sort_keys=True)
        if key in seen:
            continue
        return resolved
    return None
