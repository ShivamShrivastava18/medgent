"""Diff analyzer: extracts learnable patterns from (draft, edited) pairs.

For each section of the discharge summary, we compare the draft and edited values and
record:
  • SectionDiff with normalized edit distance and a brief description of the change
  • If structural (list reorder, brand→generic rename, suffix append), an explicit
    Rule capturing the pattern is extracted.

Rules are kept lightweight (a short prompt-hint string) so they can be injected into
the next compose call's prompt without risking the safety guarantees of Part 1.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

import Levenshtein
from pydantic import BaseModel, Field

from ..models import DischargeSummary, FieldStatus, Medication, ValuedField


SectionName = Literal[
    "principal_diagnosis", "secondary_diagnoses", "hospital_course", "procedures",
    "admission_medications", "discharge_medications", "medication_changes",
    "allergies", "follow_up", "pending_results", "discharge_condition",
]


class SectionDiff(BaseModel):
    section: str
    draft_text: str
    edited_text: str
    edit_distance: int
    edit_distance_norm: float  # 0..1
    description: str


class Rule(BaseModel):
    """A learned style/format rule extracted from observed edits."""

    section: str
    pattern_kind: Literal["brand_rename", "reorder_severity", "specialty_suffix", "pending_date_suffix", "text_change"]
    hint: str = Field(description="prompt-injectable instruction for the composer")
    frequency: int = 1
    example_before: Optional[str] = None
    example_after: Optional[str] = None


# ----------------------------- text rendering for diff ------------------------


def _stringify(vf: ValuedField) -> str:
    """Stable string representation of a field's value, for distance computation."""
    if vf.status != FieldStatus.FILLED or vf.value is None:
        return f"[{vf.status.value.upper()}{':'+vf.flag_reason if vf.flag_reason else ''}]"
    v = vf.value
    if isinstance(v, list):
        parts: list[str] = []
        for item in v:
            if isinstance(item, Medication):
                p = item.name_as_written
                if item.dose:
                    p += f" {item.dose}"
                if item.frequency:
                    p += f" {item.frequency}"
                parts.append(p)
            elif hasattr(item, "model_dump"):
                parts.append(repr(item.model_dump()))
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if hasattr(v, "model_dump"):
        d = v.model_dump()
        return " | ".join(f"{k}={v}" for k, v in d.items() if v is not None)
    return str(v)


def _section_value(draft: DischargeSummary, name: str) -> ValuedField:
    return getattr(draft, name)


_SECTIONS: tuple[str, ...] = (
    "principal_diagnosis", "secondary_diagnoses", "hospital_course", "procedures",
    "admission_medications", "discharge_medications", "allergies", "follow_up",
    "pending_results", "discharge_condition",
)


# ----------------------------- diff + rule extraction -------------------------


def section_diff(draft: DischargeSummary, edited: DischargeSummary, section: str) -> SectionDiff:
    d_text = _stringify(_section_value(draft, section))
    e_text = _stringify(_section_value(edited, section))
    dist = Levenshtein.distance(d_text, e_text)
    denom = max(len(d_text), len(e_text), 1)
    return SectionDiff(
        section=section,
        draft_text=d_text,
        edited_text=e_text,
        edit_distance=dist,
        edit_distance_norm=dist / denom,
        description=_describe_change(section, d_text, e_text),
    )


def _describe_change(section: str, d: str, e: str) -> str:
    if d == e:
        return "no change"
    if section == "secondary_diagnoses" and set(_tokenize_list(d)) == set(_tokenize_list(e)):
        return "reorder only — items unchanged"
    if section in ("admission_medications", "discharge_medications"):
        before = set(re.findall(r"[A-Z][A-Z\- ]{2,}", d))
        after = set(re.findall(r"\([A-Z][a-z\-]+\)", e))
        if after and any(b in d.upper() for b in (a.strip("()").upper() for a in after)):
            return "brand→generic rename"
    if section == "follow_up":
        if "with " in e and "with " not in d:
            return "follow-up gained specialty annotation"
    if section == "pending_results":
        if "(pending as of" in e and "(pending as of" not in d:
            return "pending result gained explicit-date suffix"
    if section == "hospital_course":
        if "hospital course summary:" in e.lower() and "hospital course summary:" not in d.lower():
            return "hospital course gained 'Hospital course summary:' opener"
    return f"text edit ({Levenshtein.distance(d, e)} chars)"


def _tokenize_list(s: str) -> list[str]:
    return [x.strip() for x in s.split("|") if x.strip()]


def extract_rules(draft: DischargeSummary, edited: DischargeSummary) -> list[Rule]:
    rules: list[Rule] = []
    for sec in _SECTIONS:
        diff = section_diff(draft, edited, sec)
        if diff.description == "no change":
            continue
        kind: str = "text_change"
        hint: str = ""
        if "brand→generic rename" in diff.description:
            kind = "brand_rename"
            hint = (
                "When writing medication entries, render brand-only names as "
                "'<generic> (<Brand>)' — e.g. 'TAB RACIPER' → 'rabeprazole (Raciper)'."
            )
        elif "reorder only" in diff.description and sec == "secondary_diagnoses":
            kind = "reorder_severity"
            hint = (
                "Order secondary diagnoses by clinical severity (most severe first); "
                "tie-break alphabetically."
            )
        elif "follow-up gained specialty" in diff.description:
            kind = "specialty_suffix"
            hint = (
                "Every follow-up item must name the specialty/department (e.g. "
                "'follow up with endocrinology'). Vague 'follow up' is not acceptable."
            )
        elif "pending result gained" in diff.description:
            kind = "pending_date_suffix"
            hint = (
                "Render pending results with explicit date suffix: "
                "'<test> (pending as of <discharge_date>)'."
            )
        elif "hospital course gained" in diff.description:
            kind = "text_change"
            hint = (
                "Open the hospital_course narrative with the exact header "
                "'Hospital course summary:' followed by one space. Break sentences with newlines."
            )
        else:
            kind = "text_change"
            hint = f"For section '{sec}', match the reviewer's phrasing exactly when evidence permits."
        rules.append(
            Rule(
                section=sec,
                pattern_kind=kind,  # type: ignore[arg-type]
                hint=hint,
                example_before=diff.draft_text[:200],
                example_after=diff.edited_text[:200],
            )
        )
    return rules


def overall_metrics(draft: DischargeSummary, edited: DischargeSummary) -> dict[str, float]:
    """Section-level edit distance + safety preservation."""
    per_section: dict[str, float] = {}
    distances: list[float] = []
    fields_changed = 0
    fields_total = 0
    for sec in _SECTIONS:
        diff = section_diff(draft, edited, sec)
        per_section[sec] = diff.edit_distance_norm
        distances.append(diff.edit_distance_norm)
        fields_total += 1
        if diff.draft_text != diff.edited_text:
            fields_changed += 1

    flags_in_draft = len(draft.safety_flags)
    flags_in_edited = len(edited.safety_flags)
    safety_preservation = (
        1.0 if flags_in_draft == 0 else min(flags_in_edited, flags_in_draft) / flags_in_draft
    )

    return {
        "edit_distance_norm_mean": (sum(distances) / max(1, len(distances))),
        "field_retention": 1 - (fields_changed / max(1, fields_total)),
        "safety_preservation": safety_preservation,
        **{f"sec.{k}": v for k, v in per_section.items()},
    }
