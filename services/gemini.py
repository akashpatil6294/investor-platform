"""Shared Gemini access: model rotation across the configured list.

`GEMINI_CHAT_MODEL` may list several models (comma separated); the chat
runtime already validates them at startup. Non-chat callers (detection,
extraction, web search) use this helper so a single overloaded model does
not fail a pipeline stage.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger(__name__)

_retry_client = None
_retry_models: list[str] = []


def configured_models() -> list[str]:
    raw = os.getenv("GEMINI_CHAT_MODELS") or os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
    return [m.strip().removeprefix("models/") for m in raw.strip("\"'").split(",") if m.strip()]


def gemini_client():
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing")
    timeout = int(os.getenv("GEMINI_TIMEOUT_MS", "45000"))
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=timeout))


def generate_with_fallback(prompt: str, config=None, *, max_output_tokens: int = 4096,
                           temperature: float = 0.0, system: str | None = None,
                           low_latency: bool = False):
    """Generate once, trying each configured model in order.

    Returns (text, model_name). Raises the last error when every model fails.
    ``low_latency`` requests a zero thinking budget for flash-family models,
    falling back to default thinking when a model rejects it.
    """
    from google.genai import types

    client = gemini_client()
    base_config = config
    if base_config is None:
        base_config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            system_instruction=system,
        )
    errors: list[str] = []
    for model in configured_models()[:2]:
        configs = [base_config]
        if low_latency and "flash" in model:
            from dataclasses import replace

            try:
                configs = [
                    replace(
                        base_config,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                    base_config,
                ]
            except Exception:
                configs = [base_config]
        last_error = None
        for config_variant in configs:
            for attempt in range(1):
                try:
                    response = client.models.generate_content(
                        model=model, contents=prompt, config=config_variant)
                    text = response.text or ""
                    if text.strip():
                        return text, model
                    last_error = RuntimeError("empty response")
                    time.sleep(1.0 + attempt)
                    continue
                except Exception as exc:  # rotate to next config/model
                    last_error = exc
                    code = getattr(exc, "code", None)
                    if code in (400, 401, 403, 404):
                        break  # config/model unusable; rotate
                    if code in (429, 500, 502, 503):
                        time.sleep(2.0 * (attempt + 1))
                        continue
                    break
            else:
                pass
            if getattr(last_error, "code", None) in (400, 401, 403, 404):
                break
        errors.append(type(last_error).__name__)
    raise RuntimeError("All configured Gemini models failed: " + "; ".join(errors))


def generate_structured(prompt: str, schema: dict, *, max_output_tokens: int = 4096,
                        temperature: float = 0.0, system: str | None = None):
    """Structured JSON output; returns (dict, model_name)."""
    import json

    from google.genai import types

    config = types.GenerateContentConfig(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        system_instruction=system,
        response_mime_type="application/json",
        response_schema=_strip_openai_keys(schema),
    )
    text, model = generate_with_fallback(prompt, config=config)
    try:
        return json.loads(text), model
    except json.JSONDecodeError as exc:
        raise RuntimeError("Gemini returned invalid JSON") from None


def _strip_openai_keys(schema):
    """Convert OpenAI-style schema bits the Gemini endpoint rejects:
    drop ``additionalProperties`` and turn ``type`` unions like
    ``["number", "null"]`` into ``type`` + ``nullable: True``."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key == "additionalProperties":
                continue
            if key == "type" and isinstance(value, list):
                types_list = [t for t in value if t != "null"]
                out["type"] = types_list[0] if types_list else "string"
                if "null" in value:
                    out["nullable"] = True
                continue
            out[key] = _strip_openai_keys(value)
        return out
    if isinstance(schema, list):
        return [_strip_openai_keys(item) for item in schema]
    return schema
