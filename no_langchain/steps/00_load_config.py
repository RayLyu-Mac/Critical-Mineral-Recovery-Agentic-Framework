# -*- coding: utf-8 -*-
"""Seed the case config. Keeps an existing case config so the canvas loop can update it.

    python 00_load_config.py C:\\path\\to\\config.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> int:
    args = common.parser("Seed the ligand-free case config.").parse_args()
    root = common.loop_case(common.work_dir(args))
    dest = common.case_steps.seed_config(root, common.seed_source(args))
    print(f"WORK={root}")
    print(f"CONFIG={dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
