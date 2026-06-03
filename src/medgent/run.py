"""CLI entry point. `medgent run <pdf>` is the one-command end-to-end path."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.logging import RichHandler

from . import config
from .agent.loop import run_agent
from .agent.trace import write_jsonl, write_markdown
from .compose import compose
from .pdf_index import build_index, encounter_summary
from .verifier import verify

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=False, show_path=False, console=console)],
    )


def _slug_for(pdf_path: Path) -> str:
    parent = pdf_path.parent.name
    return parent if parent and parent != "patients" else pdf_path.stem


@app.command()
def index(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    force: bool = typer.Option(False, "--force/--no-force", help="re-build even if cached"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Stage 0 only — build (or rebuild) the PDF index."""
    _setup_logging(verbose)
    config.ensure_dirs()
    idx = build_index(pdf, force=force)
    console.print(f"[green]indexed {len(idx.pages)} pages → {len(idx.encounters)} encounter(s)[/green]")
    console.print(f"  {encounter_summary(idx)}")


@app.command()
def run(
    pdf: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    force_index: bool = typer.Option(False, "--force-index", help="re-build PDF index"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    max_iterations: Optional[int] = typer.Option(None, "--max-iterations"),
) -> None:
    """End-to-end: index → agent loop → compose → verify → save draft + trace."""
    _setup_logging(verbose)
    config.ensure_dirs()
    slug = _slug_for(pdf)

    console.rule(f"[bold]medgent — {slug}")

    # Stage 0
    console.print("[cyan]stage 0[/cyan] indexing PDF…")
    idx = build_index(pdf, force=force_index)
    console.print(
        f"  {len(idx.pages)} pages, {len(idx.encounters)} encounter(s) → {encounter_summary(idx)}"
    )

    # Stage 1
    console.print("[cyan]stage 1[/cyan] agent loop…")
    state = run_agent(idx, max_iterations=max_iterations or config.MAX_ITERATIONS)
    console.print(
        f"  iterations={state.iteration}, tool_calls={state.tool_calls}, "
        f"safety_flags={len(state.safety_flags)}"
    )

    # Stage 2
    console.print("[cyan]stage 2[/cyan] composing draft…")
    draft = compose(state, source_pdf=str(pdf))

    # Stage 3
    console.print("[cyan]stage 3[/cyan] verifier (independent fact-check)…")
    draft = verify(draft)

    # Persist
    draft_path = config.DRAFTS_DIR / f"{slug}.json"
    md_path = config.DRAFTS_DIR / f"{slug}.md"
    trace_jsonl = config.TRACES_DIR / f"{slug}.jsonl"
    trace_md = config.TRACES_DIR / f"{slug}.md"

    draft_path.write_text(draft.model_dump_json(indent=2, exclude_none=False))
    md_path.write_text(_render_draft_markdown(draft))
    write_jsonl(state.trace, trace_jsonl)
    write_markdown(state.trace, trace_md)

    console.rule("[bold green]done")
    console.print(f"  draft (json) → {draft_path}")
    console.print(f"  draft (md)   → {md_path}")
    console.print(f"  trace (jsonl)→ {trace_jsonl}")
    console.print(f"  trace (md)   → {trace_md}")
    console.print(f"  safety flags: {len(draft.safety_flags)}")
    for sf in draft.safety_flags[:8]:
        console.print(f"    • [{sf.severity.value}] {sf.field}: {sf.reason[:120]}")


def _render_draft_markdown(draft) -> str:
    """Render the discharge summary as readable Markdown — what a clinician sees."""
    out: list[str] = []
    out.append(f"# Discharge Summary Draft\n")
    out.append(f"_Generated {draft.generated_at.isoformat()}_  ·  source: `{draft.source_pdf}`")
    out.append(f"\n**Agent metrics:** {draft.iterations_used} iterations, {draft.tool_calls_used} tool calls")
    out.append(f"\n**Safety flags:** {len(draft.safety_flags)}\n")

    def render(label: str, vf):
        status = vf.status.value.upper()
        if vf.status.value == "filled" and vf.value is not None:
            out.append(f"## {label}")
            if isinstance(vf.value, list):
                for item in vf.value:
                    if hasattr(item, "model_dump"):
                        d = item.model_dump()
                        line = ", ".join(f"{k}={v}" for k, v in d.items() if v is not None)
                        out.append(f"- {line}")
                    else:
                        out.append(f"- {item}")
            elif hasattr(vf.value, "model_dump"):
                d = vf.value.model_dump()
                for k, v in d.items():
                    if v is not None:
                        out.append(f"- **{k}:** {v}")
            else:
                out.append(str(vf.value))
            if vf.citations:
                cites = ", ".join(f"p.{c.page_no}" for c in vf.citations[:6])
                out.append(f"\n_Sources: {cites}_\n")
            else:
                out.append("")
        else:
            out.append(f"## {label}  —  **{status}**")
            if vf.flag_reason:
                out.append(f"> {vf.flag_reason}")
            if vf.conflicts:
                out.append("\nConflicting values found:")
                for c in vf.conflicts:
                    out.append(f"- {c}")
            out.append("")

    render("Patient demographics", draft.patient)
    render("Admission date", draft.admission_date)
    render("Discharge date", draft.discharge_date)
    render("Principal diagnosis", draft.principal_diagnosis)
    render("Secondary diagnoses", draft.secondary_diagnoses)
    render("Hospital course", draft.hospital_course)
    render("Procedures", draft.procedures)
    render("Admission medications", draft.admission_medications)
    render("Discharge medications", draft.discharge_medications)
    render("Medication changes", draft.medication_changes)
    render("Drug interactions", draft.drug_interactions)
    render("Allergies", draft.allergies)
    render("Follow-up instructions", draft.follow_up)
    render("Pending results", draft.pending_results)
    render("Discharge condition", draft.discharge_condition)

    if draft.safety_flags:
        out.append("## Safety flags raised")
        for sf in draft.safety_flags:
            out.append(f"- **[{sf.severity.value}]** `{sf.field}` — {sf.reason}")
        out.append("")

    out.append("\n---\n*This is a DRAFT for clinician review. Sections marked PENDING, MISSING, FLAGGED, or CONFLICTING require explicit clinician action before finalizing.*")
    return "\n".join(out)


if __name__ == "__main__":
    app()
