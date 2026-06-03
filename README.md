# medgent — agentic discharge-summary drafting

> Take-home for Dscribe (Unriddle Technologies), AI Engineer role · 2026-06-03

An agent that reads a patient's raw clinical source-note PDF and produces a structured
discharge-summary **draft for clinician review**. Built around the brief's #1 evaluation
criterion: **clinical safety above all** — the agent refuses to invent facts, surfaces
conflicts, flags pending data, and emits a readable step trace.

The full design is at [`docs/superpowers/specs/2026-06-03-dscribe-discharge-summary-design.md`](docs/superpowers/specs/2026-06-03-dscribe-discharge-summary-design.md).

---

## Architecture (4 stages, ReAct loop in the middle)

```
Stage 0 — PDF Index (deterministic envelope, LLM per page)
   pdftoppm -r 150 → PNG per page
   Gemini Flash per page → PageExtract {doc_type, dates, tables, free_text,
                                        meds, labs, dx, handwriting_conf}
   Encounter clustering (largest cluster = current admission)

Stage 1 — Field-Filler Agent Loop  (real planning + re-planning; NOT a pipeline)
   State: TodoList of required fields, accumulated evidence, open questions, flags
   Tools: search_index · fetch_page · get_medications · get_lab_values · get_dates
          compare_facts · drug_interaction_check · reconcile_medications
   Actions: call_tool · mark_field · flag_field · stop
   Hard caps: 60 iterations · 8 tool calls / field · 200 total tool calls

Stage 2 — Compose
   Atomic fields transfer from state. Narrative fields (hospital_course, follow_up)
   get one Pro call given ONLY the cited excerpts, with per-sentence citation map.

Stage 3 — Independent Verifier  (THE safety floor)
   Fresh Flash call. Sees ONLY proposed text + cited excerpts, NEVER the PDF.
   Strips any sentence not supported by ≥1 cited excerpt → SafetyFlag.

Stage 4 — Med-Recon + Drug-Interactions
   Med-Recon: brand→generic normalization (Indian brands), comparator with
              documented-reason search; flags unexplained add/stop/change.
   Drug-Interaction: mock external service with 10% transient failure rate
              to exercise robust failure handling.
```

The middle stage **is the agent** — it chooses what to work on, not a phase machine.
Re-planning is genuine: a `compare_facts` CONFLICT triggers a reconciliation query,
a `search_index` miss triggers a broadened search OR an honest FLAG, a low-confidence
handwritten citation downgrades a FILLED commit to FLAGGED. The outer phases (index,
compose, verify, side-checks) are deterministic structure around the agent.

## How the no-fabrication guardrail is enforced — five overlapping layers

1. **Type-system block.** `ValuedField[T]` Pydantic validator refuses
   `status=FILLED` without ≥1 citation, and `FLAGGED/CONFLICTING` without a reason.
   You cannot construct a fabricated FILLED field through the schema. See
   [`models.py`](src/medgent/models.py).

2. **Citation validation at commit.** When the agent calls `mark_field`, every
   citation's `page_no` must exist in the index AND its excerpt must overlap with the
   page's extracted content (shingle hit). Invalid citations cause an automatic
   FILLED → FLAGGED downgrade by the loop dispatcher. See `_validate_and_enrich_citations`
   in [`agent/loop.py`](src/medgent/agent/loop.py).

3. **Handwriting-confidence floor.** Citations sourced only from low-confidence
   handwritten content (`transcription_confidence < 0.65`) cannot support FILLED;
   they can support FLAGGED only. Enforced in the same dispatcher.

4. **Independent verifier (Stage 3).** A fresh Flash call sees ONLY the proposed text
   and the cited excerpts — never the PDF. For each sentence, it returns SUPPORTED or
   NOT_SUPPORTED. Conservative bias: when uncertain, NOT_SUPPORTED. Stripped sentences
   are recorded as SafetyFlags. See [`verifier.py`](src/medgent/verifier.py).

5. **Iteration / tool-call caps with auto-flag on overflow.** A field that exceeds its
   per-field cap is auto-FLAGGED with reason `"step cap exceeded — clinician must
   decide"`. The loop itself ends rather than spinning.

## How failures and conflicts are handled

- **Tool failures**: `_dispatch_tool` retries transient errors once with backoff.
  Persistent failures surface to the agent as an explicit "ERROR: …" observation,
  which it must handle (try another tool / flag the field). A failed call is NEVER
  treated as a silent success.
- **Conflicts**: when the agent calls `compare_facts(a, b)` and gets CONFLICT, the
  reflector queues a reconciliation step and, if unresolvable, commits the field as
  CONFLICTING with both values listed.
- **Pending vs. missing**: differentiated semantically — blank lab cells default to
  PENDING (clinical convention); cells marked `—` or `X` default to MISSING.
- **Medication changes without documented reason**: the `reconcile_medications` tool
  performs the comparator + documented-reason search; any non-UNCHANGED change with
  no documented reason is emitted as `MedicationChange(needs_reconciliation=True)`
  and the agent flags the discharge_medications field for clinician reconciliation.

## Observability — the trace

Every loop iteration emits a `StepRecord`: `{step_no, phase, field_in_focus,
reasoning, action, inputs, result_summary, next_decision}`. The trace is persisted
as JSONL (machine) and rendered as Markdown (human/video). Each flag-or-escalate
moment shows the preceding planner reasoning, so it's obvious that the agent
*chose* to flag rather than guess.

Sample after running:
```
outputs/
├── drafts/<patient>.{json,md}     # structured + clinician-readable draft
└── traces/<patient>.{jsonl,md}    # step trace + rendered Markdown for video
```

## Part 2 — Learning from doctor edits

**Simulated reviewer** (deterministic, hidden from agent): five-rule policy
applied to every draft — brand→generic medication rendering, secondary-diagnosis
severity ordering, follow-up specialty annotation, pending-lab date suffix.
**Never edits `safety_flags`** — preserves the Part 1 safety floor.
See [`learning/reviewer.py`](src/medgent/learning/reviewer.py).

**Reward signals**:
- `edit_distance_norm` — mean normalized Levenshtein distance per section
- `field_retention` — fraction of fields untouched by the reviewer
- `safety_preservation` — fraction of safety_flags retained (guardrail metric)

**Learning mechanism — structured correction memory** (chosen over DPO / SFT /
bandit because the budget allows only ~50 pairs and we need *auditable, fast*
improvement that cannot erode safety):

1. Diff analyzer over each `(draft, edited)` pair extracts patterns
   (`brand_rename`, `reorder_severity`, `specialty_suffix`, `pending_date_suffix`).
2. Patterns aggregate into a Rule store keyed by `(section, pattern_kind)`.
3. Compose's narrative call injects top-K rules into its prompt:
   `"Reviewer preferences from prior edits: ..."`.
4. **Safety guard**: rules whose hint mentions `safety_flag`/`flag` are rejected at
   injection time. The verifier (Stage 3) is excluded from the learning loop entirely.

**Evaluation**: 10 synthetic patients via Gemini Pro (`gen_patient.py`), 7 train /
3 holdout, 5 iterations. Plot `edit_distance_norm` per iteration on holdout, with
`safety_preservation` alongside as the guardrail.

**Limitations**:
- *Cold start* — first ~2 iterations have weak signal; rules accumulate gradually.
- *Gaming* — an agent can lower edit distance by becoming vaguer. Mitigation:
  field-coverage reported alongside edit-distance; both must move together.
- *Single-reviewer bias* — we are optimizing toward one hidden policy. In production,
  multiple reviewers disagree; the rule store would key by reviewer.
- *Safety drift* — the rule-injection guard + verifier-exclusion prevent learning
  from removing flags, but a sufficiently determined adversarial policy could attempt
  more subtle drift; production would need a separate compliance check.

## What I would do with more time

1. **Per-page handwriting visual re-pass**: pages with low transcription confidence
   should get a second Pro call with a "you cannot read this — what fragments are
   trustworthy?" prompt instead of just one Flash pass.
2. **Real semantic search** over the index (embeddings) — currently `search_index`
   is keyword-based; works because the index is small, but at 500+ pages it
   degrades.
3. **Encounter clustering with active prompting** — currently a date-proximity
   heuristic. A small Flash call given the doc_types could disambiguate
   "prior history reference" vs "this admission."
4. **Replace the deterministic reviewer with a fine-tuned Flash judge** that has
   inter-rater variance — gives the bandit a real distribution to optimize against.
5. **Negative test corpus** of synthetic patients with planted fabrications, so we
   can score the verifier's strip rate directly.

---

## Run instructions

### Prereqs
- macOS or Linux, Python 3.11+
- `poppler` (`brew install poppler`) — for PDF rendering
- GCP project with Vertex AI enabled
- `gcloud auth application-default login`

### Install
```bash
git clone https://github.com/ShivamShrivastava18/medgent.git
cd medgent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # edit GOOGLE_CLOUD_PROJECT if needed
```

### End-to-end on a patient
```bash
medgent run data/patients/patient_2/source.pdf
# outputs:
#   outputs/drafts/patient_2.{json,md}
#   outputs/traces/patient_2.{jsonl,md}
```

### Just (re)index a PDF
```bash
medgent index data/patients/patient_2/source.pdf --force
```

### Generate synthetic patients (Part 2 cohort)
```bash
PYTHONPATH=src python -m medgent.gen_patient 5
# writes data/synthetic/synth_{NN}/{source.pdf, manifest.json}
```

### Part 2 — learning loop
```bash
PYTHONPATH=src python -c "
from pathlib import Path
from src.medgent.learning.train import train_loop, plot_metrics
train = [Path(p) for p in sorted(Path('data/synthetic').glob('synth_*/source.pdf'))][:7]
holdout = [Path(p) for p in sorted(Path('data/synthetic').glob('synth_*/source.pdf'))][7:10]
metrics = train_loop(train, holdout, n_iterations=5)
plot_metrics(metrics, Path('outputs/learning_curve.png'))
"
```

## Results — real patient (`patient_2`)

Running the agent end-to-end on the provided 71-page PDF:

- **42 loop iterations, 26 tool calls, 8 safety flags raised.**
- `discharge_date`, `principal_diagnosis`, `secondary_diagnoses`, `discharge_medications`,
  `medication_changes`, `drug_interactions` were committed FILLED with citations.
- `admission_date` was downgraded `FILLED → FLAGGED` by the schema-level
  handwriting-confidence floor — every supporting citation came from low-confidence
  handwritten notes. **This is the structural safety story; the type system caught
  what the planner missed.** See trace step 7 in `outputs/traces/patient_2.md`.
- `hospital_course`, `procedures`, `allergies`, `follow_up`, `discharge_condition`
  flagged with honest "could not find after multiple search attempts" — the agent did
  not invent narrative.

Outputs at `outputs/drafts/patient_2.{json,md}` and `outputs/traces/patient_2.{jsonl,md}`.

## Results — Part 2

5 synthetic patients generated (`data/synthetic/synth_{01..05}`), each with three
planted forms of messiness (pending lab, undocumented med change, conflicting
diagnosis). Split 3 train / 2 holdout, 3 learning iterations.

Holdout `edit_distance_norm` (overall mean, lower is better):

| iteration | overall | sec.hospital_course | rules_total |
|---:|---:|---:|---:|
| 0 (baseline) | **0.0083** | **0.0830** | 0 |
| 1 | 0.00084 | 0.0084 | 2 |
| 2 | 0.00081 | 0.0081 | 2 |
| 3 | 0.00118 | 0.0118 | 2 |

**~10× drop in edit-distance-norm from iter 0 → iter 1**, and the rule store accumulates
2 rules (`reorder_severity` for secondary diagnoses, `text_change` for the
hospital-course opener). `safety_preservation` stays at **1.0** every iteration — the
rule-injection guard rejects any rule whose hint references safety_flags, and the
verifier is excluded from the learning loop entirely. `field_retention` 0.9 means 1
field out of 10 still gets edited (hospital_course gains sentence-level line breaks
the reviewer wants — this signal is still being absorbed and would converge with
more training data).

Artifacts:
- `outputs/learning_curve.png` — holdout edit-distance-norm + safety_preservation curve
- `outputs/learning_metrics.json` — raw per-iteration metrics
- `outputs/memory.json` — accumulated Rule store after training

## What's done vs. open

- [x] Stage 0 PDF index with encounter clustering
- [x] Pydantic schemas with no-fabrication validators
- [x] Field-filler agent loop with caps + citation validation
- [x] Compose stage with cited-narrative composition
- [x] Independent verifier (safety floor)
- [x] Med reconciliation with documented-reason search
- [x] Drug-interaction mock with random failure exercise
- [x] Trace JSONL + Markdown renderer
- [x] CLI entry point
- [x] Synthetic patient generator (typed PDFs with planted messiness)
- [x] Simulated reviewer (hidden 5-rule policy)
- [x] Diff analyzer + correction memory + train loop + plot
- [x] Smoke tests for safety machinery (`pytest tests/`)
- [ ] Negative-test corpus to score verifier strip rate directly (future work)
- [ ] Per-page low-confidence handwriting re-pass (future work)
- [ ] Semantic search (embeddings) over the index (future work)
