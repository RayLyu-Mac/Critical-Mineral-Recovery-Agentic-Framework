# -*- coding: utf-8 -*-
"""Ask the local model for the next MODIFY config and write it into the case folder."""
from __future__ import annotations

import json
import sys
from pathlib import Path

LOOP = Path(__file__).resolve().parents[1]
ENGINE = LOOP.parent / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
if str(LOOP) not in sys.path:
    sys.path.insert(0, str(LOOP))

import config as cfg  # noqa: E402
import config_rules as rules  # noqa: E402
import debate  # noqa: E402
import proposals  # noqa: E402


def _econ_json(root: Path) -> Path:
    path = root / "05_S01" / "econ_result.json"
    if not path.is_file():
        raise SystemExit(f"Missing {path}. Run 06_score.py first.")
    return path


def _read_history(history_path: Path) -> list[dict]:
    history: list[dict] = []
    if history_path.is_file():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                history.append(json.loads(line))
    return history


def _write_history(history_path: Path, history: list[dict]) -> None:
    history_path.write_text("\n".join(json.dumps(item) for item in history) + "\n", encoding="utf-8")


def _logs(root: Path, agent_dir: Path | None) -> Path:
    folder = Path(agent_dir) if agent_dir is not None else Path(root)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def record_failure(root: Path, error: str, agent_dir: Path | None = None) -> None:
    """Append one failed case when this round will not propose again."""
    root = Path(root)
    logs = _logs(root, agent_dir)
    text = (root / "config.py").read_text(encoding="utf-8")
    fields = rules.resolved_fields(rules.current_fields(text), proposals.USABLE)
    history_path = logs / "history.jsonl"
    history = _read_history(history_path)
    history.append({"loop": len(history), "config": fields, "error": error, "status": "failed"})
    _write_history(history_path, history)
    print(f"HISTORY={history_path}")


def _seen(history: list[dict]) -> set[str]:
    keys = set()
    for row in history:
        fields = row.get("config")
        if isinstance(fields, dict):
            try:
                keys.add(json.dumps(rules.resolved_fields(fields, proposals.USABLE), sort_keys=True))
            except rules.ConfigError:
                continue
    return keys


def _stop(logs: Path, history_path: Path, reason: str) -> Path:
    print(reason)
    (logs / "stop.txt").write_text(reason + "\n", encoding="utf-8")
    print(f"HISTORY={history_path}")
    return logs / "stop.txt"


def _curves_extra(root: Path, error: str | None) -> dict | None:
    """Curve CSV for the proposer. A failed case keeps the previous file, so it is not sent."""
    if error is not None or not getattr(cfg, "RAW_CURVES_IN_PROMPT", False):
        return None
    path = Path(root) / "05_S01" / "curves.csv"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    return {
        "curves_csv": text,
        "curves_note": (
            "curves_csv is the latest scored case. "
            "Columns: stage, dose_mol, pH, mu, then Al, Fe, Cu, Ni, Co, Mn. "
            "When the feed panel has more than one row, those values are the mean, "
            "and pH_sd, mu_sd, and each metal_sd follow. "
            "dose_mol is cumulative reagent added, mol. Stage 2 continues that axis. "
            "mu is ionic strength, mol/kg. Metal columns are solid / feed."
        ),
    }


def _warn_prompt_size(user: str, num_ctx: int | None) -> None:
    if num_ctx is None:
        return
    estimate = len(user) / 3
    room = int(num_ctx) - 700
    if estimate > room:
        print(
            f"    prompt estimate {estimate:.0f} tokens exceeds num_ctx {int(num_ctx)} minus 700. "
            "Ollama may cut the prompt."
        )


def _reject_reason(
    models: dict[str, str],
    fields: dict,
    candidate: dict,
    history: list[dict],
    log_path: Path,
) -> str | None:
    """Return None when the verdict is keep. Otherwise return the reject reason."""
    record = debate.judge(models, fields, candidate, history, log_path)
    if record["verdict"] == "keep":
        return None
    return str(record["reason"])


def propose_once(
    root: Path,
    model: str | None = None,
    models: dict[str, str] | None = None,
    planned: int | None = None,
    error: str | None = None,
    agent_dir: Path | None = None,
    config_out: Path | None = None,
) -> Path:
    """Read econ_result.json, debate the next case, and write it only when the verdict is keep.

    Pass error when the case failed. That path records a failed row and does not read the score.
    """
    root = Path(root)
    logs = _logs(root, agent_dir)
    config_path = root / "config.py"
    text = config_path.read_text(encoding="utf-8")
    fields = rules.resolved_fields(rules.current_fields(text), proposals.USABLE)
    history_path = logs / "history.jsonl"
    history = _read_history(history_path)
    if error:
        row = {"loop": len(history), "config": fields, "error": error, "status": "failed"}
    else:
        result = json.loads(_econ_json(root).read_text(encoding="utf-8"))
        if result.get("net_profit_usd") is None:
            row = {
                "loop": len(history),
                "config": fields,
                "error": "score missing net_profit_usd",
                "status": "failed",
            }
        else:
            row = {"loop": len(history), "config": fields, "error": None, "status": "ok"}
            row.update(proposals.compact_result(result))
    history.append(row)
    _write_history(history_path, history)
    seen = _seen(history)
    chosen = None
    updated = text
    shake_reason = proposals.flat_window(history)
    prompt_error = error or row.get("error")
    if shake_reason:
        print(f"    shake: {shake_reason}")
        prompt_error = f"{error} {shake_reason}".strip() if error else shake_reason
    num_ctx = int(cfg.RAW_CURVES_NUM_CTX) if getattr(cfg, "RAW_CURVES_IN_PROMPT", False) else None
    extra = _curves_extra(root, error)
    user = proposals.prompt(history, fields, prompt_error, logs, planned=planned, extra=extra)
    _warn_prompt_size(user, num_ctx)
    if models is None:
        models = debate.resolve_models(propose=model, interactive=False)
    model_name = models["propose"]
    log_path = logs / "debate.json"
    for attempt in range(1, cfg.MAX_FIX + 1):
        parsed = proposals.ask_model(model_name, user, cfg.OLLAMA_TIMEOUT_S, num_ctx=num_ctx)
        if parsed is None and model_name != cfg.FALLBACK_MODEL:
            parsed = proposals.ask_model(cfg.FALLBACK_MODEL, user, cfg.OLLAMA_TIMEOUT_S, num_ctx=num_ctx)
        if parsed is None:
            print(f"    no model JSON (attempt {attempt})")
            continue
        if parsed.get("stop") is True:
            if proposals.stop_allowed(history, planned):
                return _stop(logs, history_path, str(parsed.get("reason") or "Model stopped."))
            print(
                f"    stop ignored until {proposals.rounds_before_stop(planned)} scored rounds "
                f"({proposals.scored_rounds(history)} so far)"
            )
            user = proposals.prompt(
                history,
                fields,
                "Stop is not allowed yet. Propose a different MODIFY config.",
                logs,
                planned=planned,
                extra=extra,
            )
            _warn_prompt_size(user, num_ctx)
            continue
        try:
            updated = rules.apply_changes(text, parsed.get("config") or {}, proposals.USABLE)
            candidate = rules.resolved_fields(rules.current_fields(updated), proposals.USABLE)
            key = json.dumps(candidate, sort_keys=True)
            if key in seen:
                raise rules.ConfigError("This config is already in history.")
            seen.add(key)
        except rules.ConfigError as exc:
            print(f"    rejected (attempt {attempt}): {exc}")
            user = proposals.prompt(history, fields, str(exc), logs, planned=planned, extra=extra)
            _warn_prompt_size(user, num_ctx)
            continue
        print(f"    proposal: {json.dumps(candidate)}")
        if shake_reason and not proposals.is_shake(fields, candidate):
            print("    too small for a flat window")
            user = proposals.prompt(
                history,
                fields,
                shake_reason + " The last proposal only nudged the current neighborhood.",
                logs,
                planned=planned,
                extra=extra,
            )
            _warn_prompt_size(user, num_ctx)
            continue
        if shake_reason:
            print("    shake kept; verdict skipped")
            proposals.record_shake(logs / "shakes.jsonl", shake_reason, candidate)
            chosen = candidate
            break
        reason = _reject_reason(models, fields, candidate, history, log_path)
        if reason is None:
            chosen = candidate
            break
        history.append({"loop": row["loop"], "config": candidate, "status": "rejected", "error": reason})
        _write_history(history_path, history)
        user = proposals.prompt(history, fields, reason, logs, planned=planned, extra=extra)
        _warn_prompt_size(user, num_ctx)
    if chosen is None:
        alt = proposals.shake(fields, seen) if shake_reason else None
        if alt is not None:
            print(f"    shake config: {json.dumps(alt)}")
            proposals.record_shake(logs / "shakes.jsonl", shake_reason, alt)
            updated = rules.apply_changes(text, alt, proposals.USABLE)
            chosen = rules.resolved_fields(rules.current_fields(updated), proposals.USABLE)
        else:
            alt = proposals.fallback(fields, seen)
            if alt is None:
                raise SystemExit("No unseen config left.")
            print(f"    fallback config: {json.dumps(alt)}")
            reason = _reject_reason(models, fields, alt, history, log_path)
            if reason is not None:
                history.append({"loop": row["loop"], "config": alt, "status": "rejected", "error": reason})
                _write_history(history_path, history)
                if proposals.stop_allowed(history, planned):
                    return _stop(logs, history_path, f"Verdict rejected every proposal, including the fallback. {reason}")
                print(
                    f"    cooldown keeps the fallback until {proposals.rounds_before_stop(planned)} scored rounds "
                    f"({proposals.scored_rounds(history)} so far)"
                )
            updated = rules.apply_changes(text, alt, proposals.USABLE)
            chosen = rules.resolved_fields(rules.current_fields(updated), proposals.USABLE)
    out = Path(config_out) if config_out is not None else config_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(updated, encoding="utf-8")
    print(f"CONFIG={out}")
    print(f"HISTORY={history_path}")
    print(f"NEXT={json.dumps(chosen)}")
    return out
