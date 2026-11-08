# -*- coding: utf-8 -*-
"""Ligand-free PHREEQC settings and techno-economic constants.

Edit this file before run_sequence.py. The agent loops may change only the
names in MODIFY. Prices and penalties stay fixed unless you edit them here.
There is no ligand field.
"""
from __future__ import annotations

# --- Feed draw (not modified by the agent) ------------------------------------
LI_M_RATIO: tuple[float, float] = (1.02, 0.05)
PANEL_N = 1
SOURCE_N = 200
SEED = 42
PANEL_MU_MAX = 3.0
CHLORIDE_REFERENCE = (
    "Same metal-atom count as the sulfate recipe in feed_panel. "
    "Fe is FeCl3·6H2O because the deck treats iron as Fe(III)."
)

# --- Fields the agent may change ----------------------------------------------
# Celsius. Deck hydroxide phases carry delta_h so this is not locked at 25.
TEMP_C = 25.0
# Kilograms of water in SOLUTION. Salt moles stay absolute.
WATER_KG = 1.0
# sulfate or chloride.
ANION = "sulfate"
# batch: all black mass at the start.
# staged: BM_BATCHES equal doses during stage 1.
# continuous: black mass co-fed with reagent 1 over STEPS_1.
BM_ADD = "batch"
BM_BATCHES = 1
# 1 = one reagent sweep. 2 = stop reagent 1 at PH_SPLIT, filter, then reagent 2.
STAGE = 1
# pH that splits impurity cuts (Al, Fe, Cu) from value cuts (Ni, Co, Mn).
PH_SPLIT = 12.0
REAGENT_1 = "Na2CO3"
# Moles of reagent 1 added to the solution (not mol/kg).
AMOUNT_1 = 1.0
ADD_1 = "continuous"
STEPS_1 = 1
REAGENT_2 = "Na2CO3"
AMOUNT_2 = 1.0
ADD_2 = "continuous"
STEPS_2 = 1

# sit.dat is the PHREEQC database. The agent cannot change this.
DATABASE = "sit"

MODIFY = (
    "TEMP_C",
    "WATER_KG",
    "ANION",
    "BM_ADD",
    "BM_BATCHES",
    "STAGE",
    "PH_SPLIT",
    "REAGENT_1",
    "AMOUNT_1",
    "ADD_1",
    "STEPS_1",
    "REAGENT_2",
    "AMOUNT_2",
    "ADD_2",
    "STEPS_2",
)

# --- Economic constants (editable, not proposed by the agent) -----------------
# A precipitate or brine cut below this purity has zero revenue.
PURITY_THRESHOLD = 0.70
# USD per kg of recovered element. Order-of-magnitude industrial prices.
METAL_PRICE_USD_PER_KG = {
    "Co": 33.0,
    "Ni": 16.0,
    "Li": 15.0,
    "Cu": 9.5,
    "Al": 2.5,
    "Mn": 2.2,
    "Fe": 0.5,
}
# kg per mol of element.
MW_METAL_KG = {
    "Ni": 0.0586934,
    "Co": 0.0589332,
    "Mn": 0.0549380,
    "Li": 0.006941,
    "Cu": 0.063546,
    "Fe": 0.055845,
    "Al": 0.0269815,
}
# USD per mol of reagent formula as added in the deck (NaOH, Na2CO3, ...).
REAGENT_PRICE_USD_PER_MOL = {
    "NaOH": 0.016,
    "Na2CO3": 0.025,
    "NH4HCO3": 0.030,
    "Na2S": 0.080,
    "Na3PO4": 0.150,
    "KMnO4": 0.350,
    "O2": 0.010,
}
REAGENT_PRICE_DEFAULT = 0.10
# USD per mol H+ used to bring leftover alkalinity back to pH 7.
NEUTRALIZE_ACID_USD_PER_MOL_H = 0.008
# Electricity, USD per kJ. 0.10 USD/kWh = 2.8e-5 USD/kJ.
ENERGY_USD_PER_KJ = 2.8e-5
CP_WATER_KJ_KG_K = 4.184
# Process penalty for driving the pH up, USD per pH unit per kg water.
PH_ENERGY_USD_PER_PH_KG = 0.02
# USD charged for each black-mass dose and each reagent dose.
STEP_PENALTY_USD = 0.01
# Extra USD when stage 2 filters the stage-1 cake.
STAGE2_FILTRATION_FIXED_COST_USD = 0.05

# --- Agent loop ---------------------------------------------------------------
OLLAMA = "http://127.0.0.1:11435/api/chat"
MODEL = "ornith-1.5:9b"
FALLBACK_MODEL = "gemma4:latest"
OLLAMA_TIMEOUT_S = 180
DEFAULT_ROUNDS = 2
MAX_FIX = 3
ERROR_NAME = "error.json"
TEMP_C_MIN = 15.0
TEMP_C_MAX = 85.0
WATER_KG_MIN = 0.05
WATER_KG_MAX = 20.0
AMOUNT_MAX = 30.0
STEPS_MAX = 200
BM_BATCHES_MAX = 10
PH_SPLIT_MIN = 2.0
PH_SPLIT_MAX = 12.0
