# -*- coding: utf-8 -*-
"""Shared CLI for the no-LangChain step scripts.

The crew canvas appends wired file paths as positional arguments.
Every step accepts those paths so argparse does not reject config.py.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

LOOP = Path(__file__).resolve().parents[1]
ENGINE = LOOP.parent / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))
# This folder's config.py must win over engine/config.py.
if str(LOOP) not in sys.path:
    sys.path.insert(0, str(LOOP))

import case_steps  # noqa: E402

HERE = Path(__file__).resolve().parent


def _work_dir() -> Path:
    """Case folder inside this no_langchain folder.

    The path is relative to this file. It does not use a Desktop path.
    """
    return LOOP / "work"


WORK = _work_dir()

CASE_FOLDERS = ("01_feed_panel", "02_ask_thermo", "03_make_phreeqc_deck", "04_run_phreeqc", "05_S01")
AGENT_FILES = (
    "history.jsonl",
    "debate.json",
    "debate.jsonl",
    "shakes.jsonl",
    "stop.txt",
    "history_summary.json",
    "run_log.json",
    "optimized.json",
    "report.html",
)


def _move_entry(src: Path, dest: Path) -> None:
    """Move one file or folder. Leave the source in place when the move fails."""
    if not src.exists() or dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dest))
    except OSError:
        return


def loop_case(root: Path, number: int | None = None) -> Path:
    """Case folder inside loops/.

    The canvas reuses loops/loop. A numbered run uses loops/loop0, loops/loop1, ...
    A flat case (01–05 beside the work root) is moved into loops/loop once.
    """
    root = Path(root)
    name = "loop" if number is None else f"loop{number}"
    dest = root / "loops" / name
    flat = number is None and not (root / "loops").exists() and (root / "config.py").is_file()
    dest.mkdir(parents=True, exist_ok=True)
    if flat or (number is None and (root / "01_feed_panel").is_dir() and not (dest / "01_feed_panel").exists()):
        _move_entry(root / "config.py", dest / "config.py")
        for folder in CASE_FOLDERS:
            _move_entry(root / folder, dest / folder)
    return dest


def agent_dir(root: Path) -> Path:
    """One folder for history, debate, shakes, stop, and the run report."""
    root = Path(root)
    dest = root / "agent"
    dest.mkdir(parents=True, exist_ok=True)
    for name in AGENT_FILES:
        _move_entry(root / name, dest / name)
    return dest


def parser(description: str) -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=description)
    cli.add_argument("--work", type=Path, default=None, help="Case folder. Default: no_langchain/work.")
    cli.add_argument("--config", type=Path, default=None, help="Seed config.py. Used only by 00_load_config.py.")
    cli.add_argument("inputs", nargs="*", type=Path, help="Paths wired from the crew canvas.")
    return cli


def work_dir(args: argparse.Namespace) -> Path:
    return Path(args.work) if args.work else WORK


def seed_source(args: argparse.Namespace) -> Path:
    if args.config is not None:
        return Path(args.config)
    for path in args.inputs:
        if path.name == "config.py" and path.is_file():
            return path
    bundled = LOOP / "config.py"
    if bundled.is_file():
        return bundled
    raise SystemExit("No config.py on the command line and no_langchain/config.py is missing.")


def run_step(description: str, fn) -> int:
    args = parser(description).parse_args()
    root = loop_case(work_dir(args))
    fn(root)
    print(f"WORK={root}")
    return 0
