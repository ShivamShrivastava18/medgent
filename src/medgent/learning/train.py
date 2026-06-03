"""Iterative learning loop for Part 2.

Pipeline per iteration:
  1. For each train patient: draft → reviewer → diff → rules → memory.update
  2. For each holdout patient: draft (with memory) → reviewer → metrics
  3. Append metrics to a curve

After all iterations, plot mean edit-distance-norm (and safety_preservation) over
iterations. The plot is the artifact the brief asks for.
"""

from __future__ import annotations

import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config
from ..agent.loop import run_agent
from ..compose import compose
from ..models import DischargeSummary, PatientIndex
from ..pdf_index import build_index
from ..verifier import verify
from .diff import extract_rules, overall_metrics
from .memory import CorrectionMemory
from .reviewer import review

log = logging.getLogger("medgent.train")


@dataclass
class IterationMetrics:
    iteration: int
    holdout_edit_distance_norm: float
    holdout_safety_preservation: float
    holdout_field_retention: float
    per_section: dict[str, float] = field(default_factory=dict)
    rules_total: int = 0


def _draft_one(pdf_path: Path, memory: Optional[CorrectionMemory]) -> DischargeSummary:
    idx: PatientIndex = build_index(pdf_path)
    state = run_agent(idx)
    draft = compose(state, source_pdf=str(pdf_path), memory=memory)
    draft = verify(draft)
    return draft


def evaluate(holdout_pdfs: list[Path], memory: CorrectionMemory) -> IterationMetrics:
    edit_distances: list[float] = []
    safety_preservations: list[float] = []
    field_retentions: list[float] = []
    per_section_acc: dict[str, list[float]] = {}

    for pdf in holdout_pdfs:
        draft = _draft_one(pdf, memory)
        edited = review(draft)
        m = overall_metrics(draft, edited)
        edit_distances.append(m["edit_distance_norm_mean"])
        safety_preservations.append(m["safety_preservation"])
        field_retentions.append(m["field_retention"])
        for k, v in m.items():
            if k.startswith("sec."):
                per_section_acc.setdefault(k, []).append(v)

    return IterationMetrics(
        iteration=-1,
        holdout_edit_distance_norm=statistics.mean(edit_distances) if edit_distances else 0.0,
        holdout_safety_preservation=statistics.mean(safety_preservations) if safety_preservations else 1.0,
        holdout_field_retention=statistics.mean(field_retentions) if field_retentions else 1.0,
        per_section={k: statistics.mean(v) for k, v in per_section_acc.items()},
        rules_total=len(memory.rules),
    )


def train_loop(
    train_pdfs: list[Path],
    holdout_pdfs: list[Path],
    n_iterations: int = 5,
    memory_path: Path = config.OUTPUTS_DIR / "memory.json",
    metrics_path: Path = config.OUTPUTS_DIR / "learning_metrics.json",
) -> list[IterationMetrics]:
    config.ensure_dirs()
    memory = CorrectionMemory()
    metrics: list[IterationMetrics] = []

    # Iteration 0: baseline (no rules)
    log.info("=== iteration 0 (baseline, empty memory) ===")
    base = evaluate(holdout_pdfs, memory)
    base.iteration = 0
    metrics.append(base)
    log.info("  holdout edit_distance_norm=%.3f safety_preservation=%.3f rules=%d",
             base.holdout_edit_distance_norm, base.holdout_safety_preservation, base.rules_total)

    for it in range(1, n_iterations + 1):
        log.info("=== iteration %d/%d — gathering edits on %d train patients ===",
                 it, n_iterations, len(train_pdfs))
        for pdf in train_pdfs:
            draft = _draft_one(pdf, memory)
            edited = review(draft)
            new_rules = extract_rules(draft, edited)
            added = memory.add(new_rules)
            log.info("  %s: %d new rules added (total=%d)", pdf.name, added, len(memory.rules))
        memory.save(memory_path)

        m = evaluate(holdout_pdfs, memory)
        m.iteration = it
        metrics.append(m)
        log.info("  holdout edit_distance_norm=%.3f safety_preservation=%.3f rules=%d",
                 m.holdout_edit_distance_norm, m.holdout_safety_preservation, m.rules_total)

    metrics_path.write_text(json.dumps([m.__dict__ for m in metrics], indent=2))
    return metrics


def plot_metrics(metrics: list[IterationMetrics], out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        log.warning("matplotlib not installed — skipping plot")
        return

    iters = [m.iteration for m in metrics]
    eds = [m.holdout_edit_distance_norm for m in metrics]
    safety = [m.holdout_safety_preservation for m in metrics]

    fig, ax1 = plt.subplots(figsize=(8, 5))
    color1 = "tab:blue"
    ax1.set_xlabel("iteration")
    ax1.set_ylabel("mean edit-distance-norm (holdout)", color=color1)
    ax1.plot(iters, eds, "o-", color=color1, label="edit_distance_norm")
    ax1.tick_params(axis="y", labelcolor=color1)
    ax1.set_ylim(0, max(0.5, max(eds) * 1.1))

    ax2 = ax1.twinx()
    color2 = "tab:green"
    ax2.set_ylabel("safety_preservation (holdout)", color=color2)
    ax2.plot(iters, safety, "s--", color=color2, label="safety_preservation")
    ax2.tick_params(axis="y", labelcolor=color2)
    ax2.set_ylim(0, 1.05)

    plt.title("Part 2 — Learning from simulated reviewer edits")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    log.info("plot saved to %s", out_path)
