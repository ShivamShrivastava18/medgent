"""Tiny FastAPI server backing the demo UI.

Serves /api/{patients, patient/<n>/draft, patient/<n>/trace, part2, learning-curve}
plus the static index.html. Designed to be one-command — `python frontend/server.py`
from the repo root — so you don't have to switch screens during the video recording.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "outputs"
HERE = Path(__file__).resolve().parent
INDEX_HTML = HERE / "index.html"

app = FastAPI(title="medgent demo UI", docs_url=None, redoc_url=None)


# ----------------------------- helpers ---------------------------------------


def _read_draft(name: str) -> dict[str, Any]:
    p = OUTPUTS / "drafts" / f"{name}.json"
    if not p.exists():
        raise HTTPException(404, f"draft not found: {name}")
    return json.loads(p.read_text())


def _read_trace(name: str) -> list[dict[str, Any]]:
    p = OUTPUTS / "traces" / f"{name}.jsonl"
    if not p.exists():
        raise HTTPException(404, f"trace not found: {name}")
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# ----------------------------- routes ----------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if not INDEX_HTML.exists():
        return "<h1>index.html missing</h1>"
    return INDEX_HTML.read_text()


@app.get("/api/patients")
def list_patients() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted((OUTPUTS / "drafts").glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        out.append(
            {
                "name": p.stem,
                "source_pdf": d.get("source_pdf", ""),
                "safety_flags": len(d.get("safety_flags", [])),
                "iterations": d.get("iterations_used", 0),
                "tool_calls": d.get("tool_calls_used", 0),
                "is_real": p.stem == "patient_2",
            }
        )
    # Real patient first, then synth in numerical order
    out.sort(key=lambda x: (not x["is_real"], x["name"]))
    return out


@app.get("/api/patient/{name}/draft")
def draft(name: str) -> dict[str, Any]:
    return _read_draft(name)


@app.get("/api/patient/{name}/trace")
def trace(name: str) -> list[dict[str, Any]]:
    return _read_trace(name)


@app.get("/api/patient/{name}/manifest")
def manifest(name: str) -> dict[str, Any]:
    """Synthetic patients carry a manifest documenting their planted messiness."""
    p = ROOT / "data" / "synthetic" / name / "manifest.json"
    if not p.exists():
        return {"injected_messiness": [], "condition": None}
    return json.loads(p.read_text())


@app.get("/api/part2")
def part2() -> dict[str, Any]:
    metrics_path = OUTPUTS / "learning_metrics.json"
    memory_path = OUTPUTS / "memory.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else []
    memory = json.loads(memory_path.read_text()) if memory_path.exists() else {"rules": []}
    return {"metrics": metrics, "rules": memory.get("rules", [])}


@app.get("/api/learning-curve.png")
def learning_curve() -> FileResponse:
    p = OUTPUTS / "learning_curve.png"
    if not p.exists():
        raise HTTPException(404, "learning_curve.png not built yet — run train_loop first")
    return FileResponse(p, media_type="image/png")


# ----------------------------- entry ----------------------------------------


def main() -> None:
    host = "127.0.0.1"
    port = 8000
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    print(f"medgent demo UI → http://{host}:{port}")
    uvicorn.run("server:app", host=host, port=port, reload=False, log_level="info", app_dir=str(HERE))


if __name__ == "__main__":
    main()
