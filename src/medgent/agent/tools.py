"""Tools the agent can call. Each tool is a pure function over (state, index, args).

Important: a tool may FAIL. The agent must observe failures (no silent success) and the
loop dispatcher retries transient errors before surfacing the failure to the agent.
"""

from __future__ import annotations

import logging
import random
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .. import config
from ..models import (
    Citation,
    DrugInteraction,
    FieldStatus,
    LabResult,
    Medication,
    PageExtract,
    PatientIndex,
    SafetyFlag,
    Severity,
)
from ..pdf_index import current_encounter_id, pages_of_encounter

log = logging.getLogger("medgent.tools")


class ToolError(RuntimeError):
    pass


# ----------------------------- search / fetch ---------------------------------


class SearchHit(BaseModel):
    page_no: int
    doc_type: str
    encounter_id: Optional[str] = None
    score: float
    excerpt: str
    is_handwritten: bool
    transcription_confidence: float


def search_index(
    idx: PatientIndex,
    query: str,
    *,
    doc_types: Optional[list[str]] = None,
    encounter: Optional[str] = "current",
    top_k: int = 6,
) -> list[SearchHit]:
    """Naive keyword search over indexed pages. Cheap, transparent, debuggable."""
    enc_id = (
        current_encounter_id(idx) if encounter == "current"
        else None if encounter == "all"
        else encounter
    )
    pages = list(pages_of_encounter(idx, enc_id))
    if doc_types:
        pages = [p for p in pages if p.doc_type in doc_types]

    tokens = [t.lower() for t in query.split() if len(t) > 2]
    if not tokens:
        return []

    def score(p: PageExtract) -> float:
        haystack = (
            p.free_text + " "
            + " ".join(c.text or "" for t in p.tables for c in t.cells)
            + " " + " ".join(m.name_as_written for m in p.medications_mentioned)
            + " " + " ".join(lv.name + " " + (lv.value or "") for lv in p.lab_values)
            + " " + " ".join(p.diagnoses_mentioned)
        ).lower()
        return sum(haystack.count(t) for t in tokens)

    scored = [(score(p), p) for p in pages]
    scored = [t for t in scored if t[0] > 0]
    scored.sort(key=lambda t: t[0], reverse=True)

    hits: list[SearchHit] = []
    for s, p in scored[:top_k]:
        excerpt = (p.free_text or "")[:400]
        if not excerpt and p.tables:
            excerpt = " | ".join(
                f"{c.header or ''}:{c.text or ''}" for t in p.tables for c in t.cells[:8]
            )[:400]
        hits.append(
            SearchHit(
                page_no=p.page_no,
                doc_type=p.doc_type,
                encounter_id=p.encounter_id,
                score=float(s),
                excerpt=excerpt or "(no extractable text)",
                is_handwritten=p.is_handwritten,
                transcription_confidence=p.handwriting_confidence,
            )
        )
    return hits


def fetch_page(idx: PatientIndex, page_no: int) -> PageExtract:
    for p in idx.pages:
        if p.page_no == page_no:
            return p
    raise ToolError(f"page {page_no} not in index (have {len(idx.pages)})")


# ----------------------------- meds + labs -----------------------------------


def _cite_from(p: PageExtract, excerpt: str) -> Citation:
    return Citation(
        page_no=p.page_no,
        doc_type=p.doc_type,
        excerpt=excerpt[:300],
        is_handwritten=p.is_handwritten,
        transcription_confidence=p.handwriting_confidence,
    )


def get_medications(
    idx: PatientIndex,
    timing: Literal["admission", "during_stay", "discharge", "all"] = "all",
    *,
    encounter: Optional[str] = "current",
) -> list[tuple[Medication, Citation]]:
    """Returns a list of (Medication, Citation) tuples from the index."""
    enc_id = current_encounter_id(idx) if encounter == "current" else None
    out: list[tuple[Medication, Citation]] = []
    for p in pages_of_encounter(idx, enc_id):
        for m in p.medications_mentioned:
            if timing != "all" and m.timing != timing:
                continue
            med = Medication(
                name_as_written=m.name_as_written,
                dose=m.dose,
                route=m.route,
                frequency=m.frequency,
                duration=m.duration,
            )
            out.append((med, _cite_from(p, f"{m.name_as_written} {m.dose or ''} {m.frequency or ''}".strip())))
    return out


def get_lab_values(
    idx: PatientIndex,
    *,
    name: Optional[str] = None,
    encounter: Optional[str] = "current",
) -> list[tuple[LabResult, Citation]]:
    enc_id = current_encounter_id(idx) if encounter == "current" else None
    out: list[tuple[LabResult, Citation]] = []
    name_lc = name.lower() if name else None
    for p in pages_of_encounter(idx, enc_id):
        for lv in p.lab_values:
            if name_lc and name_lc not in lv.name.lower():
                continue
            status: Any = lv.status if lv.status in ("filled", "pending", "not_done") else "missing"
            lab = LabResult(
                name=lv.name, value=lv.value, units=lv.units, date_observed=lv.date, status=status
            )
            out.append((lab, _cite_from(p, f"{lv.name}: {lv.value or '—'} ({lv.status})")))
    return out


def get_dates(
    idx: PatientIndex,
    category: Literal["admission", "discharge", "all"] = "all",
    *,
    encounter: Optional[str] = "current",
) -> list[tuple[str, Citation]]:
    """Heuristic: look for admission/discharge keywords near a date on the same page."""
    enc_id = current_encounter_id(idx) if encounter == "current" else None
    out: list[tuple[str, Citation]] = []
    for p in pages_of_encounter(idx, enc_id):
        text_lc = (p.free_text or "").lower()
        for d in p.dates_visible:
            if category == "all":
                out.append((d, _cite_from(p, f"date {d} on page {p.page_no} ({p.doc_type})")))
            elif category == "admission" and any(k in text_lc for k in ("admit", "admission", "doa")):
                out.append((d, _cite_from(p, f"admission-context date {d} on {p.doc_type}")))
            elif category == "discharge" and any(k in text_lc for k in ("discharge", "dod", "at discharge")):
                out.append((d, _cite_from(p, f"discharge-context date {d} on {p.doc_type}")))
    return out


# ----------------------------- LLM-backed tools ------------------------------


class ConflictReport(BaseModel):
    relation: Literal["agree", "conflict", "unrelated"]
    explanation: str = Field(max_length=400)


def compare_facts(claim_a: str, claim_b: str) -> ConflictReport:
    """Use Flash to adjudicate whether two short claims agree, conflict, or are unrelated."""
    from ..gemini_client import call_structured

    prompt = (
        "Given two clinical-context claims, decide their relation:\n"
        "AGREE — they assert the same fact (modulo phrasing)\n"
        "CONFLICT — they assert different values for the same property\n"
        "UNRELATED — they describe different facts\n\n"
        f"Claim A: {claim_a}\nClaim B: {claim_b}\n"
        "Return JSON {relation, explanation}. Be brief."
    )
    return call_structured(prompt, schema=ConflictReport, temperature=0.0, max_output_tokens=400)  # type: ignore[return-value]


# ----------------------------- drug interaction mock --------------------------


_HARDCODED_INTERACTIONS: dict[frozenset[str], DrugInteraction] = {}


def _build_interactions() -> None:
    pairs = [
        ("warfarin", "ibuprofen", "Increased bleeding risk (NSAID potentiates anticoagulant)", Severity.HIGH),
        ("warfarin", "aspirin", "Increased bleeding risk", Severity.HIGH),
        ("rosuvastatin", "clarithromycin", "Macrolide may increase statin levels — myopathy risk", Severity.MED),
        ("metformin", "iohexol", "Risk of contrast-induced nephropathy with metformin — hold around contrast study", Severity.MED),
        ("digoxin", "furosemide", "Hypokalemia from loop diuretic potentiates digoxin toxicity", Severity.MED),
        ("ciprofloxacin", "ondansetron", "Both prolong QT — additive risk of arrhythmia", Severity.MED),
        ("insulin", "propranolol", "Beta-blocker masks hypoglycemia symptoms", Severity.LOW),
        ("amiodarone", "warfarin", "Amiodarone increases warfarin effect — bleeding risk", Severity.HIGH),
        ("pantoprazole", "clopidogrel", "PPI may reduce clopidogrel efficacy", Severity.LOW),
        ("amlodipine", "simvastatin", "Increased simvastatin exposure — myopathy risk", Severity.MED),
    ]
    for a, b, desc, sev in pairs:
        _HARDCODED_INTERACTIONS[frozenset({a, b})] = DrugInteraction(
            drug_a=a, drug_b=b, description=desc, severity=sev
        )


_build_interactions()


def drug_interaction_check(
    drug_names_normalized: list[str], *, force_fail: bool = False
) -> list[DrugInteraction]:
    """Mock external tool. Random failure rate exercises robust failure handling."""
    if force_fail or random.random() < config.DRUG_TOOL_FAIL_PROB:
        raise ToolError("drug-interaction service: 503 Service Unavailable (mock)")

    found: list[DrugInteraction] = []
    norm = [d.lower().strip() for d in drug_names_normalized if d]
    for i in range(len(norm)):
        for j in range(i + 1, len(norm)):
            key = frozenset({norm[i], norm[j]})
            if key in _HARDCODED_INTERACTIONS:
                found.append(_HARDCODED_INTERACTIONS[key])
    return found


# ----------------------------- escalation -------------------------------------


def flag_for_clinician_review(
    state_safety_flags: list[SafetyFlag],
    field: str,
    reason: str,
    severity: Severity = Severity.MED,
    citations: Optional[list[Citation]] = None,
    raised_at_step: Optional[int] = None,
) -> SafetyFlag:
    flag = SafetyFlag(
        field=field, reason=reason, severity=severity,
        raised_at_step=raised_at_step, citations=citations or [],
    )
    state_safety_flags.append(flag)
    return flag


# ----------------------------- medication reconciliation ----------------------


def reconcile_medications(idx: PatientIndex):
    """Deterministic admission-vs-discharge med comparator with documented-reason search.

    Lazy import to avoid circular dependency (med_recon → tools.search_index).
    """
    from ..med_recon import reconcile_medications as _reconcile

    adm = get_medications(idx, timing="admission", encounter="current")
    dis = get_medications(idx, timing="discharge", encounter="current")
    return _reconcile(idx, adm, dis)
