# -*- coding: utf-8 -*-
"""Write reagent thermo JSON for the current case config."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> int:
    return common.run_step("Write reagent thermo JSON.", common.case_steps.step_thermo)


if __name__ == "__main__":
    raise SystemExit(main())
