# -*- coding: utf-8 -*-
"""Ligand-free economic loop without LangChain.

The crew runs ``steps/00`` through ``steps/07`` and loops the last tile back
to the first. This file is the same chain for a terminal:

    python run_econ_loop.py --rounds 2 --propose-model ornith-1.5:9b --verdict-model gemma4:latest
    python run_econ_loop.py --ask
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

LOOP = Path(__file__).resolve().parent
ENGINE = LOOP.parent / "engine"
STEPS = LOOP / "steps"
sys.path.insert(0, str(ENGINE))
sys.path.insert(0, str(LOOP))
sys.path.insert(0, str(STEPS))

import case_steps  # noqa: E402
import config as cfg  # noqa: E402
import config_rules as rules  # noqa: E402
import debate  # noqa: E402
import proposals  # noqa: E402
import propose  # noqa: E402


def _loop_dirs(run_dir: Path) -> list[Path]:
    found: list[tuple[int, Path]] = []
    loops = run_dir / "loops"
    if not loops.is_dir():
        return []
    for path in loops.iterdir():
        if path.is_dir() and path.name.startswith("loop") and path.name[4:].isdigit():
            found.append((int(path.name[4:]), path))
    found.sort()
    return [path for _, path in found]


def _report(run_dir: Path, models: dict[str, str], logs: Path) -> Path | None:
    """Write agent/run_log.json and agent/report.html."""
    history: list[dict] = []
    history_path = logs / "history.jsonl"
    if history_path.is_file():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                history.append(json.loads(line))
    cases = _loop_dirs(run_dir)
    case = cases[-1] if cases else run_dir
    result_path = case / "05_S01" / "econ_result.json"
    if result_path.is_file() and (case / "config.py").is_file():
        result = json.loads(result_path.read_text(encoding="utf-8"))
        text = (case / "config.py").read_text(encoding="utf-8")
        fields = rules.resolved_fields(rules.current_fields(text), proposals.USABLE)
        recorded = any(
            row.get("status") in {"ok", "failed"} and row.get("config") == fields
            for row in history
        )
        if not recorded:
            if result.get("net_profit_usd") is None:
                history.append(
                    {
                        "loop": len(history),
                        "config": fields,
                        "error": "score missing net_profit_usd",
                        "status": "failed",
                    }
                )
            else:
                row = {"loop": len(history), "config": fields, "error": None, "status": "ok"}
                row.update(proposals.compact_result(result))
                history.append(row)
    loops = [row for row in history if row.get("status") != "rejected"]
    if not loops:
        return None
    ok = [row for row in loops if row.get("status") == "ok" and row.get("net_profit_usd") is not None]
    best = max(ok, key=lambda row: row["net_profit_usd"]) if ok else None
    stop_path = logs / "stop.txt"
    stop_reason = stop_path.read_text(encoding="utf-8").strip() if stop_path.is_file() else None
    summary = (
        f"Best loop {best['loop']} net profit {best['net_profit_usd']:.4f} USD."
        if best
        else "No loop produced a result."
    )
    log = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "model": models.get("propose"),
        "verdict_model": models.get("verdict"),
        "adversary_model": models.get("adversary"),
        "propose_model_2": models.get("propose_2"),
        "adversary_model_2": models.get("adversary_2"),
        "stop_reason": stop_reason,
        "best_loop": None if best is None else best["loop"],
        "summary": summary,
        "loops": loops,
    }
    (logs / "run_log.json").write_text(json.dumps(log, indent=2) + "\n", encoding="utf-8")
    import importlib.util

    report_path = ENGINE / "report_html.py"
    spec = importlib.util.spec_from_file_location("econ_report_html", report_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load {report_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(run_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Economic PHREEQC loop without LangChain.")
    parser.add_argument("--rounds", type=int, default=cfg.DEFAULT_ROUNDS)
    parser.add_argument("--model", default=None, help="Proposer model. Same as --propose-model.")
    parser.add_argument("--propose-model", default=None, help="Chat model that writes and defends the next config.")
    parser.add_argument("--adversary-model", default=None, help="Chat model that attacks the next config.")
    parser.add_argument("--verdict-model", default=None, help="Chat model that returns keep or reject.")
    parser.add_argument("--ask", action="store_true", help="Ask which proposer model and which verdict model to use.")
    parser.add_argument("--dir", type=Path, default=None)
    parser.add_argument("inputs", nargs="*", type=Path, help="Wired paths. A config.py file is the seed.")
    args = parser.parse_args(argv)
    propose_name = args.propose_model or args.model
    models = debate.resolve_models(propose_name, args.verdict_model, args.adversary_model, args.ask)
    run_dir = args.dir or (Path(__file__).resolve().parent / "runs" / datetime.now().strftime("%Y%m%d_%H%M%S"))
    source = LOOP / "config.py"
    for path in args.inputs:
        if path.name == "config.py" and path.is_file():
            source = path
            break
    import common

    logs = common.agent_dir(run_dir)
    last_ok = False
    last_case = run_dir
    for number in range(0, args.rounds + 1):
        stop_path = logs / "stop.txt"
        if stop_path.is_file():
            print(stop_path.read_text(encoding="utf-8").strip())
            break
        case = common.loop_case(run_dir, number)
        last_case = case
        if number == 0:
            case_steps.seed_config(case, source)
        print(f"\n=== loop {number}")
        print(f"LOOP_DIR={case}")
        case_error = None
        try:
            case_steps.run_all(case)
        except (SystemExit, Exception) as exc:
            case_error = exc.code if isinstance(exc, SystemExit) and isinstance(exc.code, str) else str(exc)
            case_error = case_error.strip() or exc.__class__.__name__
            print(f"    failed: {case_error}")
        result_path = case / "05_S01" / "econ_result.json"
        last_ok = case_error is None and result_path.is_file()
        if number >= args.rounds:
            if case_error:
                propose.record_failure(case, case_error, agent_dir=logs)
            break
        propose.propose_once(
            case,
            models=models,
            planned=args.rounds,
            error=case_error,
            agent_dir=logs,
            config_out=run_dir / "loops" / f"loop{number + 1}" / "config.py",
        )
    print(f"\nRun folder: {run_dir}")
    report = _report(run_dir, models, logs)
    if report is not None:
        print(f"Report: {report}")
    if last_ok:
        result = json.loads((last_case / "05_S01" / "econ_result.json").read_text(encoding="utf-8"))
        print(f"net_profit_usd {result.get('net_profit_usd')}")
    return 0 if last_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
