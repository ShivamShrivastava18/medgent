"""Stage 4 — Medication reconciliation.

Build the union of admission and discharge medications, normalize names via Flash
(crucial because the source uses Indian brands like RACIPER, EMESET, MEFTAL SPAS that
aren't in standard drug DBs), then categorize each unique drug as
ADDED / STOPPED / CHANGED / UNCHANGED.

For non-UNCHANGED cases, search progress notes for a documented reason. If none is
found, the change is flagged for clinician reconciliation. This is the satisfying
realization of brief requirement #5: never silently resolve a med change.
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

from . import config
from .gemini_client import call_structured
from .models import (
    Citation,
    MedChangeType,
    Medication,
    MedicationChange,
    PageExtract,
    PatientIndex,
)
from . import agent  # noqa: F401 — keep import path stable

log = logging.getLogger("medgent.med_recon")


# ----------------------------- LLM helpers ------------------------------------


class NormalizedMed(BaseModel):
    name_as_written: str
    generic_name: str = Field(description="lowercase generic INN/USAN")
    therapeutic_class: Optional[str] = None


class NormalizedMeds(BaseModel):
    items: list[NormalizedMed] = Field(default_factory=list)


_NORMALIZE_SYSTEM = """\
You normalize drug names appearing on Indian and international hospital charts to their
GENERIC (INN / USAN) names. Many entries are brand names like "TAB RACIPER", "TAB EMESET",
"INJ PAN", "TAB MEFTAL SPAS". Map each to its generic name in lowercase. If you genuinely
don't recognize an entry, set generic_name="unknown" — do not guess.

Examples (for calibration):
  "TAB RACIPER"     → rabeprazole
  "TAB EMESET"      → ondansetron
  "INJ PAN"         → pantoprazole
  "TAB OFLOX TZ"    → ofloxacin + tinidazole
  "TAB MEFTAL SPAS" → mefenamic acid + dicyclomine
  "TAB LOPIRAMIDE"  → loperamide
  "Tab Crocin"      → paracetamol
  "Inj Lasix"       → furosemide

Return a NormalizedMeds JSON object covering every input item, preserving order.
"""


def normalize_med_names(names: list[str]) -> dict[str, str]:
    """name_as_written → generic_name (lowercase). 'unknown' allowed; never invented."""
    names = [n for n in names if n]
    if not names:
        return {}
    prompt = "Normalize these drug entries:\n" + "\n".join(f"  - {n}" for n in names)
    out = call_structured(
        prompt,
        schema=NormalizedMeds,
        system=_NORMALIZE_SYSTEM,
        model=config.MODEL_FLASH,
        temperature=0.0,
        max_output_tokens=2048,
    )
    items = out.items if isinstance(out, NormalizedMeds) else NormalizedMeds.model_validate(out.model_dump()).items
    return {it.name_as_written: it.generic_name.lower().strip() for it in items}


# ----------------------------- recon logic ------------------------------------


def _by_generic(meds_with_cites: list[tuple[Medication, Citation]], norm: dict[str, str]) -> dict[str, list[tuple[Medication, Citation]]]:
    out: dict[str, list[tuple[Medication, Citation]]] = {}
    for med, cite in meds_with_cites:
        gen = norm.get(med.name_as_written, "unknown")
        out.setdefault(gen, []).append((med, cite))
    return out


def _meds_differ(a: Medication, b: Medication) -> bool:
    return (a.dose, a.frequency, a.route) != (b.dose, b.frequency, b.route)


def _find_documented_reason(idx: PatientIndex, drug_query: str, change_type: MedChangeType) -> Optional[tuple[str, Citation]]:
    """Naive search for 'started/stopped/changed' phrases near the drug name in progress notes."""
    from .agent.tools import search_index

    keywords = {
        MedChangeType.ADDED: "started initiated commenced began",
        MedChangeType.STOPPED: "stopped discontinued held withheld",
        MedChangeType.CHANGED: "increased decreased switched changed titrated",
    }.get(change_type, "")
    q = f"{drug_query} {keywords}"
    hits = search_index(idx, q, doc_types=["progress_note", "nurses_note", "consult_sheet", "admission_note", "discharge_summary"], top_k=3)
    if not hits:
        return None
    h = hits[0]
    return (
        f"Possible documented reason near: {h.excerpt[:240]}",
        Citation(
            page_no=h.page_no,
            doc_type=h.doc_type,
            excerpt=h.excerpt[:300],
            is_handwritten=h.is_handwritten,
            transcription_confidence=h.transcription_confidence,
        ),
    )


def reconcile_medications(
    idx: PatientIndex,
    admission_meds: list[tuple[Medication, Citation]],
    discharge_meds: list[tuple[Medication, Citation]],
) -> list[MedicationChange]:
    all_names = [m.name_as_written for m, _ in admission_meds] + [m.name_as_written for m, _ in discharge_meds]
    norm = normalize_med_names(sorted(set(all_names)))

    adm_by = _by_generic(admission_meds, norm)
    dis_by = _by_generic(discharge_meds, norm)
    all_generics = set(adm_by) | set(dis_by)
    changes: list[MedicationChange] = []

    for gen in sorted(all_generics):
        adm = adm_by.get(gen) or []
        dis = dis_by.get(gen) or []
        if adm and not dis:
            med, cite = adm[0]
            documented = _find_documented_reason(idx, med.name_as_written, MedChangeType.STOPPED)
            changes.append(
                MedicationChange(
                    medication_name=med.name_as_written,
                    normalized_name=gen,
                    change_type=MedChangeType.STOPPED,
                    prior_value=med,
                    documented_reason=documented[0] if documented else None,
                    needs_reconciliation=documented is None,
                    citations=[cite] + ([documented[1]] if documented else []),
                )
            )
        elif dis and not adm:
            med, cite = dis[0]
            documented = _find_documented_reason(idx, med.name_as_written, MedChangeType.ADDED)
            changes.append(
                MedicationChange(
                    medication_name=med.name_as_written,
                    normalized_name=gen,
                    change_type=MedChangeType.ADDED,
                    new_value=med,
                    documented_reason=documented[0] if documented else None,
                    needs_reconciliation=documented is None,
                    citations=[cite] + ([documented[1]] if documented else []),
                )
            )
        else:
            adm_med, adm_cite = adm[0]
            dis_med, dis_cite = dis[0]
            if _meds_differ(adm_med, dis_med):
                documented = _find_documented_reason(idx, adm_med.name_as_written, MedChangeType.CHANGED)
                changes.append(
                    MedicationChange(
                        medication_name=adm_med.name_as_written,
                        normalized_name=gen,
                        change_type=MedChangeType.CHANGED,
                        prior_value=adm_med,
                        new_value=dis_med,
                        documented_reason=documented[0] if documented else None,
                        needs_reconciliation=documented is None,
                        citations=[adm_cite, dis_cite] + ([documented[1]] if documented else []),
                    )
                )
            else:
                changes.append(
                    MedicationChange(
                        medication_name=adm_med.name_as_written,
                        normalized_name=gen,
                        change_type=MedChangeType.UNCHANGED,
                        prior_value=adm_med,
                        new_value=dis_med,
                        citations=[adm_cite, dis_cite],
                    )
                )

    return changes
