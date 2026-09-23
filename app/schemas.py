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

"""Strict Pydantic validation schemas for tool inputs, outputs, and error recovery."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ValidCategory = Literal["necessity", "entertainment", "luxury"]
ValidDecision = Literal["approved", "rejected"]


class ExpenseLogInput(BaseModel):
    """Input schema for logging a new expense or pre-purchase approval request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    description: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Clear description of the item or service being purchased (e.g., 'Milk', 'Video Game').",
    )
    amount: float = Field(
        ...,
        gt=0.0,
        le=100000.0,
        description="Positive dollar amount of the expense (must be greater than 0).",
    )
    category: str = Field(
        default="",
        max_length=64,
        description="Optional category ('necessity', 'entertainment', or 'luxury'). Auto-classified if blank.",
    )
    month: str = Field(
        default="",
        pattern=r"^(\d{4}-\d{2})?$",
        description="Target month in 'YYYY-MM' format. Defaults to current month when empty.",
    )
    requester_name: str = Field(
        default="Child",
        min_length=1,
        max_length=100,
        description="Name of the child or family member raising the expense or pre-purchase request.",
    )

    @field_validator("category")
    @classmethod
    def validate_optional_category(cls, v: str) -> str:
        cleaned = v.lower().strip()
        if cleaned and cleaned not in {"necessity", "entertainment", "luxury"}:
            raise ValueError(
                f"Invalid category '{v}'. Must be one of: 'necessity', 'entertainment', 'luxury', or empty."
            )
        return cleaned


class ExpenseUpdateInput(BaseModel):
    """Input schema for updating an existing expense record."""

    model_config = ConfigDict(str_strip_whitespace=True)

    expense_id: int = Field(
        ...,
        ge=1,
        description="Unique positive integer ID of the existing expense record in SQLite.",
    )
    description: str = Field(
        default="",
        max_length=500,
        description="Updated description for the expense. Leave empty to keep existing description.",
    )
    amount: float | None = Field(
        default=None,
        gt=0.0,
        le=100000.0,
        description="Updated positive dollar amount. Leave None to keep existing amount.",
    )
    category: str = Field(
        default="",
        max_length=64,
        description="Updated category ('necessity', 'entertainment', 'luxury'). Leave empty to keep existing category.",
    )

    @field_validator("category")
    @classmethod
    def validate_optional_category(cls, v: str) -> str:
        cleaned = v.lower().strip()
        if cleaned and cleaned not in {"necessity", "entertainment", "luxury"}:
            raise ValueError(
                f"Invalid category '{v}'. Allowed values: 'necessity', 'entertainment', 'luxury'."
            )
        return cleaned


class CategoryRuleInput(BaseModel):
    """Input schema for saving a custom category classification rule."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category_name: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Merchant, item keyword, or category name to remember (e.g., 'gym', 'boba', 'textbooks').",
    )
    classification: ValidCategory = Field(
        ...,
        description="Target classification bucket: 'necessity', 'entertainment', or 'luxury'.",
    )


class MonthlyReportInput(BaseModel):
    """Input schema for retrieving monthly expenses report."""

    model_config = ConfigDict(str_strip_whitespace=True)

    month: str = Field(
        default="",
        pattern=r"^(\d{4}-\d{2})?$",
        description="Target month in 'YYYY-MM' format (e.g., '2026-07'). Leave empty for current month.",
    )


class SpendingAnalysisInput(BaseModel):
    """Input schema for deep spending analysis and savings coaching."""

    model_config = ConfigDict(str_strip_whitespace=True)

    month: str = Field(
        default="",
        pattern=r"^(\d{4}-\d{2})?$",
        description="Target month in 'YYYY-MM' format. Leave empty for current month.",
    )
    savings_target_pct: float = Field(
        default=20.0,
        ge=1.0,
        le=90.0,
        description="Target percentage reduction on discretionary (entertainment/luxury) spending (1-90%).",
    )


class ParentDecisionInput(BaseModel):
    """Input schema for parent Human-in-the-Loop (HITL) approval or rejection of a kid's purchase request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    expense_id: int = Field(
        ...,
        ge=1,
        description="ID of the pending expense/purchase request in the HITL review queue.",
    )
    decision: ValidDecision = Field(
        ...,
        description="Parent decision: 'approved' to approve the purchase, or 'rejected' to decline it.",
    )
    parent_note: str = Field(
        default="",
        max_length=500,
        description="Optional parent feedback or coaching note explaining why the purchase was approved or declined.",
    )


class ToolErrorResponse(BaseModel):
    """Structured error response with explicit recovery instructions for LLM self-correction."""

    status: Literal["error"] = "error"
    error_code: str = Field(..., description="Machine-readable error code.")
    message: str = Field(..., description="Human-readable error description.")
    recovery_instruction: str = Field(
        ...,
        description="Explicit step-by-step recovery instruction telling the LLM how to recover from this error.",
    )
    suggested_next_tool: str | None = Field(
        default=None,
        description="Name of the recommended tool to call next to recover from this error.",
    )


def build_error_response(
    error_code: str,
    message: str,
    recovery_instruction: str,
    suggested_next_tool: str | None = None,
) -> dict[str, Any]:
    """Create a standardized error response dictionary with LLM recovery instructions."""
    return ToolErrorResponse(
        error_code=error_code,
        message=message,
        recovery_instruction=recovery_instruction,
        suggested_next_tool=suggested_next_tool,
    ).model_dump()
