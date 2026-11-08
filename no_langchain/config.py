# -*- coding: utf-8 -*-
"""Ligand-free PHREEQC settings for the loop without LangChain.

This folder's scripts load this file. engine/config.py is only the seed for
run_sequence.py. The agent may change only the names in MODIFY. Prices and
penalties stay fixed unless you edit them here. There is no ligand field.
"""
from __future__ import annotations

# --- Feed draw (not modified by the agent) ------------------------------------
# Mean and standard deviation of Li moles per mole of Ni+Co+Mn.
# engine/01_feed_panel/feed_panel.py does not read this name.
# That file sets li_ratio from the sulfate recipe: 2 * Li_salt_mol / (Ni+Co+Mn).
LI_M_RATIO: tuple[float, float] = (1.02, 0.05)
# Rows in blackmass_compositions_<anion>.csv. Row 1 is the fixed recipe.
# Each later row is one draw from SEED. 15 is the recipe plus 14 draws.
PANEL_N = 30
# Source-row count before the panel cut in the ligand feed.
# This loop's feed_panel does not read it. Extra rows here are PANEL_N - 1.
SOURCE_N = 200
# Numpy seed for the extra rows in feed_panel._samples.
# A fixed seed makes those draws the same on every case.
SEED = 42
# Ionic-strength cap, mol/kg, for the ligand panel screen.
# This loop's feed_panel does not read it.
PANEL_MU_MAX = 3.0
# salt_reference text for a chloride panel. feed_panel._as_chloride copies it onto each row.
CHLORIDE_REFERENCE = (
    "Same metal-atom count as the sulfate recipe in feed_panel. "
    "Fe is FeCl3·6H2O because the deck treats iron as Fe(III)."
)

# --- Fields the agent may change ----------------------------------------------
# Local box from run 20261025_090233. Seed is the best net profit:
# 70 C, sulfate, 0.5 kg, batch, NaOH 4.5 mol, Na2CO3 2.0 mol.
# Celsius. Deck hydroxide phases carry delta_h so this is not locked at 25.
TEMP_C = 25.0
# Kilograms of water in SOLUTION. Salt moles stay absolute.
WATER_KG = 1
# sulfate or chloride. Both stay open. Chloride at 85 C had the highest revenue.
ANION = "sulfate"
# batch: all black mass at the start.
# staged: BM_BATCHES equal doses during stage 1.
# continuous: black mass co-fed with reagent 1 over STEPS_1.
BM_ADD = "batch"
BM_BATCHES = 1
# 1 = add AMOUNT_1 only. 2 = add all of AMOUNT_1, filter, then add AMOUNT_2.
STAGE = 2
REAGENT_1 = "NaOH"
# Moles of reagent 1 added to the solution (not mol/kg).
AMOUNT_1 = 4.5
ADD_1 = "continuous"
STEPS_1 = 20
REAGENT_2 = "Na2CO3"
AMOUNT_2 = 2.0
ADD_2 = "continuous"
STEPS_2 = 10

# sit.dat is the PHREEQC database. The agent cannot change this.
DATABASE = "sit"

MODIFY = (
    "TEMP_C",
    "WATER_KG",
    "ANION",
    "BM_ADD",
    "BM_BATCHES",
    "STAGE",
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
NEUTRALIZE_ACID_USD_PER_MOL_H = 0.005
# Electricity, USD per kJ. 0.10 USD/kWh = 2.8e-5 USD/kJ.
ENERGY_USD_PER_KJ = 2e-4
CP_WATER_KJ_KG_K = 4.184
# Process penalty for driving the pH up, USD per pH unit per kg water.
PH_ENERGY_USD_PER_PH_KG = 0.005
# USD charged for each black-mass dose and each reagent dose.
STEP_PENALTY_USD = 5e-5
# Extra USD when stage 2 filters the stage-1 cake.
STAGE2_FILTRATION_FIXED_COST_USD = 0.05

# --- Agent loop ---------------------------------------------------------------
OLLAMA = "http://127.0.0.1:11435/api/chat"
MODEL = "qwen30b:latest"
FALLBACK_MODEL = "openbmb/minicpm5-2b:2b"
# Chat model that writes the next MODIFY config and defends it.
# CLI --propose-model overrides this. Use a chat model, not an embedding model.
PROPOSE_MODEL = "nemotron4b:latest"
# Chat model that attacks the proposal. CLI --adversary-model overrides this.
ADVERSARY_MODEL = "gemma4:latest"
# Second pair. None uses only the first proposer and adversary.
# These two read the first debate and the latest history result, then argue again.
PROPOSE_MODEL_2 = "ornith-1.5:9b"
ADVERSARY_MODEL_2 = "qwen30b:latest"
# Chat model that returns keep or reject. This is the verdict.
# CLI --verdict-model overrides this.
VERDICT_MODEL = 'openbmb/minicpm-v4.7:latest'
OLLAMA_TIMEOUT_S = 180
DEFAULT_ROUNDS = 2
# Minimum scored cases before a model stop. DEFAULT_ROUNDS still finishes.
STOP_COOLDOWN_ROUNDS = 3
# After this many scored cases, a flat window forces a large config change.
SHAKE_EVERY = 3
# USD. The window is flat when its best net profit is below the previous best plus this gain.
SHAKE_MIN_GAIN_USD = 0.05
# True sends 05_S01/curves.csv to the config proposer only.
# Debate models do not read it. A failed case keeps the previous file, so it is not sent.
RAW_CURVES_IN_PROMPT = True
# Ollama context tokens for the proposer when RAW_CURVES_IN_PROMPT is True.
RAW_CURVES_NUM_CTX = 8192
# After this many scored runs, models see a history summary.
HISTORY_SUMMARY_AFTER = 10
# Keep this many highest and this many lowest net-profit runs in that summary.
HISTORY_SUMMARY_EACH = 3
MAX_FIX = 3
ERROR_NAME = "error.json"
TEMP_C_MIN = 15.0
TEMP_C_MAX = 80.0
WATER_KG_MIN = 0.25
WATER_KG_MAX = 3.0
# Shared cap for readers that still use one amount limit.
AMOUNT_MAX = 10.0
AMOUNT_1_MIN = 1.0
AMOUNT_1_MAX = 10.0
AMOUNT_2_MIN = 0.0
AMOUNT_2_MAX = 10.0
STEPS_MAX = 200
STEPS_1_MIN = 1
STEPS_1_MAX = 200
STEPS_2_MIN = 1
STEPS_2_MAX = 200
# Staged black mass needs 2 or more doses. Batch still forces this field to 1.
BM_BATCHES_MAX = 10
# Full legal sets. A one-item tuple locks that field.
BM_ADD_CHOICES = ("batch", "staged", "continuous")
STAGE_CHOICES = (1, 2)
# No REAGENT_*_CHOICES: every usable reagent is legal.
ADD_1_CHOICES = ("once", "continuous")
ADD_2_CHOICES = ("once", "continuous")
