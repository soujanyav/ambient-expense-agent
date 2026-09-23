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

"""Context management, sliding-window history compaction, Gemini context caching, and async memory tasks."""

import hashlib
from typing import Any

from app.app_utils.telemetry import redact_pii
from app.database import (
    get_all_category_preferences,
    record_memory_audit_event,
    save_category_preference,
    schedule_background_memory_task,
)

try:
    from google.adk.agents.context_cache_config import ContextCacheConfig
except ImportError:
    ContextCacheConfig = None  # type: ignore[assignment,misc]

MAX_UNCOMPACTED_TURNS = 6
MAX_SUMMARY_CHARS = 1200

# In-memory context cache store keyed by instruction + preference fingerprint
_CONTEXT_CACHE_REGISTRY: dict[str, dict[str, Any]] = {}


def get_context_cache_config() -> Any:
    """Configure ADK / Gemini Context Caching to prevent static prompt & schema token bloat."""
    if ContextCacheConfig is not None:
        try:
            return ContextCacheConfig(
                min_tokens=1024,
                ttl_seconds=3600,
                cache_intervals=5,
            )
        except Exception:
            pass
    return {
        "enabled": True,
        "min_tokens": 1024,
        "ttl_seconds": 3600,
        "strategy": "system_instruction_and_preferences_prefix_cache",
    }


def build_cached_system_context(static_instruction: str) -> dict[str, Any]:
    """Build or retrieve a cached context prefix combining system instructions and learned category rules."""
    prefs = get_all_category_preferences()
    pref_lines = [f"- {p['category_name']} => {p['classification']}" for p in prefs]
    pref_block = (
        "\nLearned Family Category Preferences:\n" + "\n".join(pref_lines)
        if pref_lines
        else ""
    )
    combined = f"{static_instruction}{pref_block}"
    cache_key = hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

    if cache_key not in _CONTEXT_CACHE_REGISTRY:
        _CONTEXT_CACHE_REGISTRY[cache_key] = {
            "cache_key": cache_key,
            "cached_prefix": combined,
            "preferences_count": len(prefs),
            "hit_count": 0,
        }
    else:
        _CONTEXT_CACHE_REGISTRY[cache_key]["hit_count"] += 1

    return _CONTEXT_CACHE_REGISTRY[cache_key]


def compact_conversation_history(
    contents: list[Any],
    session_state: dict[str, Any] | None = None,
    max_turns: int = MAX_UNCOMPACTED_TURNS,
) -> tuple[list[Any], str]:
    """Sliding-window history compaction to prevent context window bloat in multi-turn sessions.

    When `contents` exceeds `max_turns`, older turns are summarized into a concise
    digest stored in `session_state['compacted_history_summary']`, keeping only the
    most recent `max_turns` messages verbatim.

    Args:
        contents: List of conversation Content objects or message dicts.
        session_state: Mutable ADK session state dictionary.
        max_turns: Maximum number of recent turns to retain uncompacted.

    Returns:
        Tuple of (compacted_contents_list, summary_digest_string).
    """
    if len(contents) <= max_turns:
        existing_summary = (
            session_state.get("compacted_history_summary", "")
            if isinstance(session_state, dict)
            else ""
        )
        return contents, existing_summary

    older_turns = contents[:-max_turns]
    recent_turns = contents[-max_turns:]

    digest_bullets: list[str] = []
    for item in older_turns:
        text_snippet = ""
        role = "user"
        if isinstance(item, dict):
            role = item.get("role", "user")
            text_snippet = str(item.get("content", ""))
        else:
            role = getattr(item, "role", "user") or "user"
            parts = getattr(item, "parts", None) or []
            extracted = [
                getattr(p, "text", "") for p in parts if getattr(p, "text", None)
            ]
            text_snippet = " ".join(extracted)

        safe_snippet = redact_pii(text_snippet).strip()
        if safe_snippet:
            digest_bullets.append(f"[{role}] {safe_snippet[:120]}")

    prior_digest = (
        session_state.get("compacted_history_summary", "")
        if isinstance(session_state, dict)
        else ""
    )
    combined_digest = "\n".join(
        ([prior_digest] if prior_digest else []) + digest_bullets
    )
    if len(combined_digest) > MAX_SUMMARY_CHARS:
        combined_digest = combined_digest[-MAX_SUMMARY_CHARS:]

    if isinstance(session_state, dict):
        session_state["compacted_history_summary"] = combined_digest
        session_state["compacted_turns_count"] = session_state.get(
            "compacted_turns_count", 0
        ) + len(older_turns)

    schedule_background_memory_task(
        record_memory_audit_event,
        "HISTORY_COMPACTED",
        f"Compacted {len(older_turns)} older turns into summary ({len(combined_digest)} chars).",
    )

    return recent_turns, combined_digest


async def save_category_preference_async(
    category_name: str, classification: str
) -> dict[str, Any]:
    """Non-blocking async memory helper to persist user category preferences in background."""
    task = schedule_background_memory_task(
        save_category_preference, category_name, classification
    )
    return {
        "status": "scheduled",
        "category": redact_pii(category_name).lower().strip(),
        "classification": classification.lower().strip(),
        "background_task_active": task is not None,
    }
