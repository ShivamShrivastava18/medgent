"""Schemas. The no-fabrication guardrail is enforced here, in the type system.

Every clinical field is a ValuedField. The validator refuses FILLED status without a
citation, and refuses FLAGGED/CONFLICTING without a reason. The agent cannot commit a
fact through the schema without saying where it came from.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ----------------------------- core safety enums ------------------------------


class FieldStatus(str, Enum):
    """How sure are we about this field?

    FILLED      — supported by ≥1 cited excerpt
    MISSING     — explicitly absent from source notes ("no known allergies")
    PENDING     — awaited (e.g., culture pending, blank lab cell)
    FLAGGED     — agent declines to commit; clinician must decide
    CONFLICTING — multiple disagreeing values found in sources
    """

    FILLED = "filled"
    MISSING = "missing"
    PENDING = "pending"
    FLAGGED = "flagged"
    CONFLICTING = "conflicting"


class Severity(str, Enum):
    LOW = "low"
    MED = "medium"
    HIGH = "high"


# ----------------------------- citation + provenance ---------------------------


class Citation(BaseModel):
    """A pointer back to source. Without this we cannot mark a field FILLED."""

    page_no: int = Field(ge=1)
    doc_type: str
    excerpt: str = Field(min_length=1, description="quote from the source")
    is_handwritten: bool = False
    transcription_confidence: float = Field(ge=0.0, le=1.0, default=1.0)

    def trustworthy_enough_to_fill(self, floor: float) -> bool:
        """Citations from low-confidence handwriting can support FLAG but not FILL."""
        return self.transcription_confidence >= floor or not self.is_handwritten


T = TypeVar("T")


class ValuedField(BaseModel, Generic[T]):
    """A clinical field's recorded state. THE schema-level fabrication block."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    value: Optional[T] = None
    status: FieldStatus
    citations: list[Citation] = Field(default_factory=list)
    flag_reason: Optional[str] = None
    conflicts: Optional[list[T]] = None

    @model_validator(mode="after")
    def _enforce_safety_invariants(self) -> "ValuedField[T]":
        if self.status == FieldStatus.FILLED:
            if self.value is None:
                raise ValueError("FILLED requires a non-None value")
            if not self.citations:
                raise ValueError("FILLED requires ≥1 citation — fabrication blocked")
        if self.status == FieldStatus.FLAGGED and not self.flag_reason:
            raise ValueError("FLAGGED requires flag_reason")
        if self.status == FieldStatus.CONFLICTING:
            if not self.flag_reason:
                raise ValueError("CONFLICTING requires flag_reason")
            if not self.conflicts or len(self.conflicts) < 2:
                raise ValueError("CONFLICTING requires ≥2 conflicting values")
        return self


# ----------------------------- clinical value types ----------------------------


class Demographics(BaseModel):
    name: Optional[str] = None
    age: Optional[str] = None  # string because notes often say "45/F" etc.
    sex: Optional[str] = None
    mrn: Optional[str] = None


class Medication(BaseModel):
    name_as_written: str
    normalized_name: Optional[str] = None  # generic name post-normalization
    dose: Optional[str] = None  # "40mg"
    route: Optional[str] = None  # "PO", "IV", "SC"
    frequency: Optional[str] = None  # "1-0-1", "BD", "TDS"
    duration: Optional[str] = None  # "5 days"


class MedChangeType(str, Enum):
    ADDED = "added"
    STOPPED = "stopped"
    CHANGED = "changed"
    UNCHANGED = "unchanged"


class MedicationChange(BaseModel):
    medication_name: str
    normalized_name: Optional[str] = None
    change_type: MedChangeType
    prior_value: Optional[Medication] = None
    new_value: Optional[Medication] = None
    documented_reason: Optional[str] = None
    needs_reconciliation: bool = False  # True if change has no documented reason
    citations: list[Citation] = Field(default_factory=list)


class LabResult(BaseModel):
    """A single observed lab result with explicit status (vs. pending/not-done)."""

    name: str
    value: Optional[str] = None
    units: Optional[str] = None
    date_observed: Optional[str] = None  # ISO; string for flexibility
    status: Literal["filled", "pending", "not_done", "missing"] = "filled"


class DrugInteraction(BaseModel):
    drug_a: str
    drug_b: str
    description: str
    severity: Severity


class SafetyFlag(BaseModel):
    field: str
    reason: str
    severity: Severity
    raised_at_step: Optional[int] = None
    citations: list[Citation] = Field(default_factory=list)


# ----------------------------- discharge summary top-level ---------------------


class DischargeSummary(BaseModel):
    """The final structured draft. Every section carries its own status + provenance."""

    patient: ValuedField[Demographics]
    admission_date: ValuedField[str]
    discharge_date: ValuedField[str]
    principal_diagnosis: ValuedField[str]
    secondary_diagnoses: ValuedField[list[str]]
    hospital_course: ValuedField[str]
    procedures: ValuedField[list[str]]
    admission_medications: ValuedField[list[Medication]]
    discharge_medications: ValuedField[list[Medication]]
    medication_changes: ValuedField[list[MedicationChange]]
    allergies: ValuedField[list[str]]
    follow_up: ValuedField[list[str]]
    pending_results: ValuedField[list[str]]
    discharge_condition: ValuedField[str]
    drug_interactions: ValuedField[list[DrugInteraction]]

    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_pdf: Optional[str] = None
    iterations_used: int = 0
    tool_calls_used: int = 0


# ----------------------------- Stage 0: page extraction ------------------------


PageType = Literal[
    "admission_note",
    "progress_note",
    "nurses_note",
    "er_chart",
    "vitals_chart",
    "lab_form",
    "lab_report",
    "med_admin",
    "consult_sheet",
    "investigation_image",
    "discharge_summary",
    "blank",
    "other",
]


class TableCell(BaseModel):
    row: int
    col: int
    header: Optional[str] = None
    text: Optional[str] = None
    is_blank: bool = False
    is_dash: bool = False  # "—" or "X" → not done


class ExtractedTable(BaseModel):
    title: Optional[str] = None
    headers: list[str] = Field(default_factory=list)
    cells: list[TableCell] = Field(default_factory=list)


class MedMention(BaseModel):
    name_as_written: str
    dose: Optional[str] = None
    route: Optional[str] = None
    frequency: Optional[str] = None
    duration: Optional[str] = None
    timing: Optional[Literal["admission", "during_stay", "discharge", "unknown"]] = "unknown"


class LabMention(BaseModel):
    name: str
    value: Optional[str] = None
    units: Optional[str] = None
    date: Optional[str] = None
    status: Literal["filled", "pending", "not_done", "unknown"] = "unknown"


class PageExtract(BaseModel):
    """Output of Stage 0 per page. Cached and indexed."""

    page_no: int
    doc_type: PageType
    encounter_id: Optional[str] = None  # set by clustering
    dates_visible: list[str] = Field(default_factory=list)  # ISO YYYY-MM-DD
    free_text: str = ""
    tables: list[ExtractedTable] = Field(default_factory=list)
    medications_mentioned: list[MedMention] = Field(default_factory=list)
    lab_values: list[LabMention] = Field(default_factory=list)
    diagnoses_mentioned: list[str] = Field(default_factory=list)
    is_handwritten: bool = False
    handwriting_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    notes: Optional[str] = None  # extractor's free-form notes about page quality


class Encounter(BaseModel):
    encounter_id: str
    pages: list[int]
    earliest_date: Optional[str] = None
    latest_date: Optional[str] = None
    is_current: bool = False  # the encounter we are drafting for


class PatientIndex(BaseModel):
    """The whole indexed PDF. The agent queries this; it never re-reads the PDF."""

    source_pdf: str
    pages: list[PageExtract]
    encounters: list[Encounter] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ----------------------------- agent loop state --------------------------------


FieldName = Literal[
    "demographics",
    "admission_date",
    "discharge_date",
    "principal_diagnosis",
    "secondary_diagnoses",
    "hospital_course",
    "procedures",
    "admission_medications",
    "discharge_medications",
    "medication_changes",
    "allergies",
    "follow_up",
    "pending_results",
    "discharge_condition",
    "drug_interactions",
]


REQUIRED_FIELDS: tuple[FieldName, ...] = (
    "demographics",
    "admission_date",
    "discharge_date",
    "principal_diagnosis",
    "secondary_diagnoses",
    "hospital_course",
    "procedures",
    "admission_medications",
    "discharge_medications",
    "medication_changes",
    "allergies",
    "follow_up",
    "pending_results",
    "discharge_condition",
    "drug_interactions",
)


class FieldSlot(BaseModel):
    """Mutable workspace for a single required field during the agent loop."""

    field: FieldName
    status: FieldStatus = FieldStatus.PENDING
    proposed_value: Any = None
    citations: list[Citation] = Field(default_factory=list)
    flag_reason: Optional[str] = None
    conflicts: Optional[list[Any]] = None
    attempts: int = 0
    last_action_step: Optional[int] = None
    notes: list[str] = Field(default_factory=list)  # agent's running notes on this field
    is_committed: bool = False  # set once the agent calls mark_field

    def is_done(self) -> bool:
        return self.is_committed or self.status in {
            FieldStatus.FILLED,
            FieldStatus.MISSING,
            FieldStatus.PENDING,
            FieldStatus.FLAGGED,
            FieldStatus.CONFLICTING,
        } and self.is_committed


class StepPhase(str, Enum):
    PLAN = "plan"
    ACT = "act"
    OBSERVE = "observe"
    REFLECT = "reflect"


class StepRecord(BaseModel):
    """One row of the agent's trace. JSONL'd and rendered to Markdown for the video."""

    step_no: int
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    phase: StepPhase
    field_in_focus: Optional[str] = None
    reasoning: str
    action: Optional[str] = None
    inputs: Optional[dict] = None
    result_summary: Optional[str] = None
    next_decision: Optional[str] = None


class AgentState(BaseModel):
    """In-memory mutable state for the agent loop."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    todo: dict[str, FieldSlot]
    iteration: int = 0
    tool_calls: int = 0
    open_questions: list[str] = Field(default_factory=list)
    trace: list[StepRecord] = Field(default_factory=list)
    safety_flags: list[SafetyFlag] = Field(default_factory=list)
    finished: bool = False

    def all_committed(self) -> bool:
        return all(slot.is_committed for slot in self.todo.values())

    def remaining(self) -> list[str]:
        return [k for k, slot in self.todo.items() if not slot.is_committed]
