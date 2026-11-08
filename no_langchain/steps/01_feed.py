# -*- coding: utf-8 -*-
"""Write the black-mass feed CSV for the current case config."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def main() -> int:
    return common.run_step("Write the black-mass feed CSV.", common.case_steps.step_feed)


if __name__ == "__main__":
    raise SystemExit(main())
