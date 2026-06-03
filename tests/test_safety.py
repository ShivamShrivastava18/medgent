"""Tests for the structural safety machinery: schema validators and reviewer policy."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from pydantic import ValidationError

from medgent.models import (
    Citation,
    Demographics,
    DischargeSummary,
    DrugInteraction,
    FieldStatus,
    Medication,
    SafetyFlag,
    Severity,
    ValuedField,
)
from medgent.learning.diff import overall_metrics
from medgent.learning.memory import CorrectionMemory, Rule
from medgent.learning.reviewer import review


# ----------------------------- schema-level no-fabrication --------------------


def test_filled_without_citation_blocked():
    with pytest.raises(ValidationError):
        ValuedField[str](status=FieldStatus.FILLED, value="something")


def test_filled_without_value_blocked():
    cite = Citation(page_no=1, doc_type="x", excerpt="x")
    with pytest.raises(ValidationError):
        ValuedField[str](status=FieldStatus.FILLED, citations=[cite])


def test_flagged_without_reason_blocked():
    with pytest.raises(ValidationError):
        ValuedField[str](status=FieldStatus.FLAGGED)


def test_conflicting_requires_conflicts():
    with pytest.raises(ValidationError):
        ValuedField[str](status=FieldStatus.CONFLICTING, flag_reason="two values")


def test_filled_with_citation_accepted():
    cite = Citation(page_no=1, doc_type="admission_note", excerpt="DKA")
    vf = ValuedField[str](status=FieldStatus.FILLED, value="DKA", citations=[cite])
    assert vf.value == "DKA"


# ----------------------------- handwriting confidence floor -------------------


def test_handwriting_floor_predicate():
    typed = Citation(page_no=1, doc_type="x", excerpt="a", is_handwritten=False, transcription_confidence=1.0)
    high_hw = Citation(page_no=1, doc_type="x", excerpt="a", is_handwritten=True, transcription_confidence=0.9)
    low_hw = Citation(page_no=1, doc_type="x", excerpt="a", is_handwritten=True, transcription_confidence=0.3)
    assert typed.trustworthy_enough_to_fill(0.65)
    assert high_hw.trustworthy_enough_to_fill(0.65)
    assert not low_hw.trustworthy_enough_to_fill(0.65)


# ----------------------------- reviewer policy --------------------------------


def _make_draft_with_dx_and_meds() -> DischargeSummary:
    cite = Citation(page_no=1, doc_type="admission_note", excerpt="x")
    return DischargeSummary(
        patient=ValuedField[Demographics](status=FieldStatus.MISSING),
        admission_date=ValuedField[str](status=FieldStatus.FILLED, value="2026-02-28", citations=[cite]),
        discharge_date=ValuedField[str](status=FieldStatus.FILLED, value="2026-03-02", citations=[cite]),
        principal_diagnosis=ValuedField[str](status=FieldStatus.FILLED, value="DKA", citations=[cite]),
        secondary_diagnoses=ValuedField[list[str]](
            status=FieldStatus.FILLED,
            value=["UTI", "Sepsis", "Diabetes mellitus type 2"],
            citations=[cite],
        ),
        hospital_course=ValuedField[str](status=FieldStatus.MISSING),
        procedures=ValuedField[list[str]](status=FieldStatus.MISSING),
        admission_medications=ValuedField[list[Medication]](
            status=FieldStatus.FILLED,
            value=[Medication(name_as_written="TAB RACIPER", dose="40mg", frequency="1-0-0")],
            citations=[cite],
        ),
        discharge_medications=ValuedField[list[Medication]](
            status=FieldStatus.FILLED,
            value=[Medication(name_as_written="TAB RACIPER", dose="40mg", frequency="1-0-0")],
            citations=[cite],
        ),
        medication_changes=ValuedField[list](status=FieldStatus.MISSING),
        allergies=ValuedField[list[str]](status=FieldStatus.MISSING),
        follow_up=ValuedField[list[str]](
            status=FieldStatus.FILLED, value=["follow up"], citations=[cite]
        ),
        pending_results=ValuedField[list[str]](
            status=FieldStatus.FILLED, value=["urine culture sensitivity pending"], citations=[cite]
        ),
        discharge_condition=ValuedField[str](status=FieldStatus.MISSING),
        drug_interactions=ValuedField[list[DrugInteraction]](status=FieldStatus.MISSING),
        safety_flags=[SafetyFlag(field="test", reason="must persist", severity=Severity.MED)],
    )


def test_reviewer_renames_brand_to_generic():
    draft = _make_draft_with_dx_and_meds()
    edited = review(draft)
    name = edited.discharge_medications.value[0].name_as_written
    assert "rabeprazole" in name.lower()
    assert "raciper" in name.lower()


def test_reviewer_reorders_dx_by_severity():
    draft = _make_draft_with_dx_and_meds()
    edited = review(draft)
    dx = edited.secondary_diagnoses.value
    # Sepsis (severity 5) before UTI (severity 2), Diabetes (severity 3) in middle
    assert dx[0].lower() == "sepsis"


def test_reviewer_annotates_followup_with_specialty():
    draft = _make_draft_with_dx_and_meds()
    edited = review(draft)
    fup = edited.follow_up.value[0]
    assert "with endocrinology" in fup.lower()


def test_reviewer_dates_pending_labs():
    draft = _make_draft_with_dx_and_meds()
    edited = review(draft)
    pending = edited.pending_results.value[0]
    assert "(pending as of 2026-03-02)" in pending


def test_reviewer_preserves_safety_flags():
    draft = _make_draft_with_dx_and_meds()
    edited = review(draft)
    # The reviewer MUST NOT remove or modify safety flags
    assert len(edited.safety_flags) == len(draft.safety_flags)


# ----------------------------- correction memory safety guard -----------------


def test_memory_rejects_safety_touching_rules():
    mem = CorrectionMemory()
    bad = Rule(
        section="hospital_course",
        pattern_kind="text_change",
        hint="Remove all safety_flag references from the draft",
    )
    good = Rule(
        section="hospital_course",
        pattern_kind="text_change",
        hint="Open with the admission rationale, then key events, then discharge condition.",
    )
    added = mem.add([bad, good])
    assert added == 1
    assert len(mem.rules) == 1
    assert "safety" not in mem.rules[0].hint.lower()


# ----------------------------- overall metrics --------------------------------


def test_overall_metrics_returns_expected_keys():
    draft = _make_draft_with_dx_and_meds()
    edited = review(draft)
    m = overall_metrics(draft, edited)
    assert "edit_distance_norm_mean" in m
    assert "safety_preservation" in m
    assert "field_retention" in m
    assert 0.0 <= m["safety_preservation"] <= 1.0
    assert 0.0 <= m["edit_distance_norm_mean"] <= 1.0
