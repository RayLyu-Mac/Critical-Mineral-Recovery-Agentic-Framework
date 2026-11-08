# -*- coding: utf-8 -*-
"""Run one PHREEQC deck. The database is engine/ref/data/sit.dat."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from paths import SIT_DAT  # noqa: E402


def find_phreeqc() -> Path:
    env = os.environ.get("PHREEQC_EXE") or os.environ.get("PATH_PHREEQC") or ""
    names = [
        env,
        str(Path(os.environ.get("PHREEQC_ROOT", "")) / "bin" / "Release" / "phreeqc.exe"),
        str(Path(os.environ.get("PHREEQC_ROOT", "")) / "bin" / "phreeqc.exe"),
        str(Path.home() / "phreeqc-3.8.6-extract" / "phreeqc-3.8.6-17100-x64" / "bin" / "Release" / "phreeqc.exe"),
        str(Path.home() / "phreeqc-3.8.6-extract" / "phreeqc-3.8.6-17100-x64" / "bin" / "phreeqc.exe"),
        r"C:\Program Files\USGS\phreeqc-3.8.6-17100-x64\bin\Release\phreeqc.exe",
        r"C:\Program Files\USGS\phreeqc-3.8.6-17100-x64\bin\phreeqc.exe",
        r"C:\Program Files (x86)\USGS\phreeqc-3.8.6-17100-x64\bin\Release\phreeqc.exe",
    ]
    for name in names:
        if name and Path(name).is_file():
            return Path(name)
    found = shutil.which("phreeqc") or shutil.which("phreeqc.exe")
    if found:
        return Path(found)
    raise SystemExit("phreeqc executable not found. Set PHREEQC_EXE.")


def run_deck(phr: Path, dest: Path) -> Path:
    if not phr.is_file():
        raise SystemExit(f"Deck not found: {phr}")
    if not SIT_DAT.is_file():
        raise SystemExit(f"sit.dat not found: {SIT_DAT}")
    dest.mkdir(parents=True, exist_ok=True)
    exe = find_phreeqc()
    out_log = dest / f"{phr.stem}.out"
    proc = subprocess.run(
        [str(exe), str(phr.resolve()), str(out_log.resolve()), str(SIT_DAT)],
        cwd=str(dest),
        capture_output=True,
        text=True,
    )
    log_text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    (dest / f"{phr.stem}.log").write_text(log_text, encoding="utf-8")
    if proc.returncode != 0:
        tail = "\n".join(log_text.strip().splitlines()[-12:])
        raise SystemExit(f"PHREEQC failed on {phr.name} (code {proc.returncode}).\n{tail}")
    if not out_log.is_file():
        raise SystemExit(f"PHREEQC wrote no output log for {phr.name}.")
    return out_log
