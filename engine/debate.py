# -*- coding: utf-8 -*-
"""Profit debate before a proposed config is written.

The first proposer defends the next MODIFY config.
The first adversary attacks it.
When PROPOSE_MODEL_2 and ADVERSARY_MODEL_2 are both set, that pair reads
the first debate and the latest history result, then argues again.
One verdict model returns keep or reject from every argument.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import config as cfg
import proposals


def list_models() -> list[str]:
    """Return installed Ollama model names. Empty when the server does not answer."""
    base = cfg.OLLAMA.split("/api/")[0]
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"Ollama model list failed: {exc}")
        return []
    names: list[str] = []
    for row in payload.get("models") or []:
        name = row.get("name") or row.get("model")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _pick(label: str, default: str, names: list[str]) -> str:
    print(f"{label} [{default}]")
    raw = input("> ").strip()
    if not raw:
        return default
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(names):
            return names[index - 1]
        print(f"No model number {index}. Using {default}.")
        return default
    return raw


def resolve_models(
    propose: str | None = None,
    verdict: str | None = None,
    adversary: str | None = None,
    interactive: bool = False,
) -> dict[str, str]:
    """Fill proposer, adversary, and verdict model names.

    Pass interactive=True only for --ask.
    Otherwise the names in engine/config.py are used.
    """
    if interactive and not propose and not verdict and not sys.stdin.isatty():
        print("No terminal for --ask. Using the models in engine/config.py.")
    if interactive and sys.stdin.isatty() and not propose and not verdict:
        names = list_models()
        print("Choose chat models. Do not use an embedding model.")
        if names:
            for index, name in enumerate(names, start=1):
                print(f"  {index}  {name}")
        else:
            print("Type a model name. Ollama did not return a list.")
        propose = _pick("Proposer model (writes and defends the next config)", cfg.PROPOSE_MODEL, names)
        verdict = _pick("Verdict model (keep or reject)", cfg.VERDICT_MODEL, names)
    def optional(name: str) -> str | None:
        value = getattr(cfg, name, None)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    models = {
        "propose": propose or cfg.PROPOSE_MODEL,
        "adversary": adversary or cfg.ADVERSARY_MODEL,
        "verdict": verdict or cfg.VERDICT_MODEL,
        "propose_2": optional("PROPOSE_MODEL_2"),
        "adversary_2": optional("ADVERSARY_MODEL_2"),
    }
    second = ""
    if models["propose_2"] and models["adversary_2"]:
        second = f"; second proposer {models['propose_2']}; second adversary {models['adversary_2']}"
    else:
        models["propose_2"] = None
        models["adversary_2"] = None
        second = "; second pair off"
    print(
        f"debate models: proposer {models['propose']}; "
        f"adversary {models['adversary']}; verdict {models['verdict']}{second}"
    )
    return models


def _side_schema(claim_name: str, detail_name: str) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            claim_name: {"type": "string"},
            detail_name: {"type": "string"},
        },
        "required": [claim_name, detail_name],
    }


def _verdict_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "verdict": {"type": "string", "enum": ["keep", "reject"]},
            "reason": {"type": "string"},
        },
        "required": ["verdict", "reason"],
    }


def parse_verdict(payload: dict | None) -> str:
    """Return keep only when the verdict model said keep."""
    if not isinstance(payload, dict):
        return "reject"
    word = str(payload.get("verdict") or "").strip().lower()
    return "keep" if word == "keep" else "reject"


def _changes(current: dict, candidate: dict) -> dict:
    return {
        key: {"from": current.get(key), "to": candidate.get(key)}
        for key in candidate
        if current.get(key) != candidate.get(key)
    }


def _run_root(log_path: Path) -> Path:
    parent = Path(log_path).parent
    if parent.name.startswith("loop"):
        return parent.parent
    return parent


def _packet(current: dict, candidate: dict, history: list[dict], note: str | None, summary: dict | None) -> dict:
    packet = {
        "purity_gate": cfg.PURITY_THRESHOLD,
        "goal": "Maximize net_profit_usd. A cut below the purity gate has zero revenue.",
        "current_config": current,
        "candidate_config": candidate,
        "changes": _changes(current, candidate),
        "latest_result": history[-1] if history else None,
    }
    if summary:
        packet["history_summary"] = summary
    if note:
        packet["note"] = note[:1200]
    return packet


def judge(
    models: dict[str, str],
    current: dict,
    candidate: dict,
    history: list[dict],
    log_path: Path,
    note: str | None = None,
) -> dict:
    """Run one debate and append it to the case log. Missing model text is a reject."""
    summary = proposals.history_view(history, _run_root(log_path))
    packet = json.dumps(_packet(current, candidate, history, note, summary))
    proposer = proposals.ask_model(
        models["propose"],
        packet,
        cfg.OLLAMA_TIMEOUT_S,
        system=(
            "You defend this next ligand-free PHREEQC config. "
            "Use only numbers in the user JSON. Do not invent purity or profit. "
            "Keep each string under 40 words."
        ),
        response_schema=_side_schema("claim", "evidence"),
        temperature=0.3,
        num_predict=1200,
    )
    adversary = proposals.ask_model(
        models["adversary"],
        packet,
        cfg.OLLAMA_TIMEOUT_S,
        system=(
            "You attack this next ligand-free PHREEQC config. "
            "A cut below the purity gate earns no revenue. "
            "Use only numbers in the user JSON. Do not invent figures. "
            "Keep each string under 40 words."
        ),
        response_schema=_side_schema("attack", "risk"),
        temperature=0.3,
        num_predict=1200,
    )
    proposer_2 = None
    adversary_2 = None
    if models.get("propose_2") and models.get("adversary_2"):
        review = {
            "history_result": history[-1] if history else None,
            "first_proposer": proposer,
            "first_adversary": adversary,
            "candidate_config": candidate,
            "changes": _changes(current, candidate),
        }
        if summary:
            review["history_summary"] = summary
        review = json.dumps(review)
        proposer_2 = proposals.ask_model(
            models["propose_2"],
            review,
            cfg.OLLAMA_TIMEOUT_S,
            system=(
                "You are the second proposer. Compare the first proposer and the first adversary "
                "with the history result. Defend the candidate with only those numbers. Do not invent figures. "
                "Keep each string under 40 words."
            ),
            response_schema=_side_schema("claim", "evidence"),
            temperature=0.3,
            num_predict=1200,
        )
        adversary_2 = proposals.ask_model(
            models["adversary_2"],
            review,
            cfg.OLLAMA_TIMEOUT_S,
            system=(
                "You are the second adversary. Compare the first proposer and the first adversary "
                "with the history result. A cut below the purity gate earns no revenue. "
                "Attack the candidate with only those numbers. Do not invent figures. "
                "Keep each string under 40 words."
            ),
            response_schema=_side_schema("attack", "risk"),
            temperature=0.3,
            num_predict=1200,
        )
    verdict_body = {
        "packet": json.loads(packet),
        "proposer": proposer,
        "adversary": adversary,
        "proposer_2": proposer_2,
        "adversary_2": adversary_2,
    }
    verdict_payload = proposals.ask_model(
        models["verdict"],
        json.dumps(verdict_body),
        cfg.OLLAMA_TIMEOUT_S,
        system=(
            "You are the only verdict. Read every proposer and adversary argument. "
            "Reply keep only when the candidate can raise net profit "
            "without paying for a cut below the purity gate. "
            "If the evidence is missing, reply reject. Do not invent numbers. "
            "Keep the reason under 40 words."
        ),
        response_schema=_verdict_schema(),
        temperature=0.0,
        num_predict=800,
    )
    source_loop = history[-1].get("loop") if history and isinstance(history[-1], dict) else None
    record = {
        "loop": source_loop,
        "propose_model": models["propose"],
        "adversary_model": models["adversary"],
        "propose_model_2": models.get("propose_2"),
        "adversary_model_2": models.get("adversary_2"),
        "verdict_model": models["verdict"],
        "candidate": candidate,
        "changes": _changes(current, candidate),
        "proposer": proposer,
        "adversary": adversary,
        "proposer_2": proposer_2,
        "adversary_2": adversary_2,
        "verdict": parse_verdict(verdict_payload),
        "reason": (verdict_payload or {}).get("reason") or "Verdict model returned no JSON.",
    }
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    with log_path.with_name("debate.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    print(f"    verdict {record['verdict']} ({record['verdict_model']}): {record['reason']}")
    print(f"DEBATE={log_path}")
    return record
