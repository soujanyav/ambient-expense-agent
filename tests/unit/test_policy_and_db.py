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

from app.database import add_expense, get_expenses_by_month, init_db, update_expense
from app.tools import evaluate_policy, get_monthly_expenses_report, log_expense


def test_evaluate_policy_rules():
    assert evaluate_policy(45.0, "necessity") == "auto_approved"
    assert evaluate_policy(99.99, "necessity") == "auto_approved"
    assert evaluate_policy(100.0, "necessity") == "needs_review"
    assert evaluate_policy(150.0, "necessity") == "needs_review"
    assert evaluate_policy(50.0, "entertainment") == "needs_review"
    assert evaluate_policy(80.0, "luxury") == "needs_review"


def test_database_crud(tmp_path, monkeypatch):
    test_db = tmp_path / "test_expenses.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)

    init_db()
    res = add_expense("Test milk", 5.0, "necessity", "auto_approved", "2026-07")
    assert res["id"] == 1
    assert res["status"] == "auto_approved"

    expenses = get_expenses_by_month("2026-07")
    assert len(expenses) == 1
    assert expenses[0]["description"] == "Test milk"

    updated = update_expense(1, amount=6.0, status="auto_approved")
    assert updated["amount"] == 6.0


def test_tools_monthly_report(tmp_path, monkeypatch):
    test_db = tmp_path / "test_report.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)

    log_expense("Groceries", 45.0, "necessity", month="2026-07")
    log_expense("Concert", 75.0, "entertainment", month="2026-07")

    report = get_monthly_expenses_report("2026-07")
    assert report["total_spent"] == 120.0
    assert report["category_totals"]["necessity"] == 45.0
    assert report["category_totals"]["entertainment"] == 75.0
    assert report["auto_approved_count"] == 1
    assert report["needs_review_count"] == 1
