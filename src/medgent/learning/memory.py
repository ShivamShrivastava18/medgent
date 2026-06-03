"""Correction memory: the rule store.

Rules accumulate over iterations. The Compose stage consults this store and injects
the top-K relevant rules into the narrative-composition prompt as 'reviewer preferences
from prior edits'. Rules that would touch safety_flags are REJECTED at injection time —
this is the structural guarantee that learning cannot erode the Part 1 safety floor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from .diff import Rule


_SAFETY_TOXIC_KEYWORDS = ("safety_flag", "remove flag", "delete flag", "suppress flag")


class CorrectionMemory(BaseModel):
    """Persistent collection of learned Rules, keyed by (section, pattern_kind)."""

    rules: list[Rule] = Field(default_factory=list)

    # ----- accumulators ------------------------------------------------------

    def add(self, new_rules: Iterable[Rule]) -> int:
        """Add or update rules. Safety guard rejects rules referencing safety_flags."""
        added = 0
        for r in new_rules:
            if any(k in (r.hint or "").lower() for k in _SAFETY_TOXIC_KEYWORDS):
                continue  # safety guard
            existing = self._find(r.section, r.pattern_kind)
            if existing is not None:
                existing.frequency += 1
                existing.example_before = r.example_before or existing.example_before
                existing.example_after = r.example_after or existing.example_after
            else:
                self.rules.append(r.model_copy(deep=True))
                added += 1
        return added

    def _find(self, section: str, pattern_kind: str) -> Optional[Rule]:
        for r in self.rules:
            if r.section == section and r.pattern_kind == pattern_kind:
                return r
        return None

    # ----- queries -----------------------------------------------------------

    def for_section(self, section: str, *, top_k: int = 4) -> list[Rule]:
        """Most frequently seen rules for a section."""
        applicable = [r for r in self.rules if r.section == section]
        applicable.sort(key=lambda r: r.frequency, reverse=True)
        return applicable[:top_k]

    def for_compose_prompt(self, section: str, *, top_k: int = 4) -> str:
        rules = self.for_section(section, top_k=top_k)
        if not rules:
            return ""
        lines = ["Reviewer preferences learned from prior edits (apply when evidence permits):"]
        for r in rules:
            lines.append(f"  • {r.hint}")
        return "\n".join(lines)

    # ----- persistence -------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> "CorrectionMemory":
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text())
