"""Trace persistence: JSONL during the run, Markdown for the demo video."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..models import StepPhase, StepRecord


def write_jsonl(steps: Iterable[StepRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for s in steps:
            f.write(s.model_dump_json() + "\n")


def render_markdown(steps: list[StepRecord]) -> str:
    """Human-readable trace. This is what the reviewer will read; this is what the video shows."""
    out = ["# Agent Step Trace\n"]
    for s in steps:
        header = f"## Step {s.step_no} — {s.phase.value}"
        if s.field_in_focus:
            header += f" — field: `{s.field_in_focus}`"
        out.append(header)
        out.append(f"**Reasoning.** {s.reasoning}")
        if s.action:
            out.append(f"**Action.** `{s.action}`")
        if s.inputs:
            out.append("**Inputs.**")
            out.append("```json")
            out.append(json.dumps(s.inputs, indent=2, default=str))
            out.append("```")
        if s.result_summary:
            out.append("**Result.**")
            out.append("```")
            out.append(s.result_summary[:1200])
            out.append("```")
        if s.next_decision:
            out.append(f"**Next.** {s.next_decision}")
        out.append("")  # blank line
    return "\n".join(out)


def write_markdown(steps: list[StepRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(steps))
