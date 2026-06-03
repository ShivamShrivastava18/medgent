"""The ReAct-style agent loop. The agent decides each step — there is no fixed pipeline.

Hard caps enforced (brief requirement #9):
- MAX_ITERATIONS:           absolute cap on loop iterations
- MAX_TOOL_CALLS_PER_FIELD: per-field cap; if exceeded, the field is auto-flagged
- MAX_TOTAL_TOOL_CALLS:     absolute cap on tool invocations

Failure handling:
- Tool dispatch retries transient errors once. Persistent failures surface to the
  agent as a "tool error" observation, which it must handle (try another tool / flag).
- A failed tool call is NEVER treated as a successful empty result.

Citation validation:
- When the agent attempts to commit a FILLED field, every citation's page_no must be
  present in the index. The excerpt must overlap with that page's extracted content.
  Citations that fail validation cause the field to be downgraded to FLAGGED with
  reason="citation could not be verified against the index".
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .. import config
from ..gemini_client import call_structured, GeminiError
from ..models import (
    AgentState,
    Citation,
    FieldSlot,
    FieldStatus,
    PatientIndex,
    REQUIRED_FIELDS,
    SafetyFlag,
    Severity,
    StepPhase,
    StepRecord,
)
from . import tools as T
from .prompts import PLANNER_SYSTEM, build_planner_prompt

log = logging.getLogger("medgent.agent")


# ----------------------------- LLM action schema ------------------------------


class CitationLite(BaseModel):
    page_no: int
    doc_type: str = ""
    excerpt: str = ""


class MarkAction(BaseModel):
    field: str
    value: Optional[Any] = None
    citations: list[CitationLite] = Field(default_factory=list)
    status: Literal["filled", "missing", "pending", "flagged", "conflicting"]
    flag_reason: Optional[str] = None
    conflicts: Optional[list[Any]] = None


class FlagAction(BaseModel):
    field: str
    reason: str
    severity: Literal["low", "medium", "high"] = "medium"
    citations: list[CitationLite] = Field(default_factory=list)


class AgentAction(BaseModel):
    thought: str
    field_in_focus: Optional[str] = None
    action: Literal["call_tool", "mark_field", "flag_field", "stop"]
    tool_name: Optional[str] = None
    tool_inputs: Optional[dict] = None
    mark: Optional[MarkAction] = None
    flag: Optional[FlagAction] = None
    expected_signal: str = ""


# ----------------------------- tool dispatch ---------------------------------


def _dispatch_tool(idx: PatientIndex, name: Optional[str], inputs: dict | None) -> tuple[bool, Any]:
    """Returns (ok, result_or_error_str). Retries once on transient failure."""
    if not name:
        return False, "no tool_name provided"
    inputs = inputs or {}

    handlers = {
        "search_index": lambda: T.search_index(
            idx,
            query=str(inputs.get("query", "")),
            doc_types=inputs.get("doc_types"),
            encounter=inputs.get("encounter", "current"),
            top_k=int(inputs.get("top_k", 6)),
        ),
        "fetch_page": lambda: T.fetch_page(idx, int(inputs["page_no"])),
        "get_medications": lambda: T.get_medications(
            idx, timing=inputs.get("timing", "all"), encounter=inputs.get("encounter", "current")
        ),
        "get_lab_values": lambda: T.get_lab_values(
            idx, name=inputs.get("name"), encounter=inputs.get("encounter", "current")
        ),
        "get_dates": lambda: T.get_dates(
            idx, category=inputs.get("category", "all"), encounter=inputs.get("encounter", "current")
        ),
        "compare_facts": lambda: T.compare_facts(
            claim_a=str(inputs.get("claim_a", "")), claim_b=str(inputs.get("claim_b", ""))
        ),
        "drug_interaction_check": lambda: T.drug_interaction_check(
            drug_names_normalized=list(inputs.get("drug_names_normalized", []))
        ),
        "reconcile_medications": lambda: T.reconcile_medications(idx),
    }
    fn = handlers.get(name)
    if fn is None:
        return False, f"unknown tool '{name}'. Available: {sorted(handlers)}"

    last_err: Optional[Exception] = None
    for attempt in (1, 2):
        try:
            return True, fn()
        except T.ToolError as exc:
            last_err = exc
            log.warning("tool %s failed attempt %d: %s", name, attempt, exc)
            if attempt == 1:
                time.sleep(0.4)
            continue
        except Exception as exc:  # noqa: BLE001
            return False, f"unexpected error in {name}: {exc}"
    return False, f"tool {name} failed after retry: {last_err}"


def _result_summary(name: str, result: Any) -> str:
    try:
        if isinstance(result, list):
            preview = result[:5]
            tail = f" (+{len(result) - 5} more)" if len(result) > 5 else ""
            return f"{name} → {len(result)} items: {json.dumps([_jsonable(r) for r in preview], default=str)[:900]}{tail}"
        return f"{name} → {json.dumps(_jsonable(result), default=str)[:900]}"
    except Exception:
        return f"{name} → <unserializable result>"


def _jsonable(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump()
    if isinstance(x, tuple):
        return [_jsonable(i) for i in x]
    if isinstance(x, list):
        return [_jsonable(i) for i in x]
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    return x


# ----------------------------- citation validation ----------------------------


def _enrich_citation(cite: CitationLite, idx: PatientIndex) -> tuple[bool, Citation | str]:
    """Verify cite.page_no exists in index and excerpt overlaps. Enrich with hw confidence."""
    page = next((p for p in idx.pages if p.page_no == cite.page_no), None)
    if page is None:
        return False, f"citation page {cite.page_no} not in index"
    haystack = (page.free_text or "").lower()
    for t in page.tables:
        for c in t.cells:
            haystack += " " + (c.text or "").lower()
            haystack += " " + (c.header or "").lower()
    haystack += " " + " ".join(m.name_as_written.lower() for m in page.medications_mentioned)
    haystack += " " + " ".join(d.lower() for d in page.diagnoses_mentioned)
    excerpt_lc = (cite.excerpt or "").lower().strip()
    overlap_ok = False
    if excerpt_lc:
        # 8-char shingle hit anywhere
        shingles = [excerpt_lc[i : i + 8] for i in range(0, max(1, len(excerpt_lc) - 7), 4)]
        overlap_ok = any(sh in haystack for sh in shingles)
    if not overlap_ok and len(excerpt_lc) > 0 and not page.is_handwritten:
        return False, f"citation excerpt does not overlap page {cite.page_no} content"
    # For handwritten pages, transcription is imperfect — we allow looser citations
    return True, Citation(
        page_no=cite.page_no,
        doc_type=page.doc_type,
        excerpt=(cite.excerpt or "")[:300] or "(no excerpt given)",
        is_handwritten=page.is_handwritten,
        transcription_confidence=page.handwriting_confidence,
    )


def _validate_and_enrich_citations(
    cites: list[CitationLite], idx: PatientIndex
) -> tuple[list[Citation], list[str]]:
    enriched: list[Citation] = []
    errors: list[str] = []
    for c in cites:
        ok, val = _enrich_citation(c, idx)
        if ok:
            enriched.append(val)  # type: ignore[arg-type]
        else:
            errors.append(str(val))
    return enriched, errors


# ----------------------------- state helpers ---------------------------------


def _state_summary(state: AgentState) -> str:
    rows = []
    for name, slot in state.todo.items():
        s = slot.status.value if slot.is_committed else "—"
        flag = f" flag={slot.flag_reason[:40]!r}" if slot.flag_reason else ""
        rows.append(f"  {name}: status={s} attempts={slot.attempts}{flag}")
    return "\n".join(rows)


def _recent_history(state: AgentState, n: int = 8) -> str:
    recent = state.trace[-n:]
    if not recent:
        return "(no steps yet)"
    lines = []
    for s in recent:
        line = f"[step {s.step_no}] {s.phase.value}"
        if s.field_in_focus:
            line += f" field={s.field_in_focus}"
        if s.action:
            line += f" action={s.action}"
        if s.result_summary:
            line += f" result={s.result_summary[:200]}"
        lines.append(line)
    return "\n".join(lines)


def _commit_mark(slot: FieldSlot, mark: MarkAction, citations: list[Citation], note: str = "") -> None:
    slot.status = FieldStatus(mark.status)
    slot.proposed_value = mark.value
    slot.citations = citations
    slot.flag_reason = mark.flag_reason
    slot.conflicts = mark.conflicts
    slot.is_committed = True
    if note:
        slot.notes.append(note)


# ----------------------------- the loop --------------------------------------


def run_agent(idx: PatientIndex, *, max_iterations: int = config.MAX_ITERATIONS) -> AgentState:
    state = AgentState(todo={f: FieldSlot(field=f) for f in REQUIRED_FIELDS})

    while state.iteration < max_iterations:
        state.iteration += 1
        # 1. Auto-flag any field whose per-field cap is exceeded
        for slot in state.todo.values():
            if slot.is_committed:
                continue
            if slot.attempts >= config.MAX_TOOL_CALLS_PER_FIELD:
                slot.status = FieldStatus.FLAGGED
                slot.flag_reason = (
                    f"per-field tool-call cap ({config.MAX_TOOL_CALLS_PER_FIELD}) "
                    "exceeded without sufficient evidence — clinician must decide"
                )
                slot.is_committed = True
                state.safety_flags.append(
                    SafetyFlag(
                        field=slot.field,
                        reason=slot.flag_reason,
                        severity=Severity.MED,
                        raised_at_step=state.iteration,
                    )
                )

        if state.all_committed():
            state.finished = True
            log.info("all fields committed at iteration %d", state.iteration)
            break

        if state.tool_calls >= config.MAX_TOTAL_TOOL_CALLS:
            log.warning("MAX_TOTAL_TOOL_CALLS hit — auto-flagging remaining fields")
            break

        # 2. Build prompt + call planner
        remaining = state.remaining()
        prompt = build_planner_prompt(
            state_summary=_state_summary(state),
            recent_history=_recent_history(state),
            fields_remaining=remaining,
            open_questions=state.open_questions[-5:],
            iteration=state.iteration,
            max_iterations=max_iterations,
        )

        try:
            action = call_structured(
                prompt,
                schema=AgentAction,
                system=PLANNER_SYSTEM,
                model=config.MODEL_PRO,
                temperature=0.2,
                max_output_tokens=2048,
            )
        except GeminiError as exc:
            log.error("planner LLM failed: %s — flagging step", exc)
            state.trace.append(
                StepRecord(
                    step_no=state.iteration,
                    phase=StepPhase.OBSERVE,
                    reasoning=f"planner LLM error: {exc}",
                    action=None,
                    result_summary="planner failed",
                    next_decision="will retry on next iteration",
                )
            )
            continue

        if not isinstance(action, AgentAction):
            action = AgentAction.model_validate(action.model_dump() if hasattr(action, "model_dump") else action)

        # 3. Execute the action
        rec = StepRecord(
            step_no=state.iteration,
            phase=StepPhase.PLAN,
            field_in_focus=action.field_in_focus,
            reasoning=action.thought,
            action=action.action,
            inputs=(action.tool_inputs or (action.mark.model_dump() if action.mark else (action.flag.model_dump() if action.flag else None))),
            next_decision=action.expected_signal or None,
        )

        if action.action == "call_tool":
            ok, result = _dispatch_tool(idx, action.tool_name, action.tool_inputs)
            state.tool_calls += 1
            slot = state.todo.get(action.field_in_focus or "")
            if slot:
                slot.attempts += 1
            rec.phase = StepPhase.ACT
            rec.action = f"call_tool:{action.tool_name}"
            rec.result_summary = _result_summary(action.tool_name or "?", result) if ok else f"ERROR: {result}"
            state.trace.append(rec)

            if not ok:
                # On persistent tool failure, the agent must observe it next turn.
                state.open_questions.append(
                    f"tool {action.tool_name} failed: {result}"
                )

        elif action.action == "mark_field":
            if not action.mark or action.mark.field not in state.todo:
                rec.result_summary = "ERROR: mark missing or unknown field"
                state.trace.append(rec)
                continue
            mark = action.mark
            slot = state.todo[mark.field]
            citations, cite_errors = _validate_and_enrich_citations(mark.citations, idx)
            # Schema-level safety: FILLED requires ≥1 citation
            if mark.status == "filled" and (not citations or cite_errors):
                # Downgrade to FLAGGED — fabrication blocked
                downgrade_reason = (
                    f"agent attempted FILLED but citations invalid ({'; '.join(cite_errors) or 'no citations'}); "
                    "downgraded to FLAGGED by guardrail"
                )
                slot.status = FieldStatus.FLAGGED
                slot.flag_reason = downgrade_reason
                slot.proposed_value = mark.value
                slot.is_committed = True
                state.safety_flags.append(
                    SafetyFlag(
                        field=mark.field,
                        reason=downgrade_reason,
                        severity=Severity.MED,
                        raised_at_step=state.iteration,
                    )
                )
                rec.result_summary = f"DOWNGRADED to FLAGGED: {downgrade_reason}"
            else:
                # Floor-check handwriting confidence for FILLED
                if mark.status == "filled" and citations:
                    if not any(c.trustworthy_enough_to_fill(config.HANDWRITING_FILLED_FLOOR) for c in citations):
                        downgrade_reason = (
                            "all supporting citations are low-confidence handwritten; "
                            "downgraded FILLED → FLAGGED by handwriting floor"
                        )
                        slot.status = FieldStatus.FLAGGED
                        slot.flag_reason = downgrade_reason
                        slot.proposed_value = mark.value
                        slot.citations = citations
                        slot.is_committed = True
                        state.safety_flags.append(
                            SafetyFlag(
                                field=mark.field,
                                reason=downgrade_reason,
                                severity=Severity.MED,
                                raised_at_step=state.iteration,
                            )
                        )
                        rec.result_summary = f"DOWNGRADED to FLAGGED: {downgrade_reason}"
                    else:
                        _commit_mark(slot, mark, citations)
                        rec.result_summary = f"committed {mark.field}={mark.value!r} status={mark.status} cites={len(citations)}"
                else:
                    _commit_mark(slot, mark, citations)
                    if mark.status in ("flagged", "conflicting"):
                        state.safety_flags.append(
                            SafetyFlag(
                                field=mark.field,
                                reason=mark.flag_reason or f"status={mark.status}",
                                severity=Severity.MED,
                                raised_at_step=state.iteration,
                                citations=citations,
                            )
                        )
                    rec.result_summary = f"committed {mark.field} status={mark.status}"
            rec.phase = StepPhase.REFLECT
            state.trace.append(rec)

        elif action.action == "flag_field":
            if not action.flag or action.flag.field not in state.todo:
                rec.result_summary = "ERROR: flag missing or unknown field"
                state.trace.append(rec)
                continue
            flag = action.flag
            citations, _ = _validate_and_enrich_citations(flag.citations, idx)
            slot = state.todo[flag.field]
            slot.status = FieldStatus.FLAGGED
            slot.flag_reason = flag.reason
            slot.citations = citations
            slot.is_committed = True
            state.safety_flags.append(
                SafetyFlag(
                    field=flag.field, reason=flag.reason, severity=Severity(flag.severity),
                    raised_at_step=state.iteration, citations=citations,
                )
            )
            rec.phase = StepPhase.REFLECT
            rec.result_summary = f"flagged {flag.field}: {flag.reason}"
            state.trace.append(rec)

        elif action.action == "stop":
            rec.phase = StepPhase.REFLECT
            rec.result_summary = "agent requested stop"
            state.trace.append(rec)
            state.finished = True
            break

        else:
            rec.result_summary = f"ERROR: unknown action {action.action}"
            state.trace.append(rec)

    # 4. Post-loop: any uncommitted fields → auto-flag
    for slot in state.todo.values():
        if not slot.is_committed:
            slot.status = FieldStatus.FLAGGED
            slot.flag_reason = "loop ended without commit — clinician must decide"
            slot.is_committed = True
            state.safety_flags.append(
                SafetyFlag(
                    field=slot.field,
                    reason=slot.flag_reason,
                    severity=Severity.MED,
                    raised_at_step=state.iteration,
                )
            )

    return state
