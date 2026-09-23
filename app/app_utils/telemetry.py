# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Observability, OpenTelemetry distributed tracing, PII redaction, and Intent-vs-Outcome audit logging."""

import json
import logging
import os
import re
from typing import Any

import google.auth
from google.adk.cli.api_server import _setup_instrumentation_lib_if_installed
from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
from google.adk.telemetry.setup import maybe_set_otel_providers
from opentelemetry import trace

# Compiled regex patterns for deterministic PII scrubbing before DB persistence & log emission
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Credit / Debit Card Numbers (13-19 digits, spaced or dashed)
    (
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "[REDACTED_CARD]",
    ),
    # US Social Security Numbers (XXX-XX-XXXX)
    (
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    # Email Addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    # Phone Numbers (US & International formats)
    (
        re.compile(
            r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
        ),
        "[REDACTED_PHONE]",
    ),
    # Bank Account / Routing keywords followed by digits
    (
        re.compile(r"(?i)\b(account|acct|routing)\s*#?\s*\d{6,17}\b"),
        r"\1 [REDACTED_ACCOUNT]",
    ),
]

# In-memory ring buffer of recent intent-vs-outcome audit events for diagnostics & tests
AUDIT_LOG_BUFFER: list[dict[str, Any]] = []


def redact_pii(text: str | None) -> str:
    """Scrub sensitive PII (credit cards, SSNs, emails, phone numbers, bank accounts) from text.

    Args:
        text: Raw user input, expense description, or log string.

    Returns:
        Sanitized string safe for SQLite storage and Cloud Logging export.
    """
    if not text:
        return ""
    sanitized = str(text)
    for pattern, replacement in _PII_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def redact_data_pii(data: Any) -> Any:
    """Recursively scrub PII across dictionaries, lists, and primitive values."""
    if isinstance(data, str):
        return redact_pii(data)
    if isinstance(data, dict):
        return {k: redact_data_pii(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_data_pii(item) for item in data]
    return data


def record_intent_vs_outcome(
    tool_name: str,
    intended_action: dict[str, Any],
    actual_outcome: dict[str, Any],
    duration_ms: float = 0.0,
) -> dict[str, Any]:
    """Explicitly log and trace the agent's intended action versus actual execution outcome.

    Emits both a structured JSON audit entry and OpenTelemetry span attributes.
    """
    safe_intent = redact_data_pii(intended_action)
    safe_outcome = redact_data_pii(actual_outcome)

    expected_status = safe_intent.get("expected_policy_status")
    actual_status = (
        safe_outcome.get("expense", {}).get("status")
        if isinstance(safe_outcome.get("expense"), dict)
        else safe_outcome.get("status")
    )
    outcome_matched = (
        True
        if expected_status is None or expected_status == actual_status
        else False
    )

    audit_record = {
        "event_type": "AGENT_INTENT_VS_OUTCOME_AUDIT",
        "tool_name": tool_name,
        "intended_action": safe_intent,
        "actual_outcome": safe_outcome,
        "intent_outcome_match": outcome_matched,
        "duration_ms": round(duration_ms, 2),
    }

    AUDIT_LOG_BUFFER.append(audit_record)
    if len(AUDIT_LOG_BUFFER) > 200:
        AUDIT_LOG_BUFFER.pop(0)

    # Attach to current OpenTelemetry span
    tracer = trace.get_tracer("ambient_expense_agent.audit")
    with tracer.start_as_current_span(f"audit.{tool_name}") as span:
        span.set_attribute("agent.tool_name", tool_name)
        span.set_attribute(
            "agent.intended_action", json.dumps(safe_intent, default=str)
        )
        span.set_attribute(
            "agent.actual_outcome", json.dumps(safe_outcome, default=str)
        )
        span.set_attribute("agent.intent_outcome_match", outcome_matched)
        span.set_attribute("agent.duration_ms", round(duration_ms, 2))

    logging.info(json.dumps(audit_record, default=str))
    return audit_record


def setup_telemetry() -> str | None:
    """Configure GenAI prompt/response logging via OpenTelemetry."""
    # Keep full prompts/responses out of trace span attributes (use GenAI logging instead).
    os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "false")

    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )
    if bucket and capture_content != "false":
        logging.info(
            "Prompt-response logging enabled - mode: NO_CONTENT (metadata only, no prompts/responses)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.environ.get("COMMIT_SHA", "dev")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=ambient-expense-agent,service.version={commit_sha}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
    else:
        logging.info(
            "Prompt-response logging disabled (set LOGS_BUCKET_NAME=gs://your-bucket and OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT to enable)"
        )

    # Set up OpenTelemetry exporters for Cloud Trace and Cloud Logging
    try:
        credentials, project_id = google.auth.default()
        otel_hooks = get_gcp_exporters(
            enable_cloud_tracing=True,
            enable_cloud_metrics=False,
            enable_cloud_logging=True,
            google_auth=(credentials, project_id),
        )
        otel_resource = get_gcp_resource(project_id)
        maybe_set_otel_providers(
            otel_hooks_to_setup=[otel_hooks],
            otel_resource=otel_resource,
        )
    except Exception as exc:
        logging.warning("OpenTelemetry GCP exporter fallback in local/test mode: %s", exc)

    # Set up GenAI SDK instrumentation
    _setup_instrumentation_lib_if_installed()

    return bucket
