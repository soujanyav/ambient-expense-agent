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
from typing import Any

from app.database import (
    add_expense,
    get_category_preference,
    get_expenses_by_month,
    save_category_preference,
    update_expense,
)


def evaluate_policy(amount: float, category: str) -> str:
    """Evaluate whether an expense is auto_approved or needs_review.

    Rules:
    - Expense < $100 AND category == 'necessity' => 'auto_approved'
    - Expense >= $100 OR category in ('entertainment', 'luxury') => 'needs_review'
    """
    cat = category.lower().strip()
    if amount < 100.0 and cat == "necessity":
        return "auto_approved"
    return "needs_review"


def classify_category_fast(description: str, category: str = "") -> str:
    """Fast category classification using stored rules or keywords."""
    if category.strip():
        return category.lower().strip()

    desc_lower = description.lower()

    # Check database for saved user preference
    saved_pref = get_category_preference(description)
    if saved_pref:
        return saved_pref

    # Keyword heuristics for common necessities vs entertainment/luxury
    necessity_keywords = [
        "milk",
        "groceries",
        "grocery",
        "rent",
        "utility",
        "utilities",
        "electricity",
        "water",
        "gas",
        "doctor",
        "pharmacy",
        "medicine",
        "transit",
        "bus",
        "subway",
        "internet",
    ]
    entertainment_keywords = [
        "concert",
        "movie",
        "ticket",
        "game",
        "gaming",
        "dining out",
        "restaurant",
        "bar",
        "cocktail",
        "spa",
        "luxury",
        "vacation",
    ]

    if any(kw in desc_lower for kw in necessity_keywords):
        return "necessity"
    if any(kw in desc_lower for kw in entertainment_keywords):
        return "entertainment"

    return "necessity"  # Default fallback if unspecified


def log_expense(
    description: str,
    amount: float,
    category: str = "",
    month: str = "",
) -> dict[str, Any]:
    """Logs a new expense in a single fast execution turn.

    Args:
        description: Description of expense (e.g. 'Milk', 'Concert tickets').
        amount: Dollar amount.
        category: Optional category ('necessity', 'entertainment', 'luxury'). Auto-classified if empty.
        month: Optional month in 'YYYY-MM' format. Defaults to current month.

    Returns:
        Dictionary with saved expense details and policy status.
    """
    target_month = (
        month.strip() if month.strip() else datetime.datetime.now().strftime("%Y-%m")
    )
    final_category = classify_category_fast(description, category)
    status = evaluate_policy(amount, final_category)

    result = add_expense(
        description=description,
        amount=amount,
        category=final_category,
        status=status,
        month=target_month,
    )
    return {
        "status": "success",
        "message": f"Recorded expense #{result['id']} for '{description}' as '{status}'.",
        "expense": result,
    }


def update_expense_record(
    expense_id: int,
    description: str = "",
    amount: float | None = None,
    category: str = "",
) -> dict[str, Any]:
    """Updates an existing expense entry and re-evaluates approval status.

    Args:
        expense_id: ID of the expense record.
        description: New description (optional).
        amount: New dollar amount (optional).
        category: New category ('necessity', 'entertainment', 'luxury') (optional).

    Returns:
        Updated expense record.
    """
    desc_opt = description if description.strip() else None
    cat_opt = category if category.strip() else None

    monthly_all = get_expenses_by_month()
    match = next((item for item in monthly_all if item["id"] == expense_id), None)
    if not match:
        return {"status": "error", "message": f"Expense ID {expense_id} not found."}

    eval_amount = amount if amount is not None else match["amount"]
    eval_cat = cat_opt if cat_opt is not None else match["category"]

    new_status = evaluate_policy(eval_amount, eval_cat)

    updated = update_expense(
        expense_id=expense_id,
        description=desc_opt,
        amount=amount,
        category=cat_opt,
        status=new_status,
    )
    if not updated:
        return {
            "status": "error",
            "message": f"Failed to update expense ID {expense_id}.",
        }

    return {
        "status": "success",
        "message": f"Updated expense #{expense_id}. Status is '{new_status}'.",
        "expense": updated,
    }


def get_monthly_expenses_report(month: str = "") -> dict[str, Any]:
    """Retrieves all expenses for a given month with category breakdowns, totals, and review items.

    Args:
        month: Target month string in 'YYYY-MM' format. Leave empty for current month.

    Returns:
        Comprehensive monthly report with category totals and items needing review.
    """
    target_month = (
        month.strip() if month.strip() else datetime.datetime.now().strftime("%Y-%m")
    )
    expenses = get_expenses_by_month(target_month)

    total_spent = sum(e["amount"] for e in expenses)
    category_totals: dict[str, float] = {}
    needs_review: list[dict[str, Any]] = []
    auto_approved: list[dict[str, Any]] = []

    for e in expenses:
        cat = e["category"]
        category_totals[cat] = category_totals.get(cat, 0.0) + e["amount"]
        if e["status"] == "needs_review":
            needs_review.append(e)
        else:
            auto_approved.append(e)

    return {
        "month": target_month,
        "total_expenses_count": len(expenses),
        "total_spent": round(total_spent, 2),
        "category_totals": {k: round(v, 2) for k, v in category_totals.items()},
        "needs_review_count": len(needs_review),
        "needs_review_items": needs_review,
        "auto_approved_count": len(auto_approved),
        "auto_approved_items": auto_approved,
        "all_expenses": expenses,
    }


def save_category_rule(category_name: str, classification: str) -> dict[str, Any]:
    """Saves user preference for how a category should be classified ('necessity' or 'entertainment')."""
    result = save_category_preference(category_name, classification)
    return {"status": "success", "saved_rule": result}
