# -*- coding: utf-8 -*-
"""Write the stage-1 PHREEQC deck."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> int:
    return common.run_step("Write the stage-1 PHREEQC deck.", common.case_steps.step_stage1_deck)


if __name__ == "__main__":
    raise SystemExit(main())
