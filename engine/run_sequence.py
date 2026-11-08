# -*- coding: utf-8 -*-
"""Run one ligand-free PHREEQC case and write econ_result.json.

    python run_sequence.py
    python run_sequence.py --config path\\to\\config.py --out path\\to\\run_folder

Prints RUN_ROOT= and ECON_JSON= on success.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(ENGINE))

import case_steps  # noqa: E402


def run(config_path: Path | None = None, out_root: Path | None = None) -> Path:
    if out_root is None:
        root = ENGINE / "run" / datetime.now().strftime("%Y%m%d_%H%M%S")
    else:
        root = Path(out_root)
        if root.exists() and any(root.iterdir()):
            root = root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    source = Path(config_path) if config_path else ENGINE / "config.py"
    case_steps.seed_config(root, source)
    case_steps.run_all(root)
    return root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one ligand-free economic PHREEQC case.")
    parser.add_argument("--config", type=Path, help="config.py. Default: engine/config.py.")
    parser.add_argument("--out", type=Path, help="Run folder. Default: engine/run/<time>/.")
    parser.add_argument("inputs", nargs="*", type=Path, help="Wired paths. A config.py file is used when --config is omitted.")
    args = parser.parse_args(argv)
    config = args.config
    if config is None:
        for path in args.inputs:
            if path.name == "config.py":
                config = path
                break
    run(config, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
