# -*- coding: utf-8 -*-
"""Score the case: 7-metal cuts, 0.70 purity gate, and net profit."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> int:
    return common.run_step("Score recovery, purity, and net profit.", common.case_steps.step_score)


if __name__ == "__main__":
    raise SystemExit(main())
