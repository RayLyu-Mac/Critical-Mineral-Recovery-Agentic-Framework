# -*- coding: utf-8 -*-
"""Filter at PH_SPLIT and run stage 2. Exits 0 without a run when STAGE is 1."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> int:
    return common.run_step("Run stage 2 when STAGE is 2.", common.case_steps.step_stage2)


if __name__ == "__main__":
    raise SystemExit(main())
