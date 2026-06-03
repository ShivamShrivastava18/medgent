"""Stage 2 — Compose.

Atomic fields are transferred verbatim from the agent's state. Narrative fields
(hospital_course, follow_up) get one Pro call each that receives ONLY the citation
excerpts the agent gathered — the composer never re-reads the PDF. Output is JSON
with `text` and per-sentence `citation_indices` so the verifier can check sentence-level.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

from . import config
from .gemini_client import call_structured
from .models import (
    AgentState,
    Citation,
    DischargeSummary,
    Demographics,
    DrugInteraction,
    FieldSlot,
    FieldStatus,
    Medication,
    MedicationChange,
    ValuedField,
)

log = logging.getLogger("medgent.compose")


_NARRATIVE_FIELDS = {"hospital_course", "follow_up"}


# ----------------------------- narrative composition --------------------------


class CitedSentence(BaseModel):
    text: str
    citation_indices: list[int] = Field(default_factory=list, description="indices into the evidence list")


class NarrativeOutput(BaseModel):
    sentences: list[CitedSentence] = Field(default_factory=list)


_NARRATIVE_SYSTEM = """\
You are composing one section of a discharge summary for clinician review. You will
receive ONLY a list of evidence excerpts gathered by the upstream agent — you cannot
see the original PDF. You may use ONLY these excerpts to write the section.

RULES:
- Every sentence you write MUST be supported by ≥1 evidence excerpt; cite indices.
- If the evidence does not support a clinically necessary claim, omit it. Do NOT invent.
- Be clinically concise — short sentences, no filler.
- Use present tense for ongoing observations, past tense for events.
- Output JSON matching the NarrativeOutput schema. No prose outside JSON.
"""


def _compose_narrative(field_name: str, evidence: list[Citation], memory_hint: str = "") -> NarrativeOutput:
    if not evidence:
        return NarrativeOutput(sentences=[])
    bullets = "\n".join(
        f"  [{i}] (page {c.page_no}, {c.doc_type}{', handwritten' if c.is_handwritten else ''}): {c.excerpt}"
        for i, c in enumerate(evidence)
    )
    target = {
        "hospital_course": "Write the hospital course: admission reason → key events / interventions → discharge condition. 3–6 short sentences.",
        "follow_up": "Write follow-up instructions as a list of brief items. Always include specialty/department when documented.",
    }[field_name]

    learned_block = f"\n\n{memory_hint}\n" if memory_hint else ""

    prompt = (
        f"Compose: {field_name}\n\n"
        f"Guideline: {target}\n"
        f"{learned_block}\n"
        f"Evidence excerpts (cite by index):\n{bullets}\n\n"
        "Return JSON {sentences: [{text, citation_indices: [int]}, ...]}."
    )
    out = call_structured(
        prompt,
        schema=NarrativeOutput,
        system=_NARRATIVE_SYSTEM,
        model=config.MODEL_PRO,
        temperature=0.1,
        max_output_tokens=1024,
    )
    return out if isinstance(out, NarrativeOutput) else NarrativeOutput.model_validate(out.model_dump())


def _narrative_to_field(field_name: str, slot: FieldSlot, memory_hint: str = "") -> ValuedField:
    if slot.status != FieldStatus.FILLED or not slot.citations:
        return _slot_to_valuedfield(slot, _coerce=str)
    narr = _compose_narrative(field_name, slot.citations, memory_hint=memory_hint)
    if not narr.sentences:
        return ValuedField[str](
            status=FieldStatus.FLAGGED,
            value=None,
            flag_reason="compose found insufficient evidence to write narrative — clinician must draft",
            citations=slot.citations,
        )
    # join sentences, preserve mapping for verifier downstream
    joined = " ".join(s.text for s in narr.sentences)
    # narrow citations to those actually referenced
    used = sorted({i for s in narr.sentences for i in s.citation_indices if 0 <= i < len(slot.citations)})
    used_cites = [slot.citations[i] for i in used] or slot.citations
    return ValuedField[str](
        status=FieldStatus.FILLED,
        value=joined,
        citations=used_cites,
    )


# ----------------------------- atomic transfer --------------------------------


def _coerce_value(field: str, raw: Any) -> Any:
    """Best-effort coercion from the agent's free-form value into the schema type."""
    if raw is None:
        return None
    if field == "demographics":
        if isinstance(raw, dict):
            return Demographics(**{k: raw.get(k) for k in ("name", "age", "sex", "mrn")})
        return None
    if field in ("secondary_diagnoses", "procedures", "allergies", "follow_up", "pending_results"):
        if isinstance(raw, list):
            return [str(x) for x in raw]
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(";") if s.strip()]
        return [str(raw)]
    if field in ("admission_medications", "discharge_medications"):
        out: list[Medication] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    out.append(
                        Medication(
                            name_as_written=str(item.get("name_as_written", item.get("name", ""))),
                            normalized_name=item.get("normalized_name"),
                            dose=item.get("dose"),
                            route=item.get("route"),
                            frequency=item.get("frequency"),
                            duration=item.get("duration"),
                        )
                    )
                elif isinstance(item, str):
                    out.append(Medication(name_as_written=item))
        return out
    if field == "medication_changes":
        if isinstance(raw, list):
            return [MedicationChange.model_validate(x) if isinstance(x, dict) else x for x in raw]
        return []
    if field == "drug_interactions":
        if isinstance(raw, list):
            return [DrugInteraction.model_validate(x) if isinstance(x, dict) else x for x in raw]
        return []
    return raw if isinstance(raw, str) else str(raw)


def _slot_to_valuedfield(slot: FieldSlot, _coerce=None) -> ValuedField:
    value = _coerce(slot.proposed_value) if _coerce and slot.proposed_value is not None else slot.proposed_value
    try:
        return ValuedField(
            value=value,
            status=slot.status,
            citations=slot.citations,
            flag_reason=slot.flag_reason,
            conflicts=slot.conflicts,
        )
    except Exception as exc:
        # Schema rejected — downgrade to FLAGGED
        return ValuedField(
            value=None,
            status=FieldStatus.FLAGGED,
            citations=slot.citations,
            flag_reason=f"compose validation failed: {exc}",
        )


# ----------------------------- main entry --------------------------------------


def compose(state: AgentState, source_pdf: Optional[str] = None, memory=None) -> DischargeSummary:
    """If `memory` is a CorrectionMemory, its rules are injected into narrative prompts."""
    fields: dict[str, ValuedField] = {}
    for name, slot in state.todo.items():
        if name in _NARRATIVE_FIELDS:
            hint = memory.for_compose_prompt(name) if memory is not None else ""
            fields[name] = _narrative_to_field(name, slot, memory_hint=hint)
        else:
            coerced = lambda v, n=name: _coerce_value(n, v)
            fields[name] = _slot_to_valuedfield(slot, _coerce=coerced)

    return DischargeSummary(
        patient=fields["demographics"],
        admission_date=fields["admission_date"],
        discharge_date=fields["discharge_date"],
        principal_diagnosis=fields["principal_diagnosis"],
        secondary_diagnoses=fields["secondary_diagnoses"],
        hospital_course=fields["hospital_course"],
        procedures=fields["procedures"],
        admission_medications=fields["admission_medications"],
        discharge_medications=fields["discharge_medications"],
        medication_changes=fields["medication_changes"],
        allergies=fields["allergies"],
        follow_up=fields["follow_up"],
        pending_results=fields["pending_results"],
        discharge_condition=fields["discharge_condition"],
        drug_interactions=fields["drug_interactions"],
        safety_flags=state.safety_flags,
        source_pdf=source_pdf,
        iterations_used=state.iteration,
        tool_calls_used=state.tool_calls,
    )
