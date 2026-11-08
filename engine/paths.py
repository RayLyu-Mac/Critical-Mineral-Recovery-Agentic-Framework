# -*- coding: utf-8 -*-
"""Paths for the isolated framework. Every path is relative to this file."""
from __future__ import annotations

from pathlib import Path

ENGINE = Path(__file__).resolve().parent
ROOT = ENGINE.parent
REF_DEMO = ENGINE / "ref"
REF_DATA = REF_DEMO / "data"
REF_THERMO = REF_DEMO / "02_ask_thermo" / "ask_thermo.py"
SIT_DAT = REF_DATA / "sit.dat"
OVERLAY_DAT = REF_DATA / "nist46_leachant_blocks.dat"
LANGCHAIN_CHROMA = ENGINE / "chroma_db"
