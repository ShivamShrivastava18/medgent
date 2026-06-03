# Video demo walkthrough (3–5 minutes)

Record with Loom. Two patients shown:
- **Real patient** (`data/patients/patient_2/source.pdf`) — messy, mixed handwritten/typed, prior-encounter contamination. This is where the agent's flag-or-escalate behaviour stands out.
- **One synthetic patient** (`data/synthetic/synth_03/source.pdf` recommended — a typed cleaner case with planted messiness).

## Suggested narrative (≈4 minutes)

### 0:00–0:30 — Frame the problem (30s)
> "The agent reads raw clinical PDFs and produces a structured discharge-summary
> draft for clinician review. The hard requirement is no fabrication — any field
> we can't source from the documents must be flagged, never guessed."

Show the architecture diagram in README. Point at the 4 stages: PDF index, agent loop, compose, independent verifier.

### 0:30–1:30 — Real patient run (60s)

```
medgent run data/patients/patient_2/source.pdf
```

Show the live terminal output (Rich UI). Highlight:
- "indexing PDF…" → 71 pages, 7 encounters detected, the largest cluster (51 pages) marked current.
- "agent loop…" → finishes in N iterations and M tool calls.
- "verifier (independent fact-check)…" runs.
- "safety flags: N" listed.

Open `outputs/drafts/patient_2.md`. Walk through:
- `Discharge date` — FILLED with page-2 citation.
- `Principal diagnosis` — FILLED ("Acute gastroenteritis with dehydration") with page-1 citation.
- `Admission date` — FLAGGED with reason: "all supporting citations are low-confidence handwritten; downgraded FILLED → FLAGGED by handwriting floor". **This is the key safety moment.**
- `Hospital course`, `Procedures`, `Allergies`, `Follow-up`, `Discharge condition` — each FLAGGED honestly because the typed admission/discharge pages don't contain them and the handwritten notes are unreliable. The agent did not invent narrative.

### 1:30–2:30 — Walk the trace (60s)

Open `outputs/traces/patient_2.md`. Find the `admission_date` block (around step 7).

Read aloud:
> "The agent searched for the admission date, got back a hit from a handwritten
> nurses' note dated 2026-02-28 with transcription confidence 0.6. The agent's
> reasoning text says: '… insufficient to confidently fill the field. I will
> perform a keyword search to corroborate or replace this date.' It tried a
> different tool, got the same low-confidence source, and decided to commit
> FILLED. The schema-level guardrail then downgraded FILLED → FLAGGED because
> every supporting citation came from low-confidence handwriting."

This is THE structural safety story — the type system caught what the agent missed.

### 2:30–3:15 — Synthetic patient (45s)

```
medgent run data/synthetic/synth_03/source.pdf
```

Show:
- The agent fills more fields successfully (typed PDF, fewer handwriting issues).
- One field flagged for the planted "undocumented med change" — see `manifest.json` to confirm we caught the planted messiness.
- The med-reconciliation tool found the change and surfaced it; we did not silently resolve.

### 3:15–4:00 — Part 2 — Learning from doctor edits (45s)

Open `outputs/learning_curve.png`. Show:
- Mean edit-distance-norm on holdout decreases over iterations.
- `safety_preservation` stays at 1.0 throughout (right axis).

> "The simulated reviewer applies a hidden 5-rule style policy. After each
> iteration's edits, the diff analyzer extracts patterns into a Rule store; the
> next compose call injects relevant rules into its prompt. Rules referencing
> safety_flags are rejected at injection — that's why safety_preservation never
> drops. The verifier (Part 1 safety floor) is excluded from the learning loop."

### 4:00–4:30 — Limitations / what's next (30s)

> "What this doesn't yet do: real semantic search over the index, a second
> pass on low-confidence pages with a different prompt, and learning that
> reaches atomic fields rather than just narrative. See README §What I would
> do with more time."

---

## Cue cards to keep open while recording

- `outputs/drafts/patient_2.md` — final draft
- `outputs/traces/patient_2.md` — trace with step 7 highlighted
- `outputs/learning_curve.png` — Part 2 plot
- `data/synthetic/synth_03/manifest.json` — to compare planted messiness vs detected flags
- `src/medgent/models.py` — point at `ValuedField._enforce_safety_invariants` to show the schema-level block
- `src/medgent/verifier.py` — point at "this fresh Flash call never sees the PDF"

## What NOT to say

- Don't apologize for any FLAGGED field on the real patient. Every one of them is the agent doing the right thing on incomplete or low-confidence data.
- Don't read all 15 fields. Pick 3-4 representative ones (one FILLED, one FLAGGED-by-honest-search, the handwriting-floor one).
- Don't apologize that we only have one real patient — explain that the synthetic cohort exists precisely to give Part 2 a real evaluation surface.
