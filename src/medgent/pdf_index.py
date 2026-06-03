"""Stage 0 — PDF Index.

Render each page as PNG via pdftoppm, send each to Gemini Flash for structured extraction,
cluster pages into encounters by date proximity. The output (PatientIndex) is the queryable
substrate the agent loop runs against; the agent never re-reads the PDF directly.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import re
import subprocess
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Optional

from . import config
from .gemini_client import call_multimodal_structured
from .models import Encounter, PageExtract, PatientIndex

log = logging.getLogger("medgent.pdf_index")


# ----------------------------- PDF → PNG ---------------------------------------


def render_pages(pdf_path: Path, out_dir: Path, dpi: int = config.PDF_RENDER_DPI) -> list[Path]:
    """Render every page of `pdf_path` to PNG. Returns sorted list of PNG paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # If pages already rendered for this PDF (cache by file hash), reuse.
    digest = hashlib.sha1(pdf_path.read_bytes()).hexdigest()[:12]
    sub = out_dir / f"{pdf_path.stem}-{digest}"
    sub.mkdir(parents=True, exist_ok=True)
    existing = sorted(sub.glob("p-*.png"))
    if existing:
        return existing

    cmd = [
        "/opt/homebrew/bin/pdftoppm",
        "-r",
        str(dpi),
        "-png",
        str(pdf_path),
        str(sub / "p"),
    ]
    log.info("rendering %s → %s", pdf_path.name, sub)
    subprocess.run(cmd, check=True)
    return sorted(sub.glob("p-*.png"))


# ----------------------------- per-page extraction -----------------------------


_PAGE_EXTRACTION_SYSTEM = """\
You are extracting clinical information from ONE page of a hospital chart for a downstream
discharge-summary agent. The chart may be typed OR handwritten and may include forms,
tables, narratives, and photos of investigations.

CRITICAL RULES — these are non-negotiable safety constraints:
- If handwriting is unclear, set handwriting_confidence LOW (≤0.5) and put what you saw
  in `notes` field; do NOT guess clinical values to "look complete".
- Lab/form CELLS: an empty cell → status="pending" (default in clinical forms);
  a cell containing "—", "X", "Nil", or "Not done" → status="not_done";
  a cell with a numeric/text value → status="filled".
- Use ISO YYYY-MM-DD for dates. Input dates are typically DD/MM/YY (Indian convention),
  e.g. "28/2/26" → "2026-02-28". If a year is ambiguous, assume the current decade.
- Be specific in `free_text`: capture full visible text verbatim where possible.
- `is_handwritten` is True if the dominant content is handwritten.
"""


_PAGE_EXTRACTION_USER = """\
Extract structured data from page {page_no}. Schema fields:

doc_type — choose the BEST single label from:
  admission_note, progress_note, nurses_note, er_chart, vitals_chart, lab_form,
  lab_report, med_admin, consult_sheet, investigation_image, discharge_summary,
  blank, other.

dates_visible — every date you can read on the page (ISO).

free_text — verbatim transcription of visible text; preserve structure with newlines.

tables — every visible structured table or form. For each: title, headers, list of cells
with (row, col, header, text, is_blank, is_dash). Tables are crucial for lab forms.

medications_mentioned — every drug mentioned with name_as_written + (dose/route/frequency/duration if present) + timing label.

lab_values — every lab/test with (name, value, units, date, status). Use the cell semantics from rules.

diagnoses_mentioned — diagnoses, impressions, or admitting problems mentioned.

is_handwritten, handwriting_confidence (0..1) — be HONEST. Bad handwriting → low confidence.

notes — optional remarks about what you could not read or page quality.

Return a single JSON object matching the PageExtract schema. The page_no is {page_no}.
"""


def extract_page(png_path: Path, page_no: int) -> PageExtract:
    """LLM call: a single page image → PageExtract."""
    try:
        result = call_multimodal_structured(
            _PAGE_EXTRACTION_USER.format(page_no=page_no),
            image_paths=[png_path],
            schema=PageExtract,
            system=_PAGE_EXTRACTION_SYSTEM,
            temperature=0.1,
            max_output_tokens=6000,
        )
        # Ensure page_no is correct (the model sometimes echoes the number from prompt)
        if isinstance(result, PageExtract):
            result.page_no = page_no
            return result
        return PageExtract.model_validate(result.model_dump() if hasattr(result, "model_dump") else result)
    except Exception as exc:  # noqa: BLE001
        log.error("page %d extraction failed: %s", page_no, exc)
        # Don't crash whole run — return an empty page extract marked as failed.
        return PageExtract(
            page_no=page_no,
            doc_type="other",
            notes=f"extraction failed: {exc}",
            handwriting_confidence=0.0,
            is_handwritten=False,
        )


def extract_all_pages(png_paths: list[Path], max_workers: int = 4) -> list[PageExtract]:
    """Parallelize per-page extraction with bounded concurrency."""
    results: dict[int, PageExtract] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(extract_page, p, _page_no_from(p)): _page_no_from(p)
            for p in png_paths
        }
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            page_no = futures[fut]
            try:
                results[page_no] = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.error("worker page %d crashed: %s", page_no, exc)
                results[page_no] = PageExtract(
                    page_no=page_no, doc_type="other", notes=f"worker error: {exc}", handwriting_confidence=0.0
                )
            done += 1
            log.info("indexed %d/%d pages", done, len(png_paths))
    return [results[i] for i in sorted(results)]


def _page_no_from(png_path: Path) -> int:
    m = re.search(r"p-(\d+)\.png$", png_path.name)
    if not m:
        raise ValueError(f"cannot parse page number from {png_path.name}")
    return int(m.group(1))


# ----------------------------- encounter clustering ---------------------------


def _parse_iso(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def cluster_encounters(pages: list[PageExtract], window_days: int = config.ENCOUNTER_DAYS_WINDOW) -> list[Encounter]:
    """Group pages into encounters by date proximity.

    Heuristic: collect every visible date across pages; sort; cut clusters where the gap
    exceeds `window_days`. Pages with no date inherit the encounter of their nearest dated
    neighbor by page number. Most recent cluster is marked is_current.
    """
    page_dates: list[tuple[int, date]] = []
    for p in pages:
        for s in p.dates_visible:
            d = _parse_iso(s)
            if d is not None:
                page_dates.append((p.page_no, d))

    if not page_dates:
        # No dates anywhere — one encounter with all pages
        enc = Encounter(
            encounter_id="enc-1",
            pages=[p.page_no for p in pages],
            is_current=True,
        )
        for p in pages:
            p.encounter_id = enc.encounter_id
        return [enc]

    # Sort dates ascending and find cluster boundaries
    unique_dates = sorted({d for _, d in page_dates})
    clusters: list[list[date]] = [[unique_dates[0]]]
    for d in unique_dates[1:]:
        if (d - clusters[-1][-1]).days > window_days:
            clusters.append([d])
        else:
            clusters[-1].append(d)

    # Build encounters and assign pages
    encounters: list[Encounter] = []
    for i, cluster in enumerate(clusters, start=1):
        encounters.append(
            Encounter(
                encounter_id=f"enc-{i}",
                pages=[],
                earliest_date=cluster[0].isoformat(),
                latest_date=cluster[-1].isoformat(),
            )
        )

    def encounter_for_date(d: date) -> str:
        for enc in encounters:
            if enc.earliest_date and enc.latest_date:
                if date.fromisoformat(enc.earliest_date) <= d <= date.fromisoformat(enc.latest_date):
                    return enc.encounter_id
        # fallback: nearest
        nearest = min(
            encounters,
            key=lambda e: min(
                abs((d - date.fromisoformat(e.earliest_date)).days) if e.earliest_date else 10**9,
                abs((d - date.fromisoformat(e.latest_date)).days) if e.latest_date else 10**9,
            ),
        )
        return nearest.encounter_id

    # Assign pages with explicit dates first
    page_to_enc: dict[int, str] = {}
    for pno, d in page_dates:
        page_to_enc.setdefault(pno, encounter_for_date(d))

    # Fill in undated pages from nearest dated neighbor by page_no
    dated_pnos = sorted(page_to_enc)
    for p in pages:
        if p.page_no in page_to_enc:
            continue
        if not dated_pnos:
            page_to_enc[p.page_no] = encounters[-1].encounter_id
            continue
        nearest = min(dated_pnos, key=lambda x: abs(x - p.page_no))
        page_to_enc[p.page_no] = page_to_enc[nearest]

    for p in pages:
        p.encounter_id = page_to_enc[p.page_no]
        for enc in encounters:
            if enc.encounter_id == p.encounter_id and p.page_no not in enc.pages:
                enc.pages.append(p.page_no)

    # Most recent encounter (latest end date) = current
    encounters.sort(key=lambda e: e.latest_date or "0000-00-00")
    for enc in encounters:
        enc.is_current = False
    encounters[-1].is_current = True

    return encounters


# ----------------------------- public entry point -----------------------------


def index_path_for(pdf_path: Path) -> Path:
    digest = hashlib.sha1(pdf_path.read_bytes()).hexdigest()[:12]
    return config.INDEX_DIR / f"{pdf_path.stem}-{digest}.index.json"


def build_index(pdf_path: Path, *, force: bool = False) -> PatientIndex:
    """Render → extract → cluster → cache."""
    config.ensure_dirs()
    cache = index_path_for(pdf_path)
    if cache.exists() and not force:
        log.info("using cached index %s", cache.name)
        return PatientIndex.model_validate_json(cache.read_text())

    pngs = render_pages(pdf_path, config.PAGES_DIR)
    log.info("extracting %d pages…", len(pngs))
    pages = extract_all_pages(pngs)
    encounters = cluster_encounters(pages)
    idx = PatientIndex(source_pdf=str(pdf_path), pages=pages, encounters=encounters)
    cache.write_text(idx.model_dump_json(indent=2))
    log.info("index cached at %s", cache)
    return idx


# ----------------------------- helper queries on the index --------------------


def pages_of_encounter(idx: PatientIndex, encounter_id: Optional[str]) -> Iterable[PageExtract]:
    if encounter_id is None:
        yield from idx.pages
        return
    for p in idx.pages:
        if p.encounter_id == encounter_id:
            yield p


def current_encounter_id(idx: PatientIndex) -> Optional[str]:
    for enc in idx.encounters:
        if enc.is_current:
            return enc.encounter_id
    return None


def encounter_summary(idx: PatientIndex) -> str:
    parts = []
    for enc in idx.encounters:
        tag = " (current)" if enc.is_current else ""
        parts.append(
            f"{enc.encounter_id}{tag}: pages {min(enc.pages) if enc.pages else '?'}–{max(enc.pages) if enc.pages else '?'} "
            f"({enc.earliest_date}..{enc.latest_date})"
        )
    return " | ".join(parts)
