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

"""Agentic guardrails, HITL execution stops, and Intent-vs-Outcome audit callbacks for ADK."""

import time
from typing import Any

from google.genai import types

from app.app_utils.telemetry import record_intent_vs_outcome, redact_data_pii, redact_pii
from app.memory import build_cached_system_context, compact_conversation_history
from app.schemas import build_error_response

_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "bypass policy",
    "force auto_approved",
    "override parent approval",
)


def before_model_guardrail_callback(
    callback_context: Any, llm_request: Any
) -> Any | None:
    """ADK before_model_callback: compacts conversation history, caches static context, and enforces input guardrails."""
    session_state: dict[str, Any] = getattr(callback_context, "state", {}) or {}

    # 1. Build/update cached context fingerprint for instructions + learned preferences
    cache_entry = build_cached_system_context("ambient_expense_policy_v2")
    session_state["context_cache_key"] = cache_entry["cache_key"]

    contents = getattr(llm_request, "contents", None)
    if not contents or not isinstance(contents, list):
        return None

    # 2. Check latest user prompt for prompt-injection attempts & scrub PII
    for content in contents:
        parts = getattr(content, "parts", None) or []
        for part in parts:
            raw_text = getattr(part, "text", None)
            if raw_text:
                lower_text = raw_text.lower()
                if any(pat in lower_text for pat in _INJECTION_PATTERNS):
                    return types.Content(
                        role="model",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    "Guardrail Blocked: Policy thresholds ($100 limit and "
                                    "Parent Approval for entertainment/luxury) cannot be bypassed."
                                )
                            )
                        ],
                    )
                part.text = redact_pii(raw_text)

    # 3. Sliding-window history compaction to prevent context bloat
    compacted_contents, summary_digest = compact_conversation_history(
        contents=contents,
        session_state=session_state,
    )
    if len(compacted_contents) != len(contents):
        llm_request.contents = compacted_contents

    return None


def before_tool_guardrail_callback(
    tool: Any, args: dict[str, Any], tool_context: Any
) -> dict[str, Any] | None:
    """ADK before_tool_callback: validates tool inputs, enforces pre-execution guardrails, and logs intended action."""
    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
    safe_args = redact_data_pii(args or {})

    # Validate amount guardrails before tool execution
    if "amount" in safe_args and safe_args["amount"] is not None:
        try:
            amt = float(safe_args["amount"])
            if amt <= 0:
                return build_error_response(
                    error_code="GUARDRAIL_INVALID_AMOUNT",
                    message=f"Amount must be strictly positive (received {amt}).",
                    recovery_instruction=(
                        "Ask the user for a valid positive dollar amount greater than $0.00, "
                        "then retry calling the tool."
                    ),
                    suggested_next_tool=tool_name,
                )
        except (TypeError, ValueError):
            return build_error_response(
                error_code="GUARDRAIL_NON_NUMERIC_AMOUNT",
                message=f"Amount '{safe_args['amount']}' is not a valid number.",
                recovery_instruction="Convert the dollar amount to a numeric float (e.g., 45.0) before calling the tool.",
                suggested_next_tool=tool_name,
            )

    # Determine expected policy outcome for Intent-vs-Outcome tracking
    from app.tools import get_auto_approval_limit

    expected_policy_status = None
    if tool_name in {"log_expense", "request_purchase_approval"}:
        amt = float(safe_args.get("amount", 0.0) or 0.0)
        cat = str(safe_args.get("category", "") or "").lower().strip()
        req_name = str(
            safe_args.get("requester_name") or safe_args.get("kid_name") or "Child"
        )
        limit = get_auto_approval_limit(req_name)
        if amt >= limit or cat in {"entertainment", "luxury"}:
            expected_policy_status = "needs_review"
        elif cat == "necessity" and amt < limit:
            expected_policy_status = "auto_approved"

    intended_action = {
        "tool_name": tool_name,
        "arguments": safe_args,
        "expected_policy_status": expected_policy_status,
        "started_at": time.perf_counter(),
    }

    state = getattr(tool_context, "state", None)
    if isinstance(state, dict):
        state["last_intended_action"] = intended_action

    return None


def after_tool_guardrail_callback(
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    tool_response: dict[str, Any] | Any,
) -> dict[str, Any] | None:
    """ADK after_tool_callback: enforces post-execution HITL stops and logs Intended Action vs Actual Outcome."""
    from app.tools import get_auto_approval_limit

    tool_name = getattr(tool, "name", None) or getattr(tool, "__name__", str(tool))
    state = getattr(tool_context, "state", None)
    intended_action = (
        state.get("last_intended_action")
        if isinstance(state, dict) and isinstance(state.get("last_intended_action"), dict)
        else {
            "tool_name": tool_name,
            "arguments": redact_data_pii(args or {}),
            "expected_policy_status": None,
            "started_at": time.perf_counter(),
        }
    )

    started_at = float(intended_action.get("started_at", time.perf_counter()))
    duration_ms = (time.perf_counter() - started_at) * 1000.0

    response_dict = (
        dict(tool_response)
        if isinstance(tool_response, dict)
        else {"result": tool_response}
    )

    # Post-execution policy invariant guardrail & HITL Execution Stop
    expense_obj = response_dict.get("expense")
    if isinstance(expense_obj, dict):
        amt = float(expense_obj.get("amount", 0.0) or 0.0)
        cat = str(expense_obj.get("category", "")).lower().strip()
        status = str(expense_obj.get("status", "")).lower().strip()
        req_name = str(expense_obj.get("requester_name", "Child"))
        limit = get_auto_approval_limit(req_name)

        # Hard invariant: >= requester's limit ($10 for Son, $30 for Daughter, $100 default) or entertainment/luxury MUST trigger HITL Parent Approval Stop
        if (amt >= limit or cat in {"entertainment", "luxury"}) and status not in {
            "approved",
            "rejected",
        }:
            expense_obj["status"] = "needs_review"
            response_dict["auto_approval_limit"] = limit
            response_dict["hitl_execution_stopped"] = True
            response_dict["hitl_status"] = "pending_parent_approval"
            response_dict["parent_approval_required"] = True

            # Trigger ADK native Human-in-the-Loop confirmation pause if supported by tool_context
            request_conf = getattr(tool_context, "request_confirmation", None)
            if callable(request_conf):
                try:
                    request_conf(
                        hint=(
                            f"Parent Approval Required: {req_name} "
                            f"requested ${amt:.2f} for '{expense_obj.get('description')}' ({cat}, limit=${limit:.0f}). "
                            "Approve or reject via Parent Portal or resolve_parent_approval."
                        ),
                        payload={
                            "expense_id": expense_obj.get("id"),
                            "amount": amt,
                            "limit": limit,
                            "category": cat,
                            "description": expense_obj.get("description"),
                        },
                    )
                except Exception:
                    pass

    record_intent_vs_outcome(
        tool_name=tool_name,
        intended_action=intended_action,
        actual_outcome=response_dict,
        duration_ms=duration_ms,
    )

    return response_dict if isinstance(tool_response, dict) else None
