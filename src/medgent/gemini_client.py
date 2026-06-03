"""Thin wrapper around google-genai (Vertex AI). Keeps SDK quirks in one place."""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any, Optional, Type

from google import genai
from google.genai import types
from pydantic import BaseModel

from . import config

log = logging.getLogger("medgent.gemini")


class GeminiError(RuntimeError):
    pass


_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True,
            project=config.GCP_PROJECT,
            location=config.GCP_LOCATION,
        )
    return _client


def _retry(call, attempts: int = 3, base_sleep: float = 1.0):
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 — broad on purpose; provider errors vary
            last = exc
            # Last attempt: bail.
            if i == attempts - 1:
                break
            sleep = base_sleep * (2**i) + random.random() * 0.5
            log.warning("Gemini call failed (attempt %d/%d): %s — retrying in %.1fs", i + 1, attempts, exc, sleep)
            time.sleep(sleep)
    raise GeminiError(f"Gemini call failed after {attempts} attempts: {last}") from last


def call_text(
    prompt: str,
    *,
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 0.2,
    max_output_tokens: int = 4096,
    thinking_budget: Optional[int] = None,
) -> str:
    """Plain text → text. Used for short reasoning calls."""
    cfg_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    if system:
        cfg_kwargs["system_instruction"] = system
    if thinking_budget is not None:
        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    cfg = types.GenerateContentConfig(**cfg_kwargs)

    def go() -> str:
        resp = get_client().models.generate_content(
            model=model or config.MODEL_FLASH,
            contents=prompt,
            config=cfg,
        )
        text = (resp.text or "").strip()
        if not text:
            raise GeminiError("empty response text")
        return text

    return _retry(go)


def call_structured(
    prompt: str,
    *,
    schema: Type[BaseModel],
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 0.1,
    max_output_tokens: int = 8192,
    thinking_budget: Optional[int] = None,
) -> BaseModel:
    """Prompt → instance of `schema`. Strict JSON mode via response_schema."""
    cfg_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "response_mime_type": "application/json",
        "response_schema": schema,
    }
    if system:
        cfg_kwargs["system_instruction"] = system
    if thinking_budget is not None:
        cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking_budget)

    cfg = types.GenerateContentConfig(**cfg_kwargs)

    def go() -> BaseModel:
        resp = get_client().models.generate_content(
            model=model or config.MODEL_FLASH,
            contents=prompt,
            config=cfg,
        )
        # google-genai returns parsed objects on resp.parsed when response_schema is set
        if getattr(resp, "parsed", None) is not None:
            return resp.parsed  # type: ignore[return-value]
        # Fallback: parse JSON ourselves
        raw = (resp.text or "").strip()
        if not raw:
            raise GeminiError("empty structured response")
        try:
            data = json.loads(raw)
            return schema.model_validate(data)
        except Exception as exc:
            raise GeminiError(f"could not parse structured response: {exc}\nraw:{raw[:500]}") from exc

    return _retry(go)


def call_multimodal_structured(
    text_prompt: str,
    *,
    image_paths: list[Path],
    schema: Type[BaseModel],
    model: Optional[str] = None,
    system: Optional[str] = None,
    temperature: float = 0.1,
    max_output_tokens: int = 8192,
) -> BaseModel:
    """Multimodal call: text + images → structured JSON."""
    parts: list[Any] = [text_prompt]
    for p in image_paths:
        mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
        parts.append(
            types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)
        )

    cfg_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "response_mime_type": "application/json",
        "response_schema": schema,
    }
    if system:
        cfg_kwargs["system_instruction"] = system

    cfg = types.GenerateContentConfig(**cfg_kwargs)

    def go() -> BaseModel:
        resp = get_client().models.generate_content(
            model=model or config.MODEL_FLASH,
            contents=parts,
            config=cfg,
        )
        if getattr(resp, "parsed", None) is not None:
            return resp.parsed  # type: ignore[return-value]
        raw = (resp.text or "").strip()
        try:
            return schema.model_validate(json.loads(raw))
        except Exception as exc:
            raise GeminiError(f"could not parse multimodal structured response: {exc}\nraw:{raw[:500]}") from exc

    return _retry(go)
