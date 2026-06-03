"""The simulated 'reviewer' — a deterministic clinician with a HIDDEN, CONSISTENT
editing policy. The agent never sees this file at runtime.

The policy applies surface-level normalizations that a real reviewer might apply:

  1. Brand-only medication names → "generic (Brand)" format.
  2. Secondary diagnoses sorted by severity (descending), ties alphabetical.
  3. Vague follow-up items annotated with the relevant specialty.
  4. Pending labs/results gain an explicit "(pending as of <discharge_date>)" suffix.
  5. SAFETY FLAGS ARE NEVER EDITED — preserves the Part 1 safety floor.

The reviewer is INTENTIONALLY non-trivial and never sees the prompt the agent saw.
Its job is to leave a learnable signal for the correction-memory loop without ever
becoming a content-rewriter (it does not invent or remove clinical facts).
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Optional

from ..models import (
    DischargeSummary,
    FieldStatus,
    Medication,
    ValuedField,
)


# Tiny Indian-brand → (generic, BrandTitle) lookup. Designed to overlap meds the
# real patient PDF actually contains, plus common ones synthetic patients use.
BRAND_TO_GENERIC: dict[str, tuple[str, str]] = {
    "RACIPER": ("rabeprazole", "Raciper"),
    "EMESET": ("ondansetron", "Emeset"),
    "PAN": ("pantoprazole", "Pan"),
    "PAN-40": ("pantoprazole", "Pan-40"),
    "OFLOX TZ": ("ofloxacin + tinidazole", "Oflox-TZ"),
    "OFLOX-TZ": ("ofloxacin + tinidazole", "Oflox-TZ"),
    "MEFTAL SPAS": ("mefenamic acid + dicyclomine", "Meftal-Spas"),
    "LOPIRAMIDE": ("loperamide", "Lopiramide"),
    "ENTROL": ("racecadotril", "Entrol"),
    "ZEDOTT": ("amoxicillin + clavulanate", "Zedott"),
    "M STRONG": ("multivitamin", "M-Strong"),
    "M-STRONG": ("multivitamin", "M-Strong"),
    "CROCIN": ("paracetamol", "Crocin"),
    "DOLO": ("paracetamol", "Dolo"),
    "LASIX": ("furosemide", "Lasix"),
    "AUGMENTIN": ("amoxicillin + clavulanate", "Augmentin"),
    "TAZACT": ("piperacillin + tazobactam", "Tazact"),
    "MONOCEF": ("ceftriaxone", "Monocef"),
    "MEROMAC": ("meropenem", "Meromac"),
    "NORVASC": ("amlodipine", "Norvasc"),
    "ATORVA": ("atorvastatin", "Atorva"),
}


# Diagnosis severity rough-ordering. Higher = more severe. Used for secondary-dx sort.
SEVERITY_KEYWORDS: dict[str, int] = {
    "septic shock": 6, "sepsis": 5, "respiratory failure": 5, "shock": 5,
    "stroke": 5, "myocardial infarction": 5, "nstemi": 5, "stemi": 5,
    "dka": 5, "diabetic ketoacidosis": 5, "pulmonary embolism": 5,
    "acute kidney injury": 4, "aki": 4, "pneumonia": 4, "copd exacerbation": 4,
    "chf": 4, "heart failure": 4, "anemia": 3, "hypertension": 3, "diabetes": 3,
    "thyroid": 2, "uti": 2, "urinary tract infection": 2, "cellulitis": 3,
    "gastroenteritis": 2, "dehydration": 2,
}


# Diagnosis → specialty hint for follow-up
SPECIALTY_KEYWORDS: dict[str, str] = {
    "dka": "endocrinology", "diabetic": "endocrinology", "diabetes": "endocrinology",
    "thyroid": "endocrinology",
    "myocardial infarction": "cardiology", "nstemi": "cardiology", "stemi": "cardiology",
    "chf": "cardiology", "heart failure": "cardiology", "angina": "cardiology",
    "stroke": "neurology", "tia": "neurology", "seizure": "neurology",
    "pneumonia": "pulmonology", "copd": "pulmonology", "asthma": "pulmonology",
    "uti": "urology", "urinary tract infection": "urology", "pyelonephritis": "urology",
    "gastroenteritis": "gastroenterology", "hepatitis": "gastroenterology",
    "cellulitis": "general surgery",
}


# Vague follow-up phrases that should be specialty-annotated
_VAGUE_FOLLOWUP_PATTERNS = [
    r"^follow[\s-]?up$",
    r"^review[\s-]?up$",
    r"^review in \d+ (week|day|month)s?$",
    r"^opd review",
    r"^follow up review",
]


def _is_vague_followup(text: str) -> bool:
    t = text.strip().lower()
    return any(re.search(p, t) for p in _VAGUE_FOLLOWUP_PATTERNS)


def _infer_specialty(principal: Optional[str], secondary: Optional[list[str]]) -> Optional[str]:
    text = " ".join([principal or ""] + (secondary or [])).lower()
    for kw, spec in SPECIALTY_KEYWORDS.items():
        if kw in text:
            return spec
    return None


def _severity_of(dx: str) -> int:
    d = dx.lower()
    for kw, sev in SEVERITY_KEYWORDS.items():
        if kw in d:
            return sev
    return 1


# ----------------------------- edit operations --------------------------------


def _edit_brand_in_med(med: Medication) -> Medication:
    name = med.name_as_written
    # strip "TAB ", "INJ ", "CAP " prefix for matching
    bare = re.sub(r"^(TAB|INJ|CAP|SYP|TABLET|INJECTION)\.?\s+", "", name, flags=re.IGNORECASE).strip().upper()
    bare = re.sub(r"\s+\d+\s*MG.*$", "", bare).strip()
    if bare in BRAND_TO_GENERIC:
        generic, brand_title = BRAND_TO_GENERIC[bare]
        new = med.model_copy(deep=True)
        new.name_as_written = f"{generic} ({brand_title})"
        new.normalized_name = generic
        return new
    return med


def _edit_brand_field(vf: ValuedField) -> ValuedField:
    if vf.status != FieldStatus.FILLED or not vf.value:
        return vf
    if not isinstance(vf.value, list):
        return vf
    new_meds = []
    for item in vf.value:
        if isinstance(item, Medication):
            new_meds.append(_edit_brand_in_med(item))
        else:
            new_meds.append(item)
    vf2 = vf.model_copy(deep=True)
    vf2.value = new_meds
    return vf2


def _edit_dx_order(vf: ValuedField) -> ValuedField:
    if vf.status != FieldStatus.FILLED or not vf.value:
        return vf
    if not isinstance(vf.value, list):
        return vf
    ordered = sorted(vf.value, key=lambda d: (-_severity_of(str(d)), str(d).lower()))
    vf2 = vf.model_copy(deep=True)
    vf2.value = ordered
    return vf2


def _edit_followup(vf: ValuedField, specialty: Optional[str]) -> ValuedField:
    if vf.status != FieldStatus.FILLED or not vf.value or specialty is None:
        return vf
    if not isinstance(vf.value, list):
        return vf
    edited = []
    for entry in vf.value:
        s = str(entry).strip()
        if _is_vague_followup(s):
            edited.append(f"{s.rstrip('.')} with {specialty}")
        else:
            edited.append(s)
    vf2 = vf.model_copy(deep=True)
    vf2.value = edited
    return vf2


def _edit_hospital_course(vf: ValuedField) -> ValuedField:
    """Reviewer enforces a recognizable opener so we have a measurable, learnable edit.

    The hidden rule: hospital_course must start with 'Hospital course summary:' and
    have sentence-level line breaks. Real reviewers absolutely standardize narrative
    headers like this; we use a deterministic version so Part 2 has measurable signal.
    """
    if vf.status != FieldStatus.FILLED or not vf.value:
        return vf
    text = str(vf.value).strip()
    if not text:
        return vf
    new_text = text
    if not new_text.lower().startswith("hospital course summary:"):
        new_text = "Hospital course summary: " + new_text
    # Pretty-print: ensure sentence-level line breaks
    new_text = re.sub(r"\.\s+(?=[A-Z(])", ".\n", new_text)
    vf2 = vf.model_copy(deep=True)
    vf2.value = new_text
    return vf2


def _edit_pending_results(vf: ValuedField, discharge_date: Optional[str]) -> ValuedField:
    if vf.status != FieldStatus.FILLED or not vf.value:
        return vf
    if not isinstance(vf.value, list):
        return vf
    date_tag = discharge_date or "discharge"
    edited = []
    for entry in vf.value:
        s = str(entry).strip()
        if "pending" in s.lower() and "as of" not in s.lower():
            edited.append(f"{s.rstrip('.')} (pending as of {date_tag})")
        else:
            edited.append(s)
    vf2 = vf.model_copy(deep=True)
    vf2.value = edited
    return vf2


# ----------------------------- public entry point ------------------------------


def review(draft: DischargeSummary) -> DischargeSummary:
    """Apply the hidden policy. Returns a NEW DischargeSummary; original is not mutated."""
    edited = deepcopy(draft)

    edited.admission_medications = _edit_brand_field(edited.admission_medications)
    edited.discharge_medications = _edit_brand_field(edited.discharge_medications)
    edited.secondary_diagnoses = _edit_dx_order(edited.secondary_diagnoses)
    edited.hospital_course = _edit_hospital_course(edited.hospital_course)

    principal_val = (
        edited.principal_diagnosis.value if edited.principal_diagnosis.status == FieldStatus.FILLED else None
    )
    secondary_val = (
        edited.secondary_diagnoses.value if edited.secondary_diagnoses.status == FieldStatus.FILLED else None
    )
    specialty = _infer_specialty(principal_val, secondary_val)
    edited.follow_up = _edit_followup(edited.follow_up, specialty)

    discharge_date_val = (
        edited.discharge_date.value if edited.discharge_date.status == FieldStatus.FILLED else None
    )
    edited.pending_results = _edit_pending_results(edited.pending_results, discharge_date_val)

    # safety_flags: deliberately NOT edited — preserves the Part 1 safety floor.
    return edited
