"""Prompt construction and validation-error feedback formatting.

The pipeline is the retry engine: after every failed attempt it re-issues the
prompt with (a) the previous raw assistant output and (b) the concrete schema
validation errors appended to the context — so the model sees exactly which
fields and cross-field rules failed and why.
"""

from __future__ import annotations

import json
from typing import Any

from extractor.schemas.base import BaseExtraction

SYSTEM_PROMPT = """\
You are a deterministic data-extraction engine. You read pruned DOM digests \
or PDF text and emit ONLY a JSON object that validates against the given \
JSON Schema.

Rules:
- Output exactly one JSON object. No prose, no markdown fences, no keys \
outside the schema.
- Copy values verbatim from the source; never invent values. Use null for \
anything genuinely absent.
- Normalize currencies to ISO 4217 codes (e.g. $ -> USD, € -> EUR) and dates \
to ISO 8601 (YYYY-MM-DD) when the schema demands it.
- Cross-field rules in the schema are hard constraints: if the numbers do not \
reconcile, re-read the source and fix the values, do not fudge them.
"""


def build_messages(
    *,
    schema: type[BaseExtraction],
    source_text: str,
    source_kind: str,
    source_origin: str,
    images: list[dict[str, str]] | None = None,
    escalation: str | None = None,
) -> list[dict[str, Any]]:
    """Build the base message list for an extraction attempt.

    ``images`` is a list of ``{"mime": ..., "data_b64": ...}`` dicts appended
    to the user message (scanned PDF pages).
    """
    schema_json = json.dumps(schema.model_json_schema(), indent=2)
    rules = schema.validator_contract()

    context = f"[source: {source_kind} | {source_origin}]"
    if escalation:
        context += f"\n[escalation: {escalation}]"

    user_text = (
        f"{context}\n\n"
        f"## Task\n{schema.schema_description}\n\n"
        f"## Validation rules (all must hold)\n{rules}\n\n"
        f"## JSON Schema\n{schema_json}\n\n"
        f"## Document\n{source_text}"
    )

    content: list[dict[str, Any] | str] = []
    if images:
        for img in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{img['mime']};base64,{img['data_b64']}"},
                }
            )
        content.append({"type": "text", "text": user_text})
        user_content: Any = content
    else:
        user_content = user_text

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_feedback_message(
    *,
    errors: list[str],
    previous_output: str | None,
    escalation_context: str | None = None,
) -> dict[str, Any]:
    """Build the retry user message: previous output + exact validation errors."""
    lines = [
        "Your previous answer failed schema validation. Fix it and output the "
        "corrected JSON object only.",
        "",
        "## Validation errors (must all be resolved)",
    ]
    for i, err in enumerate(errors, 1):
        lines.append(f"{i}. {err}")
    if previous_output:
        lines += ["", "## Your previous (invalid) output", previous_output]
    if escalation_context:
        lines += [
            "",
            "## Additional source context (re-injected because fields were missing)",
            escalation_context,
        ]
    lines += [
        "",
        "Return the complete corrected JSON object matching the schema exactly.",
    ]
    return {"role": "user", "content": "\n".join(lines)}


def excerpt(text: str, head: int = 900, tail: int = 500) -> str:
    """Head+tail excerpt for audit records (keeps traces bounded)."""
    if len(text) <= head + tail + 20:
        return text
    return f"{text[:head]}\n…[{len(text) - head - tail} chars omitted]…\n{text[-tail:]}"
