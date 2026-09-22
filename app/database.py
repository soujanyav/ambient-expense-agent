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

import datetime
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).parent.parent / "data" / "expenses.db"


def get_db_connection() -> sqlite3.Connection:
    """Connect to SQLite DB, ensuring directory exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Initialize database tables for expenses and category preferences."""
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
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
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
        conn.commit()


def add_expense(
    description: str,
    amount: float,
    category: str,
    status: str,
    month: str | None = None,
) -> dict[str, Any]:
    """Add a new expense record to the database."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    if not month:
        month = datetime.datetime.now().strftime("%Y-%m")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO expenses (description, amount, category, status, month, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (description, amount, category.lower(), status.lower(), month, now, now),
        )
        expense_id = cursor.lastrowid
        conn.commit()

    return {
        "id": expense_id,
        "description": description,
        "amount": amount,
        "category": category.lower(),
        "status": status.lower(),
        "month": month,
        "created_at": now,
        "updated_at": now,
    }


def update_expense(
    expense_id: int,
    description: str | None = None,
    amount: float | None = None,
    category: str | None = None,
    status: str | None = None,
) -> dict[str, Any] | None:
    """Update an existing expense record by ID."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        row = cursor.fetchone()
        if not row:
            return None

        current_desc = description if description is not None else row["description"]
        current_amount = amount if amount is not None else row["amount"]
        current_cat = category.lower() if category is not None else row["category"]
        current_status = status.lower() if status is not None else row["status"]

        cursor.execute(
            """
            UPDATE expenses
            SET description = ?, amount = ?, category = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                current_desc,
                current_amount,
                current_cat,
                current_status,
                now,
                expense_id,
            ),
        )
        conn.commit()

        cursor.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,))
        updated_row = cursor.fetchone()
        return dict(updated_row) if updated_row else None


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
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT classification FROM category_preferences WHERE category_name = ?",
            (category_name.lower().strip(),),
        )
        row = cursor.fetchone()
        return row["classification"] if row else None


def save_category_preference(category_name: str, classification: str) -> dict[str, str]:
    """Save user classification preference for a category."""
    init_db()
    now = datetime.datetime.now(datetime.UTC).isoformat()
    cat = category_name.lower().strip()
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

    return {"category": cat, "classification": cls, "status": "saved"}
