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

"""Expense validation, HITL parent approval, category learning, and spending analysis tools."""

import datetime
from typing import Any

from pydantic import ValidationError

from app.app_utils.telemetry import record_intent_vs_outcome, redact_pii
from app.database import (
    add_expense,
    get_category_preference,
    get_expense_by_id,
    get_expenses_by_month,
    save_category_preference,
    update_expense,
)
from app.schemas import (
    CategoryRuleInput,
    ExpenseLogInput,
    ExpenseUpdateInput,
    MonthlyReportInput,
    ParentDecisionInput,
    SpendingAnalysisInput,
    build_error_response,
)


def evaluate_policy(amount: float, category: str) -> str:
    """Evaluate whether an expense is auto_approved or needs_review (Parent HITL Gate).

    Args:
        amount: Positive dollar amount of the expense or purchase request.
        category: Expense category ('necessity', 'entertainment', or 'luxury').

    Returns:
        Policy status string:
        - 'auto_approved' if amount < $100.00 AND category == 'necessity'.
        - 'needs_review' if amount >= $100.00 OR category in ('entertainment', 'luxury').
    """
    cat = category.lower().strip()
    if amount < 100.0 and cat == "necessity":
        return "auto_approved"
    return "needs_review"


def classify_category_fast(description: str, category: str = "") -> str:
    """Classify an expense description using stored family preferences or keyword heuristics.

    Args:
        description: Item or merchant description (e.g., 'Milk', 'Roblox coins', 'School bus pass').
        category: Optional explicit category ('necessity', 'entertainment', 'luxury').

    Returns:
        Normalized category string ('necessity', 'entertainment', or 'luxury').
    """
    if category and category.strip():
        return category.lower().strip()

    desc_lower = redact_pii(description).lower()

    # 1. Check database for saved family/parent preference
    saved_pref = get_category_preference(desc_lower)
    if saved_pref:
        return saved_pref

    # 2. Keyword heuristics for necessities vs entertainment/luxury
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
        "thermometer",
        "transit",
        "bus",
        "subway",
        "internet",
        "school",
        "textbook",
        "lunch",
        "notebook",
        "tuition",
    ]
    luxury_keywords = [
        "luxury",
        "spa",
        "designer",
        "jewelry",
        "first class",
        "rolex",
        "vip",
    ]
    entertainment_keywords = [
        "concert",
        "movie",
        "ticket",
        "game",
        "gaming",
        "xbox",
        "playstation",
        "nintendo",
        "roblox",
        "toy",
        "boba",
        "candy",
        "ice cream",
        "dining out",
        "restaurant",
        "bar",
        "cocktail",
        "vacation",
        "streaming",
    ]

    if any(kw in desc_lower for kw in luxury_keywords):
        return "luxury"
    if any(kw in desc_lower for kw in entertainment_keywords):
        return "entertainment"
    if any(kw in desc_lower for kw in necessity_keywords):
        return "necessity"

    return "necessity"


def log_expense(
    description: str,
    amount: float,
    category: str = "",
    month: str = "",
    requester_name: str = "Child",
) -> dict[str, Any]:
    """Logs a new expense or pre-purchase approval request and enforces Parent HITL rules.

    Args:
        description: Description of the expense or item to purchase (e.g., 'Milk', 'Video Game').
        amount: Positive dollar amount of the item (e.g., 45.0).
        category: Optional category ('necessity', 'entertainment', 'luxury'). Auto-classified if empty.
        month: Optional target month in 'YYYY-MM' format. Defaults to current month if empty.
        requester_name: Name of the child or family member requesting or logging the purchase.

    Returns:
        Dictionary containing:
        - status: 'success' or 'error'
        - message: Summary of whether the item was 'auto_approved' or paused at the Parent Approval Gate ('needs_review')
        - hitl_execution_stopped: True if parent approval is required before purchasing
        - expense: Full persisted SQLite record
        - recovery_instruction: Present if status == 'error' to guide LLM self-correction
    """
    try:
        validated = ExpenseLogInput(
            description=description,
            amount=amount,
            category=category,
            month=month,
            requester_name=requester_name,
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_EXPENSE_INPUT",
            message=f"Validation failed for log_expense: {exc.errors()[0]['msg']}",
            recovery_instruction=(
                "Ensure `description` is non-empty, `amount` is a positive number (> 0), "
                "`category` is one of ('necessity', 'entertainment', 'luxury', or ''), "
                "and `month` matches 'YYYY-MM'."
            ),
            suggested_next_tool="log_expense",
        )

    target_month = (
        validated.month
        if validated.month
        else datetime.datetime.now().strftime("%Y-%m")
    )
    final_category = classify_category_fast(validated.description, validated.category)
    status = evaluate_policy(validated.amount, final_category)
    requires_parent_hitl = status == "needs_review"

    result = add_expense(
        description=validated.description,
        amount=validated.amount,
        category=final_category,
        status=status,
        month=target_month,
        requester_name=validated.requester_name,
    )

    hitl_notice = (
        "PAUSED FOR PARENT APPROVAL (HITL Stop): Purchase requires parent sign-off before buying."
        if requires_parent_hitl
        else "AUTO-APPROVED: Necessity under $100 is approved for immediate purchase."
    )

    response = {
        "status": "success",
        "message": (
            f"Recorded expense #{result['id']} for '{result['description']}' "
            f"(${result['amount']:.2f}, {final_category}) as '{status}'. {hitl_notice}"
        ),
        "hitl_execution_stopped": requires_parent_hitl,
        "parent_approval_required": requires_parent_hitl,
        "expense": result,
    }

    record_intent_vs_outcome(
        tool_name="log_expense",
        intended_action={
            "description": validated.description,
            "amount": validated.amount,
            "category": final_category,
            "expected_policy_status": status,
        },
        actual_outcome=response,
    )
    return response


def request_purchase_approval(
    description: str,
    amount: float,
    kid_name: str = "Child",
    category: str = "",
    month: str = "",
) -> dict[str, Any]:
    """Submits a child's pre-purchase request to check if it is auto-approved or needs Parent Approval.

    Args:
        description: Item the child wants to buy (e.g., 'Nintendo Switch game', 'School calculator').
        amount: Price of the item in dollars (must be > 0).
        kid_name: Name of the child requesting pre-purchase approval.
        category: Optional category ('necessity', 'entertainment', 'luxury'). Auto-classified if blank.
        month: Optional month in 'YYYY-MM' format. Defaults to current month.

    Returns:
        Dictionary with pre-purchase approval decision, HITL stop flag, and expense ID.
    """
    return log_expense(
        description=description,
        amount=amount,
        category=category,
        month=month,
        requester_name=kid_name,
    )


def resolve_parent_approval(
    expense_id: int,
    decision: str,
    parent_note: str = "",
) -> dict[str, Any]:
    """Resolves a pending Human-in-the-Loop (HITL) parent approval request by approving or rejecting it.

    Args:
        expense_id: Positive integer ID of the expense record awaiting parent approval.
        decision: Parent decision ('approved' to allow purchase, or 'rejected' to decline).
        parent_note: Optional coaching note from the parent explaining the decision.

    Returns:
        Dictionary with the updated expense record and parent decision status.
    """
    try:
        validated = ParentDecisionInput(
            expense_id=expense_id,
            decision=decision.lower().strip(),  # type: ignore[arg-type]
            parent_note=parent_note,
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_PARENT_DECISION",
            message=f"Validation failed for resolve_parent_approval: {exc.errors()[0]['msg']}",
            recovery_instruction=(
                "Pass a positive `expense_id` and set `decision` to either 'approved' or 'rejected'."
            ),
            suggested_next_tool="get_monthly_expenses_report",
        )

    existing = get_expense_by_id(validated.expense_id)
    if not existing:
        return build_error_response(
            error_code="EXPENSE_ID_NOT_FOUND",
            message=f"Expense ID {validated.expense_id} was not found in the database.",
            recovery_instruction=(
                "Call `get_monthly_expenses_report` first to list valid expense IDs awaiting review, "
                "then call `resolve_parent_approval` with the matching `expense_id`."
            ),
            suggested_next_tool="get_monthly_expenses_report",
        )

    updated = update_expense(
        expense_id=validated.expense_id,
        status=validated.decision,
        parent_note=validated.parent_note,
    )
    return {
        "status": "success",
        "message": (
            f"Parent {validated.decision.upper()} purchase #{validated.expense_id} "
            f"('{existing['description']}', ${existing['amount']:.2f})."
        ),
        "hitl_resolved": True,
        "decision": validated.decision,
        "expense": updated,
    }


def update_expense_record(
    expense_id: int,
    description: str = "",
    amount: float | None = None,
    category: str = "",
) -> dict[str, Any]:
    """Updates an existing expense entry and re-evaluates approval policy rules.

    Args:
        expense_id: Positive integer ID of the expense record to modify.
        description: New item description (optional; leave empty to keep existing).
        amount: New positive dollar amount (optional; leave None to keep existing).
        category: New category ('necessity', 'entertainment', 'luxury') (optional).

    Returns:
        Dictionary containing the updated expense record, new approval status, or recovery instructions on error.
    """
    try:
        validated = ExpenseUpdateInput(
            expense_id=expense_id,
            description=description,
            amount=amount,
            category=category,
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_UPDATE_INPUT",
            message=f"Validation failed for update_expense_record: {exc.errors()[0]['msg']}",
            recovery_instruction=(
                "Provide a valid `expense_id >= 1`, a positive `amount` (if changing price), "
                "and a valid `category` ('necessity', 'entertainment', or 'luxury')."
            ),
            suggested_next_tool="get_monthly_expenses_report",
        )

    match = get_expense_by_id(validated.expense_id)
    if not match:
        return build_error_response(
            error_code="EXPENSE_NOT_FOUND",
            message=f"Expense ID {validated.expense_id} not found.",
            recovery_instruction=(
                "Call `get_monthly_expenses_report` first to retrieve valid `expense_id` numbers, "
                "then retry `update_expense_record` with an existing ID."
            ),
            suggested_next_tool="get_monthly_expenses_report",
        )

    desc_opt = validated.description if validated.description else None
    cat_opt = validated.category if validated.category else None

    eval_amount = validated.amount if validated.amount is not None else match["amount"]
    eval_cat = cat_opt if cat_opt is not None else match["category"]

    new_status = evaluate_policy(eval_amount, eval_cat)

    updated = update_expense(
        expense_id=validated.expense_id,
        description=desc_opt,
        amount=validated.amount,
        category=cat_opt,
        status=new_status,
    )
    if not updated:
        return build_error_response(
            error_code="DATABASE_UPDATE_FAILED",
            message=f"Failed to update expense ID {validated.expense_id}.",
            recovery_instruction="Verify database availability and retry with a valid `expense_id`.",
            suggested_next_tool="get_monthly_expenses_report",
        )

    response = {
        "status": "success",
        "message": f"Updated expense #{validated.expense_id}. Status is '{new_status}'.",
        "hitl_execution_stopped": new_status == "needs_review",
        "expense": updated,
    }
    record_intent_vs_outcome(
        tool_name="update_expense_record",
        intended_action={
            "expense_id": validated.expense_id,
            "amount": eval_amount,
            "category": eval_cat,
            "expected_policy_status": new_status,
        },
        actual_outcome=response,
    )
    return response


def get_monthly_expenses_report(month: str = "") -> dict[str, Any]:
    """Retrieves all expenses for a given month with category breakdowns, totals, and review items.

    Args:
        month: Target month string in 'YYYY-MM' format (e.g., '2026-07'). Leave empty for current month.

    Returns:
        Comprehensive monthly report with category totals, auto_approved items, needs_review items, and parent decisions.
    """
    try:
        validated = MonthlyReportInput(month=month)
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_MONTH_FORMAT",
            message=f"Invalid month format '{month}': {exc.errors()[0]['msg']}",
            recovery_instruction="Format `month` as 'YYYY-MM' (for example, '2026-07') or pass an empty string '' for the current month.",
            suggested_next_tool="get_monthly_expenses_report",
        )

    target_month = (
        validated.month
        if validated.month
        else datetime.datetime.now().strftime("%Y-%m")
    )
    expenses = get_expenses_by_month(target_month)

    total_spent = sum(e["amount"] for e in expenses if e["status"] != "rejected")
    category_totals: dict[str, float] = {}
    needs_review: list[dict[str, Any]] = []
    auto_approved: list[dict[str, Any]] = []
    parent_approved: list[dict[str, Any]] = []
    parent_rejected: list[dict[str, Any]] = []

    for e in expenses:
        cat = e["category"]
        if e["status"] != "rejected":
            category_totals[cat] = category_totals.get(cat, 0.0) + e["amount"]
        if e["status"] == "needs_review":
            needs_review.append(e)
        elif e["status"] == "approved":
            parent_approved.append(e)
        elif e["status"] == "rejected":
            parent_rejected.append(e)
        else:
            auto_approved.append(e)

    return {
        "status": "success",
        "month": target_month,
        "total_expenses_count": len(expenses),
        "total_spent": round(total_spent, 2),
        "category_totals": {k: round(v, 2) for k, v in category_totals.items()},
        "needs_review_count": len(needs_review),
        "needs_review_items": needs_review,
        "auto_approved_count": len(auto_approved),
        "auto_approved_items": auto_approved,
        "parent_approved_count": len(parent_approved),
        "parent_approved_items": parent_approved,
        "parent_rejected_count": len(parent_rejected),
        "parent_rejected_items": parent_rejected,
        "all_expenses": expenses,
    }


def analyze_spending_and_savings(
    month: str = "",
    savings_target_pct: float = 20.0,
) -> dict[str, Any]:
    """Analyzes monthly family/kid spending patterns, Needs vs Wants ratios, and highlights areas to save.

    Args:
        month: Target month in 'YYYY-MM' format. Leave empty for current month.
        savings_target_pct: Target percentage reduction on discretionary wants (default 20.0%).

    Returns:
        Structured spending analysis containing Needs vs Wants breakdown, top discretionary items,
        projected monthly & annual savings, and kid-friendly financial coaching recommendations.
    """
    try:
        validated = SpendingAnalysisInput(
            month=month, savings_target_pct=savings_target_pct
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_ANALYSIS_INPUT",
            message=f"Validation failed for analyze_spending_and_savings: {exc.errors()[0]['msg']}",
            recovery_instruction="Ensure `month` is 'YYYY-MM' (or empty) and `savings_target_pct` is between 1.0 and 90.0.",
            suggested_next_tool="analyze_spending_and_savings",
        )

    report = get_monthly_expenses_report(validated.month)
    if report.get("status") == "error":
        return report

    total_spent = float(report["total_spent"])
    cat_totals = report["category_totals"]
    necessity_spend = float(cat_totals.get("necessity", 0.0))
    discretionary_spend = float(cat_totals.get("entertainment", 0.0)) + float(
        cat_totals.get("luxury", 0.0)
    )

    needs_pct = round((necessity_spend / total_spent) * 100, 1) if total_spent > 0 else 0.0
    wants_pct = (
        round((discretionary_spend / total_spent) * 100, 1) if total_spent > 0 else 0.0
    )

    potential_monthly_savings = round(
        discretionary_spend * (validated.savings_target_pct / 100.0), 2
    )
    potential_annual_savings = round(potential_monthly_savings * 12, 2)

    discretionary_items = sorted(
        [
            e
            for e in report["all_expenses"]
            if e["category"] in {"entertainment", "luxury"}
            and e["status"] != "rejected"
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )

    areas_to_save: list[str] = []
    if discretionary_spend > 0:
        areas_to_save.append(
            f"Trim entertainment & luxury spending (${discretionary_spend:.2f}) by {validated.savings_target_pct:.0f}% "
            f"to save ${potential_monthly_savings:.2f}/month (${potential_annual_savings:.2f}/year)."
        )
    if discretionary_items:
        top_item = discretionary_items[0]
        areas_to_save.append(
            f"Largest discretionary item is '{top_item['description']}' (${top_item['amount']:.2f} by {top_item.get('requester_name', 'Child')}) — "
            "apply the 48-hour cooling-off rule before approving similar wants."
        )
    if report["needs_review_count"] > 0:
        pending_sum = sum(i["amount"] for i in report["needs_review_items"])
        areas_to_save.append(
            f"There are {report['needs_review_count']} pending items (${pending_sum:.2f}) awaiting Parent Approval — "
            "declining or deferring 1-2 impulse requests immediately boosts monthly savings."
        )
    if not areas_to_save:
        areas_to_save.append(
            "Great job! 100% of spending so far is within approved necessities. Keep putting allowance savings into your goal jar!"
        )

    return {
        "status": "success",
        "month": report["month"],
        "total_spent": total_spent,
        "necessity_spend": round(necessity_spend, 2),
        "discretionary_spend": round(discretionary_spend, 2),
        "needs_vs_wants_ratio": {
            "needs_pct": needs_pct,
            "wants_pct": wants_pct,
            "recommended_benchmark": "70% Needs / 30% Wants & Savings",
        },
        "potential_monthly_savings": potential_monthly_savings,
        "potential_annual_savings": potential_annual_savings,
        "top_discretionary_expenses": discretionary_items[:5],
        "pending_parent_approvals_count": report["needs_review_count"],
        "areas_to_save": areas_to_save,
    }


def save_category_rule(category_name: str, classification: str) -> dict[str, Any]:
    """Saves a custom family preference for how a category or item keyword should be classified.

    Args:
        category_name: Item keyword or category name to remember (e.g., 'boba', 'soccer cleats', 'textbooks').
        classification: Target bucket ('necessity', 'entertainment', or 'luxury').

    Returns:
        Dictionary confirming the saved category rule or providing recovery instructions if invalid.
    """
    try:
        validated = CategoryRuleInput(
            category_name=category_name,
            classification=classification.lower().strip(),  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        return build_error_response(
            error_code="INVALID_CATEGORY_RULE",
            message=f"Invalid category rule input: {exc.errors()[0]['msg']}",
            recovery_instruction=(
                "Pass a non-empty `category_name` and set `classification` to one of: "
                "'necessity', 'entertainment', or 'luxury'."
            ),
            suggested_next_tool="save_category_rule",
        )

    result = save_category_preference(validated.category_name, validated.classification)
    return {"status": "success", "saved_rule": result}
