"""Gemini client: model fallback, retry on overload, JSON mode, fixture mode.

The fallback/retry shape is lifted from Daily-news-IT-Guy, where it was
learned the hard way on the same free-tier key:

  429/5xx "high demand"   transient, worst on the newest model -> back off
  404 "not available"     permanent for this key -> next model in the list

What differs is the model list. The news bot puts *-lite first because one
line of summary does not need much. Here the model writes Terraform that has
to survive `terraform validate` and a real Checkov run, and every failed
attempt costs a repair round trip (another call plus ~1 min of Checkov), so
a full Flash model is the cheaper choice overall. Lite stays at the end as a
last resort for a day when every Flash tier is saturated -- the gates, not the
model, guarantee correctness, so a weaker model can only cost repairs, never
a wrong post.

Override without a commit by setting the GEMINI_MODEL repository variable.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from common import log

# `or`, not a get() default: an unset repository variable arrives as "".
GEMINI_MODELS = [
    m.strip() for m in (
        os.environ.get("GEMINI_MODEL")
        or "gemini-3.7-flash,gemini-3.8-flash,gemini-3.5-flash,gemini-3.5-flash-lite"
    ).split(",") if m.strip()
]
GEMINI_ATTEMPTS = 3
# Low but not zero: code should be boring and repeatable, while the post text
# still benefits from a little variety across 30 days of the same structure.
TEMPERATURE = 0.3


class LLMUnavailable(RuntimeError):
    """No model in the list would answer. Not the rule's fault."""


class LLM:
    def __init__(self, system_prompt: str, fixture_dir: str | None = None):
        self.system_prompt = system_prompt
        # LLM_FIXTURE_DIR replays recorded answers (content.json,
        # diagram.json) instead of calling Gemini -- for tests and for
        # previewing the whole pipeline without an API key.
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.model_used: str | None = None
        self.calls = 0

    def generate_json(self, prompt: str, label: str) -> dict:
        """One call that must return a JSON object. Raises on non-JSON.

        JSON-parse failure is raised as ValueError so the caller treats it
        like any other gate failure (schema gate) and repairs it.
        """
        self.calls += 1
        text = self._fixture(label) if self.fixture_dir else self._gemini(prompt, label)
        try:
            data = json.loads(_strip_fences(text))
        except json.JSONDecodeError as exc:
            raise ValueError(f"model output is not valid JSON: {exc}\n{text[:500]}")
        if not isinstance(data, dict):
            raise ValueError("model output is JSON but not an object")
        return data

    def _fixture(self, label: str) -> str:
        kind = label.split("/")[0]                  # "content/repair-1" -> content
        self.model_used = "fixture"
        return (self.fixture_dir / f"{kind}.json").read_text(encoding="utf-8")

    def _gemini(self, prompt: str, label: str) -> str:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise LLMUnavailable("GEMINI_API_KEY is not set (or use LLM_FIXTURE_DIR)")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            # JSON mode stops the model wrapping output in prose or markdown
            # fences. We deliberately do not pass response_schema: Gemini
            # accepts only a subset of JSON Schema, and our own jsonschema
            # gate gives the model a precise error to repair from anyway.
            response_mime_type="application/json",
            temperature=TEMPERATURE,
        )
        for model in GEMINI_MODELS:
            for attempt in range(1, GEMINI_ATTEMPTS + 1):
                try:
                    response = client.models.generate_content(
                        model=model, contents=prompt, config=config)
                    text = (response.text or "").strip()
                    if text:
                        log.info("Gemini %s: answered by %s", label, model)
                        self.model_used = model
                        return text
                    break
                except Exception as exc:
                    transient = any(code in str(exc)
                                    for code in ("429", "500", "502", "503", "504"))
                    if not transient:
                        log.warning("Gemini %s: %s unusable, trying next (%s)",
                                    label, model, str(exc)[:160])
                        break
                    if attempt == GEMINI_ATTEMPTS:
                        log.warning("Gemini %s: %s still failing after %d attempts",
                                    label, model, GEMINI_ATTEMPTS)
                        break
                    delay = 5 * 2 ** attempt        # 10s, 20s
                    log.warning("Gemini %s: %s attempt %d/%d failed, retry in %ds",
                                label, model, attempt, GEMINI_ATTEMPTS, delay)
                    time.sleep(delay)
        raise LLMUnavailable(f"no model in {', '.join(GEMINI_MODELS)} would answer")


def _strip_fences(text: str) -> str:
    """Belt and braces for a model that ignores JSON mode and adds ```json."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
