"""Runtime config. Loads from env (and .env if python-dotenv is around) so we keep keys out of code."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[2]

# Vertex AI
GCP_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "synth-hackathon-2026")
GCP_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# Models
MODEL_PRO = os.environ.get("MEDGENT_MODEL_PRO", "gemini-2.5-pro")
MODEL_FLASH = os.environ.get("MEDGENT_MODEL_FLASH", "gemini-2.5-flash")

# Agent caps (brief requirement #9)
MAX_ITERATIONS = int(os.environ.get("MEDGENT_MAX_ITERATIONS", "60"))
MAX_TOOL_CALLS_PER_FIELD = int(os.environ.get("MEDGENT_MAX_TOOL_CALLS_PER_FIELD", "8"))
MAX_TOTAL_TOOL_CALLS = int(os.environ.get("MEDGENT_MAX_TOTAL_TOOL_CALLS", "200"))

# PDF rendering
PDF_RENDER_DPI = int(os.environ.get("MEDGENT_PDF_DPI", "150"))

# Drug-interaction mock failure rate (for exercising robustness in demo)
DRUG_TOOL_FAIL_PROB = float(os.environ.get("MEDGENT_DRUG_TOOL_FAIL_PROB", "0.10"))

# Encounter clustering: pages within N days are same encounter
ENCOUNTER_DAYS_WINDOW = int(os.environ.get("MEDGENT_ENCOUNTER_DAYS_WINDOW", "10"))

# Citation confidence floor for FILLED (anything below → must FLAG)
HANDWRITING_FILLED_FLOOR = float(os.environ.get("MEDGENT_HANDWRITING_FLOOR", "0.65"))

# Paths
DATA_DIR = ROOT / "data"
PATIENTS_DIR = DATA_DIR / "patients"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
OUTPUTS_DIR = ROOT / "outputs"
TRACES_DIR = OUTPUTS_DIR / "traces"
DRAFTS_DIR = OUTPUTS_DIR / "drafts"
INDEX_DIR = OUTPUTS_DIR / "index"
PAGES_DIR = ROOT / ".pages"


def ensure_dirs() -> None:
    for d in (OUTPUTS_DIR, TRACES_DIR, DRAFTS_DIR, INDEX_DIR, PAGES_DIR, SYNTHETIC_DIR):
        d.mkdir(parents=True, exist_ok=True)
