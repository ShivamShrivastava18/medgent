"""Stage 3 — Independent verifier (the safety floor).

The verifier is a fresh Flash call that sees ONLY the proposed text and the cited
excerpts — NOT the original PDF. For each sentence in a narrative field, it asks:
"Is this sentence supported by ≥1 cited excerpt?" If not, the sentence is stripped
and a SafetyFlag is recorded.

Conservative bias: if uncertain, mark NOT_SUPPORTED. Better to strip a legitimate
sentence than to let an unsupported one through.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from . import config
from .gemini_client import call_structured
from .models import (
    Citation,
    DischargeSummary,
    FieldStatus,
    SafetyFlag,
    Severity,
    ValuedField,
)

log = logging.getLogger("medgent.verifier")


_NARRATIVE_SECTIONS = ("hospital_course", "follow_up")


_VERIFIER_SYSTEM = """\
You verify whether a proposed clinical sentence is supported by cited evidence.

You will receive:
- A proposed sentence
- A list of cited excerpts (and only those — you DO NOT have the original PDF)

You return: SUPPORTED if at least one excerpt directly supports the sentence, otherwise
NOT_SUPPORTED. When uncertain, default to NOT_SUPPORTED (conservative bias). Do not use
general clinical knowledge to fill gaps — you can only verify what the excerpts show.
"""


class Verdict(BaseModel):
    decision: Literal["supported", "not_supported"]
    reason: str = Field(max_length=240)


def _split_sentences(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    # Lightweight sentence splitter; preserves trailing punctuation
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)
    return [p.strip() for p in parts if p.strip()]


def _verify_sentence(sentence: str, evidence: list[Citation]) -> Verdict:
    if not evidence:
        return Verdict(decision="not_supported", reason="no citations provided")
    bullets = "\n".join(
        f"  [{i}] (page {c.page_no}, {c.doc_type}): {c.excerpt}"
        for i, c in enumerate(evidence)
    )
    prompt = (
        f"Proposed sentence:\n  {sentence}\n\n"
        f"Cited excerpts (these are the ONLY source of truth available):\n{bullets}\n\n"
        "Decide SUPPORTED or NOT_SUPPORTED. Be conservative — if no excerpt clearly "
        "supports the sentence, return NOT_SUPPORTED."
    )
    try:
        out = call_structured(
            prompt,
            schema=Verdict,
            system=_VERIFIER_SYSTEM,
            model=config.MODEL_FLASH,
            temperature=0.0,
            max_output_tokens=600,
        )
    except Exception as exc:
        # Conservative bias: when the verifier itself fails, treat as not_supported.
        # Better to strip a legitimate sentence than to let an unsupported one through.
        log.warning("verifier call failed (%s) — defaulting to not_supported", str(exc)[:120])
        return Verdict(decision="not_supported", reason="verifier call failed; conservative default")
    return out if isinstance(out, Verdict) else Verdict.model_validate(out.model_dump())


def verify(draft: DischargeSummary) -> DischargeSummary:
    """Walk narrative sections; strip unsupported sentences; append safety_flags."""
    for section_name in _NARRATIVE_SECTIONS:
        field: ValuedField = getattr(draft, section_name)
        if field.status != FieldStatus.FILLED or not field.value or not field.citations:
            continue

        text = field.value if isinstance(field.value, str) else str(field.value)
        sentences = _split_sentences(text)
        if not sentences:
            continue

        kept: list[str] = []
        for sent in sentences:
            verdict = _verify_sentence(sent, field.citations)
            if verdict.decision == "supported":
                kept.append(sent)
            else:
                draft.safety_flags.append(
                    SafetyFlag(
                        field=section_name,
                        reason=f"verifier stripped unsupported sentence: {sent[:160]} — {verdict.reason}",
                        severity=Severity.MED,
                    )
                )
                log.info("verifier stripped sentence from %s: %s", section_name, sent[:80])

        if not kept:
            # Whole section unsupported — downgrade to FLAGGED
            setattr(
                draft,
                section_name,
                ValuedField(
                    value=None,
                    status=FieldStatus.FLAGGED,
                    citations=field.citations,
                    flag_reason="all sentences stripped by verifier — clinician must draft this section",
                ),
            )
        else:
            new_text = " ".join(kept)
            setattr(
                draft,
                section_name,
                ValuedField(
                    value=new_text,
                    status=FieldStatus.FILLED,
                    citations=field.citations,
                ),
            )

    return draft
