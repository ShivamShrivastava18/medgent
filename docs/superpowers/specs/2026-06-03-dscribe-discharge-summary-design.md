# Dscribe Take-Home — Discharge Summary Agent (Design)

**Author:** Shivam Shrivastava  •  **Date:** 2026-06-03  •  **Budget:** 6–10 hrs in a 48-hr window

## Goal

Build an agentic AI system that reads a patient's raw clinical source-note PDF and produces a structured discharge-summary draft for clinician review. The hard requirement is **clinical safety**: the agent must never invent a fact. Unknown fields must be marked missing/pending/flagged, never guessed.

## Constraints (from brief)

1. Real agent loop with planning + re-planning, not a hardcoded pipeline.
2. PDF ingestion is our problem.
3. No fabrication — required fields the agent can't source must be flagged.
4. Handle pending/missing data, medication reconciliation, conflicting notes.
5. Use mock external tools; agent decides when.
6. Robust failure handling (retry / fall back / report), step cap, readable trace.

## Architecture

Four stages. **The middle stage is a real agent loop**; the rest are deterministic structure around it.

```
Stage 0 — PDF Index (deterministic envelope, LLM per page)
    pdftoppm -r 150 → PNG per page
    Gemini Flash per page → PageExtract (doc_type, dates, text, tables, meds, labs, dx,
                                         handwriting_confidence, encounter_id)
    Encounter clustering: group pages by date proximity → encounters[]

Stage 1 — Field-Filler Agent Loop (ReAct, ~50 LOC core)
    State: todo (FieldSlot per required section), iteration, tool_calls, open_questions, trace
    Tools: search_index, fetch_page, get_medications, get_lab_values, get_dates,
           compare_facts, drug_interaction_check, flag_for_clinician_review, mark_field
    Loop: plan → act → observe → reflect → (maybe re-plan)
    Caps: 60 iters total, 8 tool calls per field, 200 total tool calls

Stage 2 — Compose (schema-first)
    Atomic fields transfer directly from state.todo.
    Narrative fields (hospital_course, follow_up) — one Pro call with evidence packets,
      output is JSON with text + per-sentence citations.

Stage 3 — Verifier (independent Flash call — the safety floor)
    Sees ONLY proposed text + cited chunks (NOT the PDF).
    Strips any sentence not supported by ≥1 cited chunk; records safety_flag.

Stage 4 — Side checks
    Med-Recon: deterministic comparator over admission/discharge meds, LLM-assisted
      brand→generic normalization, flag unexplained add/stop/change.
    Drug-Interaction: mock tool, 10% random failure to exercise robustness.
```

## Schemas (Pydantic v2 — fabrication blocked at the type level)

```python
class FieldStatus(StrEnum):
    FILLED       = "filled"        # supported by citation(s)
    MISSING      = "missing"       # explicitly absent
    PENDING      = "pending"       # awaited
    FLAGGED      = "flagged"       # clinician must decide
    CONFLICTING  = "conflicting"   # multiple disagreeing values

class Citation(BaseModel):
    page_no: int
    doc_type: str
    excerpt: str
    is_handwritten: bool
    transcription_confidence: float  # 0..1

class ValuedField(BaseModel, Generic[T]):
    value: Optional[T] = None
    status: FieldStatus
    citations: list[Citation] = []
    flag_reason: Optional[str] = None
    conflicts: Optional[list[T]] = None

    # Validator: FILLED requires ≥1 citation; FLAGGED/CONFLICTING require flag_reason
```

The `DischargeSummary` is a record of `ValuedField[T]` for every required section.

## Agent loop semantics

**One outer loop, ReAct-style** — the agent chooses the next field/action, NOT a fixed phase machine. Re-planning examples (must appear in trace):

- `compare_facts` returns CONFLICT → reflector queues reconciliation; ultimately CONFLICTING-mark
- `search_index` returns nothing strong → broaden query, switch doc types, or accept gap → FLAGGED
- Med-recon shows a drug stopped with no documented reason → flag, continue (never silently resolve)
- Citation only from handwritten content with low confidence → downgrade FILLED → FLAGGED

**Hard caps** (brief #9):
- `MAX_ITERATIONS = 60`
- `MAX_TOOL_CALLS_PER_FIELD = 8` (force-flag after this)
- `MAX_TOTAL_TOOL_CALLS = 200`
- Any cap hit on an unfilled field → auto-flag with reason="step cap exceeded"

**Tool failure handling**:
- Retry once with backoff on transient errors
- On persistent failure → flag (e.g., "drug interaction check unavailable — verify manually")
- Never proceed as if a failed call succeeded

## Observability

`StepRecord` per loop iteration → JSONL during run → Markdown renderer for video demo:

```python
class StepRecord(BaseModel):
    step_no: int
    phase: Literal["plan","act","observe","reflect"]
    field_in_focus: Optional[str]
    reasoning: str
    action: Optional[str]
    inputs: Optional[dict]
    result_summary: Optional[str]
    next_decision: Optional[str]
```

Every flag/escalation appears in the trace next to the exact preceding reasoning.

## Medication reconciliation

Build union of admission + discharge meds, normalize names via Gemini (brand↔generic — Indian brand names like RACIPER→rabeprazole, EMESET→ondansetron — not in standard DBs). Then:

| Case | Action |
|---|---|
| Both, same dose/freq | OK |
| Both, different dose/freq | CHANGED → search for documented reason → flag if absent |
| Admission only | STOPPED → search for documented reason → flag if absent |
| Discharge only | ADDED → search for documented reason → flag if absent |

## Verifier semantics

A fresh Flash call. Inputs: proposed sentence + cited chunks. Output: SUPPORTED / NOT_SUPPORTED. Conservative bias — when uncertain, mark NOT_SUPPORTED. NOT_SUPPORTED → strip sentence, append `safety_flag(unsupported_claim_removed=...)`.

**Critical property**: verifier never sees the original PDF. It cannot lean on general clinical knowledge to "confirm" a plausible-sounding claim — it can only confirm what the cited excerpts show.

## Part 2 — Learning from doctor edits

**Simulated reviewer** = deterministic editor with hidden, consistent policy:
1. Brand-only meds → `<generic> (<brand>)`
2. Secondary diagnoses ordered by severity (rule-mapped)
3. Hospital course enforced shape: admission rationale → key events → discharge condition
4. Follow-up must name specialty
5. Pending labs render as `<test> (pending as of <date>)`
6. **Never edits anything in `safety_flags`** — preserves Part 1 safety floor

**Reward signals** (all reported):
- `edit_distance_norm = levenshtein(draft, edited) / max(len(draft), len(edited))`
- `field_retention = 1 - (fields_changed / fields_total)`
- `safety_preservation = flags_retained / flags_in_draft` (guardrail metric)

**Learning mechanism — structured correction memory**:
1. Diff analyzer (Flash) extracts patterns from (draft, edited) pairs
2. Patterns aggregate into rule store: `Rule(section, pattern, replacement, frequency, examples)`
3. Next compose call injects top-K relevant rules into its prompt
4. Rule-injection guard: rules that would touch `safety_flags` are rejected

Why correction memory over DPO/SFT/bandit:
- Few-shot regime (~50 pairs max in budget); DPO needs more
- Auditable: rules are inspectable
- Verifier (Stage 3) is excluded → Part 1 safety guarantee preserved by construction

**Evaluation**: 10 synthetic patients, 7 train / 3 holdout, 5 iterations. Plot `edit_distance_norm` per section per iteration on holdout; report `safety_preservation` alongside.

**Limitations** (covered in README):
- Cold start (weak signal first few iterations)
- Gaming via vagueness (counter: report field-coverage alongside)
- Safety erosion (counter: rule guard + verifier exclusion)
- Single-reviewer bias (acknowledge)

## Two-patient problem

We received 1 real PDF. Brief expects ≥2 in the video.

1. Real patient (71-page DKA + prior gastroenteritis) = **headline demo**. Encounter disambiguation, unexplained-med-change flag, pending-lab handling.
2. `gen_patient.py` — Gemini Pro authors fictional source-note bundles (rotation: COPD exacerbation, post-op, CHF, sepsis, fall+CHI); rendered to PDF via fpdf2 with intentional messiness (one pending lab, one undocumented med change, one conflicting dx between progress notes).
3. Video: real + 1 strong synthetic case (the conflict one). README explains the synthetic cohort backs Part 2.

## Tech stack

| Component | Choice |
|---|---|
| Language | Python 3.11+ |
| LLM SDK | `google-genai` via Vertex AI (project `synth-hackathon-2026`) |
| Reasoning model | `gemini-2.5-pro` |
| Extraction / verifier / diff model | `gemini-2.5-flash` |
| Schema | Pydantic v2 |
| PDF→PNG | `pdftoppm` (poppler) |
| Synthetic PDF gen | `fpdf2` (pure Python) |
| Tracing | JSONL + Markdown renderer |
| CLI | `typer` |
| Tests | `pytest` |

## Repo

```
medgent/
├── README.md
├── pyproject.toml
├── .env.example
├── docs/superpowers/specs/2026-06-03-dscribe-discharge-summary-design.md
├── src/medgent/
│   ├── config.py         models.py         gemini_client.py
│   ├── pdf_index.py      compose.py        verifier.py
│   ├── med_recon.py      drug_interaction.py     run.py
│   ├── agent/{loop.py, tools.py, prompts.py, trace.py}
│   └── learning/{reviewer.py, diff.py, memory.py, train.py}
├── data/patients/patient_2/  (real PDF)
├── data/synthetic/           (generated PDFs)
├── outputs/{drafts, traces}/
└── tests/
```

## Time budget (10 hrs)

| Hr | Work | Cut-line for 6hr |
|---:|---|---|
| 1 | Skeleton, gemini client, Stage 0 indexer | keep |
| 2 | Schemas, agent loop scaffold, 3 core tools | keep |
| 3 | Remaining tools, compose | keep |
| 4 | Verifier, med-recon, drug-interaction mock | keep |
| 5 | Trace renderer, e2e on real patient, polish | keep |
| 6 | Synthetic patient generator (3–5 patients) | **STOP = strong Part 1** |
| 7 | Part 2: reviewer + diff analyzer + correction memory | optional |
| 8 | Part 2: learning loop + curve | optional |
| 9 | Video, README finalize | always |
| 10 | Buffer | always |

## Locked nuances

1. Date parser: **DD/MM/YY** (Indian convention).
2. Blank lab cells: default to PENDING (not MISSING). Cells marked `—`/`X`: MISSING with reason="not performed".
3. Brand→generic normalization is required for med-recon; uses a Gemini call (Indian brands not in standard DBs).
4. Handwriting confidence flows through citations; low-confidence sources can support FLAGGED but never FILLED.
5. Verifier never sees the original PDF — only proposed text + cited excerpts.
6. The agent can revise an earlier `mark_field` decision when new evidence arrives (re-planning, not append-only).
7. The agent's `stop` is the agent's choice; step caps are the safety net.
