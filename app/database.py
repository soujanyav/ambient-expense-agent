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

"""SQLite persistence layer with automatic PII redaction and non-blocking async background tasks."""

import asyncio
from collections.abc import Callable
import concurrent.futures
import datetime
import sqlite3
from pathlib import Path
from typing import Any

from app.app_utils.telemetry import redact_pii

DB_PATH = Path(__file__).parent.parent / "data" / "expenses.db"

# Dedicated background thread pool so memory/preference writes never block UI or event loops
_BACKGROUND_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="memory_bg_worker"
)
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def schedule_background_memory_task(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> concurrent.futures.Future[Any] | asyncio.Task[Any]:
    """Dispatch a persistence or memory consolidation operation to a non-blocking background task.

    If an asyncio event loop is running, schedules an `asyncio.Task` wrapping `asyncio.to_thread`.
    Otherwise submits to the background `ThreadPoolExecutor` for non-blocking execution.
    """
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(asyncio.to_thread(func, *args, **kwargs))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task
    except RuntimeError:
        return _BACKGROUND_EXECUTOR.submit(func, *args, **kwargs)


def get_db_connection() -> sqlite3.Connection:
    """Connect to SQLite DB, ensuring directory exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize database tables for expenses, category preferences, and audit logs."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                month TEXT NOT NULL,
                requester_name TEXT NOT NULL DEFAULT 'Child',
                parent_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Ensure backward-compatible schema migration for existing SQLite databases
        cursor.execute("PRAGMA table_info(expenses)")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        if "requester_name" not in existing_cols:
            cursor.execute(
                "ALTER TABLE expenses ADD COLUMN requester_name TEXT NOT NULL DEFAULT 'Child'"
            )
        if "parent_note" not in existing_cols:
            cursor.execute(
                "ALTER TABLE expenses ADD COLUMN parent_note TEXT NOT NULL DEFAULT ''"
            )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS category_preferences (
                category_name TEXT PRIMARY KEY,
                classification TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def record_memory_audit_event(event_type: str, summary: str) -> None:
    """Persist a redacted memory or HITL event into the SQLite audit log table."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    safe_summary = redact_pii(summary)
    with get_db_connection() as conn:
        conn.execute(
            "INSERT INTO memory_audit_log (event_type, summary, created_at) VALUES (?, ?, ?)",
            (event_type, safe_summary, now),
        )
        conn.commit()


def add_expense(
    description: str,
    amount: float,
    category: str,
    status: str,
    month: str | None = None,
    requester_name: str = "Child",
    parent_note: str = "",
) -> dict[str, Any]:
    """Add a new expense record to the database with automatic PII redaction."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    if not month:
        month = datetime.datetime.now().strftime("%Y-%m")

    safe_desc = redact_pii(description)
    safe_requester = redact_pii(requester_name) or "Child"
    safe_note = redact_pii(parent_note)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO expenses (
                description, amount, category, status, month,
                requester_name, parent_note, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                safe_desc,
                amount,
                category.lower(),
                status.lower(),
                month,
                safe_requester,
                safe_note,
                now,
                now,
            ),
        )
        expense_id = cursor.lastrowid
        conn.commit()

    schedule_background_memory_task(
        record_memory_audit_event,
        "EXPENSE_CREATED",
        f"Expense #{expense_id} ({safe_desc}) logged with status={status.lower()}",
    )

    return {
        "id": expense_id,
        "description": safe_desc,
        "amount": amount,
        "category": category.lower(),
        "status": status.lower(),
        "month": month,
        "requester_name": safe_requester,
        "parent_note": safe_note,
        "created_at": now,
        "updated_at": now,
    }


def update_expense(
    expense_id: int,
    description: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    status: str | None = None,
    parent_note: str | None = None,
) -> dict[str, Any] | None:
    """Update an existing expense record by ID with PII redaction."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        row = cursor.fetchone()
        if not row:
            return None

        current_desc = (
            redact_pii(description) if description is not None else row["description"]
        )
        current_amount = amount if amount is not None else row["amount"]
        current_cat = category.lower() if category is not None else row["category"]
        current_status = status.lower() if status is not None else row["status"]
        current_note = (
            redact_pii(parent_note)
            if parent_note is not None
            else (row["parent_note"] if "parent_note" in row.keys() else "")
        )

        cursor.execute(
            """
            UPDATE expenses
            SET description = ?, amount = ?, category = ?, status = ?, parent_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                current_desc,
                current_amount,
                current_cat,
                current_status,
                current_note,
                now,
                expense_id,
            ),
        )
        conn.commit()

        cursor.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        updated_row = cursor.fetchone()
        result = dict(updated_row) if updated_row else None

    if result:
        schedule_background_memory_task(
            record_memory_audit_event,
            "EXPENSE_UPDATED",
            f"Expense #{expense_id} updated to status={current_status}",
        )
    return result


def get_expense_by_id(expense_id: int) -> dict[str, Any] | None:
    """Retrieve a single expense record by its ID across any month."""
    init_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_expenses_by_month(month: str | None = None) -> list[dict[str, Any]]:
    """Fetch all expenses for a given month (YYYY-MM). Default is current month."""
    init_db()
    if not month:
        month = datetime.datetime.now().strftime("%Y-%m")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM expenses WHERE month = ? ORDER BY id ASC", (month,)
        )
        rows = cursor.fetchall()
        return [dict(r) for r in rows]


def get_category_preference(category_name: str) -> str | None:
    """Retrieve saved category preference if exists."""
    init_db()
    safe_key = redact_pii(category_name).lower().strip()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT classification FROM category_preferences WHERE category_name = ?",
            (safe_key,),
        )
        row = cursor.fetchone()
        if row:
            return row["classification"]

        # Also check substring matches in stored preferences
        cursor.execute("SELECT category_name, classification FROM category_preferences")
        for pref_row in cursor.fetchall():
            if pref_row["category_name"] in safe_key:
                return pref_row["classification"]
        return None


def get_all_category_preferences() -> list[dict[str, str]]:
    """Fetch all saved category preferences."""
    init_db()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT category_name, classification, updated_at FROM category_preferences ORDER BY category_name ASC"
        )
        return [dict(r) for r in cursor.fetchall()]


def save_category_preference(category_name: str, classification: str) -> dict[str, str]:
    """Save user classification preference for a category (with non-blocking audit log)."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    cat = redact_pii(category_name).lower().strip()
    cls = classification.lower().strip()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO category_preferences (category_name, classification, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(category_name) DO UPDATE SET classification = excluded.classification, updated_at = excluded.updated_at
            """,
            (cat, cls, now),
        )
        conn.commit()

    schedule_background_memory_task(
        record_memory_audit_event,
        "PREFERENCE_SAVED",
        f"Saved category rule '{cat}' -> '{cls}'",
    )

    return {"category": cat, "classification": cls, "status": "saved"}
