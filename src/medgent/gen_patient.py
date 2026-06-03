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


# Conditions oriented toward what an Indian tertiary-care hospital actually sees a lot
# of in adult medicine wards, so the synthetic charts read plausibly like patient_2.pdf.
CONDITIONS = [
    "Dengue fever with thrombocytopenia",
    "Acute viral hepatitis (probable Hepatitis E)",
    "Enteric fever (Typhoid) with fever spikes",
    "Acute gastroenteritis with dehydration and AKI",
    "Acute exacerbation of COPD with type 2 respiratory failure",
    "Diabetic ketoacidosis (DKA) in known T2DM",
    "Community-acquired pneumonia in elderly",
    "Acute pyelonephritis with sepsis",
    "Lower-segment cesarean section, post-op day 2",
    "Acute coronary syndrome (NSTEMI) — medically managed",
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
You generate a synthetic adult inpatient chart mirroring the typed pages of an Indian
tertiary-care hospital (the same style as the assignment's provided patient PDF). ALL
content is fictional. Do not use real patient identifiers.

LOCALE — non-negotiable:
- Indian patient name (e.g. "Ramesh K. Reddy, 56/M", "Sunita Mehra, 42/F"). Use the
  X/M or X/F notation typical on Indian charts.
- Dates in DD/MM/YY format (e.g. "28/02/26").
- Drug names are Indian BRAND names from this set whenever possible, written ALL-CAPS
  with a TAB / INJ / SYP / CAP prefix as on Indian prescriptions:
    Antibiotics: TAB AUGMENTIN, TAB CLAVAM, INJ MONOCEF, INJ TAZACT, INJ MEROMAC,
      TAB AZITHRAL, TAB CIPLOX, TAB OFLOX-TZ, TAB METROGYL, TAB DOXY-1
    PPI / antacid: TAB PAN-40, TAB RACIPER, TAB OMEZ, TAB PANTOSEC, SYP MUCAINE
    Antiemetic: INJ EMESET, TAB EMESET, TAB PERINORM
    GI: TAB MEFTAL-SPAS, TAB LOPIRAMIDE, TAB ENTROL, SYP CREMAFFIN
    Analgesic / antipyretic: TAB DOLO-650, TAB CROCIN, TAB ULTRACET, TAB BRUFEN, INJ VOVERAN
    Cardio: TAB ECOSPRIN, TAB CLOPILET, TAB ATORVA, TAB ROSULIP, TAB AMLOKIND,
      TAB METOLAR, TAB CARDACE, TAB TELMA, TAB TELMA-H, INJ LASIX, TAB DYTOR
    Diabetes: TAB GLYCOMET, TAB GLIMER, INJ INSUGEN, INJ LANTUS
    Respiratory: TAB MONTAIR, NEB DUOLIN, NEB BUDECORT, TAB DERIPHYLLIN, TAB ASTHALIN
    Endocrine: TAB THYRONORM, TAB ELTROXIN
    Steroid: INJ SOLU-MEDROL, TAB WYSOLONE, TAB OMNACORTIL
    Vitamins: TAB BECOSULES, TAB ZINCOVIT, TAB M-STRONG
- Frequency notation: 1-0-0 (morning) / 0-0-1 (night) / 1-0-1 (BID) / 1-1-1 (TDS) /
  SOS (PRN) / HS (bedtime) / STAT (immediate).
- Vital signs in Indian shorthand: PR 88/min, BP 130/80 mmHg, RR 20/min, SpO2 98%
  at room air, Temp 99.4°F afebrile, GCS 15/15.
- Use IRDAI-mandated discharge-summary section headers (ALL CAPS with colons):
  DIAGNOSIS:, CHIEF COMPLAINTS:, HISTORY OF PRESENT ILLNESS:, PAST HISTORY:,
  PERSONAL HISTORY:, GENERAL EXAMINATION:, SYSTEMIC EXAMINATION:, INVESTIGATIONS:,
  COURSE IN THE HOSPITAL:, CONDITION AT DISCHARGE:, ADVICE ON DISCHARGE:,
  FOLLOW-UP INSTRUCTIONS:.
- Hospital name a generic fictional Indian one (e.g. "Apollo Speciality Hospital",
  "Care Multispeciality Hospital", "Sunshine Medical Centre").

STRUCTURE per section (synthesise each section field as typed clinical text):
- admission_note: starts with hospital header, patient demographics, ADMISSION DATE,
  followed by DIAGNOSIS / CHIEF COMPLAINTS / HOPI / PAST HISTORY / EXAMINATION /
  INVESTIGATIONS (reports awaited). 300-400 words.
- progress_note_day_1, progress_note_day_2: SOAP-style with date, vitals row, lab
  values updated, current meds with frequencies, plan. 150-220 words each.
- lab_report: tabular-style listing with sample collection date, parameter, value,
  units, reference range. INCLUDE the planted pending lab here.
- medication_record: two clearly labelled lists — ADMISSION MEDICATIONS and
  DISCHARGE MEDICATIONS. Use brand names from the catalog above. Indicate dose,
  route, frequency (1-0-0 style), duration.
- discharge_advice: DIAGNOSIS (numbered final list), CONDITION AT DISCHARGE,
  ADVICE ON DISCHARGE (medication table mirroring the real patient: No, Medication
  Name, Dosage, Frequency, Duration), FOLLOW-UP INSTRUCTIONS, PENDING RESULTS.

MESSINESS — inject exactly THREE items and list each in `injected_messiness`:
  1. ONE lab result explicitly "pending" / "awaited" at discharge.
  2. ONE medication change between admission and discharge with NO documented
     reason in any progress note.
  3. ONE conflicting diagnosis — admission's assessment vs discharge's diagnosis
     disagree on ONE secondary dx (e.g. admission says "Rule out X", discharge
     lists X as confirmed without intervening evidence in the notes).

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
    def _safe_para(s: str, max_word: int = 30) -> str:
        """Replace non-ASCII chars and split very long words so fpdf's WORD wrap doesn't choke."""
        s = s.encode("ascii", "replace").decode()
        return " ".join(
            (w if len(w) <= max_word else " ".join(w[i : i + max_word] for i in range(0, len(w), max_word)))
            for w in s.split()
        )

    for title, body in page_order:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(
            0, 7,
            text=_safe_para(f"{title}  -  Patient {sections.patient_id}  -  {sections.condition}"),
            new_x="LMARGIN", new_y="NEXT",
        )
        pdf.ln(1)
        pdf.set_font("Helvetica", "", 10)
        for para in body.split("\n"):
            para = para.strip()
            if not para:
                pdf.ln(2)
                continue
            safe = _safe_para(para)
            try:
                pdf.multi_cell(0, 5, text=safe, new_x="LMARGIN", new_y="NEXT")
            except Exception:
                pdf.multi_cell(0, 5, text=safe[:500] + " ...", new_x="LMARGIN", new_y="NEXT")

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
