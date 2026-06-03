"""Synthetic patient generator.

Gemini Pro authors a fictional source-note bundle (admission, progress, labs, meds,
discharge advice) for a chosen condition; fpdf2 renders each section to its own page
in a single PDF. We intentionally inject three kinds of messiness so Part 1 and Part 2
have realistic test material:

  1. One pending lab result
  2. One medication change (added or stopped) with NO documented reason
  3. One conflicting diagnosis between the admission note and discharge advice

The injected_messiness list is preserved alongside each generated patient so we can
score whether the agent caught the planted issues. **Synthetic only — never used for
real patients.**
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional

from fpdf import FPDF
from pydantic import BaseModel, Field

from . import config
from .gemini_client import call_structured

log = logging.getLogger("medgent.gen_patient")


CONDITIONS = [
    "Acute COPD exacerbation",
    "Acute heart failure exacerbation",
    "Community-acquired pneumonia",
    "Acute pyelonephritis",
    "Post-operative recovery after open cholecystectomy",
    "Diabetic foot infection with cellulitis",
    "Acute stroke (ischemic, left MCA territory)",
    "Acute myocardial infarction (NSTEMI)",
    "Mechanical fall with closed head injury",
    "Sepsis secondary to urinary tract infection",
]


class SyntheticPatientSections(BaseModel):
    """Each field is the rendered TEXT of one page of the synthetic chart."""

    patient_id: str
    condition: str
    admission_note: str
    progress_note_day_1: str
    progress_note_day_2: str
    lab_report: str
    medication_record: str
    discharge_advice: str
    injected_messiness: list[str] = Field(
        description="explicit list of what we deliberately made messy/missing/conflicting"
    )


_GEN_SYSTEM = """\
You are generating a synthetic hospital chart for a fictional patient. The notes will be
used to test a discharge-summary drafting agent's ability to handle messy real-world
clinical documentation. ALL content is fictional — do not use real patient data.

Each section should look like a typed clinical note (no handwriting simulation needed).
Use realistic hospital chart phrasing, abbreviations, drug brand-names, and structure.

You MUST inject exactly three pieces of messiness, and you MUST list them in
injected_messiness so we can grade detection:

  1. ONE laboratory result that is "pending" or "awaited" at the time of discharge.
  2. ONE medication change (added or stopped between admission and discharge) for which
     NO documented reason appears anywhere in the notes.
  3. ONE conflicting diagnosis — the admission note's assessment vs. the discharge
     advice's diagnosis list should disagree about ONE secondary diagnosis (e.g.,
     "rule out X" in admission becomes "confirmed X" in discharge with no documentation
     of how it was confirmed).

Return a SyntheticPatientSections object matching the schema. No prose outside JSON.
"""


def generate_patient(condition: str, *, seed: int) -> SyntheticPatientSections:
    prompt = (
        f"Generate a synthetic patient chart for: **{condition}**. Use seed {seed} for "
        "demographic variety (age, sex, occupation). Patient id should be "
        f"`synth_{seed:02d}`. Each note should be a multi-paragraph typed clinical note "
        "with realistic structure: admission note ~250-350 words; each progress note "
        "~150-220 words; lab report a table-style listing with values; medication record "
        "showing both admission and discharge med lists clearly; discharge advice listing "
        "diagnoses, follow-up, discharge condition, and pending results."
    )
    out = call_structured(
        prompt,
        schema=SyntheticPatientSections,
        system=_GEN_SYSTEM,
        model=config.MODEL_PRO,
        temperature=0.85,
        max_output_tokens=8000,
    )
    return out if isinstance(out, SyntheticPatientSections) else SyntheticPatientSections.model_validate(out.model_dump())


# ----------------------------- PDF rendering ----------------------------------


def render_to_pdf(sections: SyntheticPatientSections, out_path: Path) -> Path:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=14)

    page_order = [
        ("ADMISSION NOTE", sections.admission_note),
        ("PROGRESS NOTE — Day 1", sections.progress_note_day_1),
        ("PROGRESS NOTE — Day 2", sections.progress_note_day_2),
        ("LABORATORY REPORT", sections.lab_report),
        ("MEDICATION RECORD", sections.medication_record),
        ("DISCHARGE ADVICE", sections.discharge_advice),
    ]
    for title, body in page_order:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(
            0, 7,
            txt=f"{title}  -  Patient {sections.patient_id}  -  {sections.condition}",
            wrapmode="CHAR",
        )
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 10)
        for para in body.split("\n"):
            para = para.strip()
            if not para:
                pdf.ln(2)
                continue
            ascii_para = para.encode("ascii", "replace").decode()
            try:
                pdf.multi_cell(0, 5, txt=ascii_para, wrapmode="CHAR")
            except Exception:
                # Last resort: truncate
                pdf.multi_cell(0, 5, txt=ascii_para[:500] + " ...", wrapmode="CHAR")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    return out_path


def generate_cohort(n: int, *, seed_start: int = 1) -> list[Path]:
    """Generate n synthetic patients spanning the CONDITIONS rotation."""
    config.ensure_dirs()
    rng = random.Random(seed_start)
    paths: list[Path] = []
    for i in range(n):
        seed = seed_start + i
        condition = CONDITIONS[i % len(CONDITIONS)]
        log.info("synth %d: %s", seed, condition)
        sec = generate_patient(condition, seed=seed)
        out_dir = config.SYNTHETIC_DIR / sec.patient_id
        out_dir.mkdir(parents=True, exist_ok=True)
        # Persist the ground-truth sidecar FIRST so we don't lose it if rendering fails
        (out_dir / "manifest.json").write_text(sec.model_dump_json(indent=2))
        try:
            pdf_path = render_to_pdf(sec, out_dir / "source.pdf")
            paths.append(pdf_path)
        except Exception as exc:
            log.error("render failed for %s: %s — manifest kept", sec.patient_id, exc)
    return paths


if __name__ == "__main__":  # quick CLI
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    paths = generate_cohort(n)
    for p in paths:
        print(p)
