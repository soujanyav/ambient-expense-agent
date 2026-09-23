# ruff: noqa
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

"""Multi-Agent Family & Kids Ambient Expense Validation, Pre-Purchase HITL, and Spending Analysis System."""

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.guardrails import (
    after_tool_guardrail_callback,
    before_model_guardrail_callback,
    before_tool_guardrail_callback,
)
from app.memory import get_context_cache_config
from app.tools import (
    analyze_spending_and_savings,
    classify_category_fast,
    get_monthly_expenses_report,
    log_expense,
    request_purchase_approval,
    resolve_parent_approval,
    save_category_rule,
    update_expense_record,
)

# 1. Fast Low-Latency Model for Classification & Policy Gate
fast_flash_model = Gemini(
    model="gemini-3.5-flash",
    retry_options=types.HttpRetryOptions(attempts=3),
)

# 2. High-Reasoning Pro Model for Deep Financial & Savings Coaching
reasoning_pro_model = Gemini(
    model="gemini-2.5-pro",
    retry_options=types.HttpRetryOptions(attempts=3),
)

# Sub-Agent 1: Category Classifier & Family Rule Learner
classifier_agent = Agent(
    name="classifier_agent",
    model=fast_flash_model,
    description="Classifies kids and family expenses into 'necessity', 'entertainment', or 'luxury' and saves family category rules.",
    instruction="""You are the Family Expense Classifier Agent.
Your responsibility is to classify items into 'necessity', 'entertainment', or 'luxury' and persist custom family preferences using `save_category_rule(category_name, classification)`.
If a user clarifies how an item should be categorized, immediately call `save_category_rule`.""",
    tools=[save_category_rule, classify_category_fast],
    before_model_callback=before_model_guardrail_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

# Sub-Agent 2: Policy Evaluator & Parent HITL Gatekeeper
evaluator_agent = Agent(
    name="evaluator_agent",
    model=fast_flash_model,
    description="Evaluates kids pre-purchase requests and expenses against family approval thresholds and manages Parent HITL stops.",
    instruction="""You are the Pre-Purchase Policy Evaluator & Parent Approval Gatekeeper Agent.
Rules:
1. Necessities strictly under $100 (< $100.00) are automatically approved (`auto_approved`).
2. Purchases >= $100.00 OR in 'entertainment' / 'luxury' trigger a Human-in-the-Loop (HITL) Parent Approval stop (`needs_review`).
3. Use `request_purchase_approval` or `log_expense` for new items, `update_expense_record` for edits, and `resolve_parent_approval` when a parent approves or rejects a pending request.""",
    tools=[
        log_expense,
        request_purchase_approval,
        update_expense_record,
        resolve_parent_approval,
    ],
    before_model_callback=before_model_guardrail_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

# Sub-Agent 3: Dedicated Spending Analysis & Kids Savings Coach (Strategic Model Routing -> Pro)
spending_analyst_agent = Agent(
    name="spending_analyst_agent",
    model=reasoning_pro_model,
    description="Dedicated financial coach that prepares monthly spending analysis, Needs vs Wants ratios, and highlights actionable areas for kids and families to save money.",
    instruction="""You are the Family Spending Analyst & Kids Savings Coach Agent, powered by a high-reasoning model.
Your mission:
1. Call `analyze_spending_and_savings(month, savings_target_pct)` and `get_monthly_expenses_report(month)` to inspect family and kids spending.
2. Break down Needs ('necessity') vs. Wants ('entertainment' & 'luxury').
3. Highlight specific areas where kids can save money (e.g., video games, treats, dining out, impulse items awaiting parent approval).
4. Provide encouraging, kid-friendly coaching tips and concrete monthly/annual dollar savings projections.""",
    tools=[analyze_spending_and_savings, get_monthly_expenses_report],
    before_model_callback=before_model_guardrail_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

# Root Orchestrator Agent combining direct low-latency tool execution + specialized sub-agent hierarchy
root_agent = Agent(
    name="ambient_expense_agent",
    model=fast_flash_model,
    description="Root orchestrator for Family & Kids Expense Tracking, Pre-Purchase Parent Approval (HITL), and Spending Analysis.",
    instruction="""You are the Ambient Expense Validation & Family Financial Coach Agent.

Your Core Capabilities & Routing Strategy:
1. **Log Expenses & Kids Pre-Purchase Requests**:
   - Call `log_expense(description, amount, category)` or `request_purchase_approval(description, amount, kid_name, category)` when a child or parent mentions an expense or asks for permission before buying.
   - Expense < $100 for necessities -> Automatically approved (`auto_approved`).
   - Expense >= $100 OR for entertainment/luxury -> Paused at the Parent Approval HITL Gate (`needs_review`).
2. **Parent Approval Resolution (HITL)**:
   - Use `resolve_parent_approval(expense_id, decision, parent_note)` when a parent approves ('approved') or declines ('rejected') a pending purchase request.
3. **Update Expenses**:
   - Use `update_expense_record(expense_id, ...)` to modify an existing expense entry and re-evaluate policy status.
4. **Save Category Rules**:
   - Use `save_category_rule(category_name, classification)` when a user specifies a category preference.
5. **Monthly Reports, Spending Analysis & Areas to Save**:
   - Use `get_monthly_expenses_report(month)` and `analyze_spending_and_savings(month)` (or delegate to `spending_analyst_agent`) whenever the user asks for a monthly summary, category breakdown, spending analysis, or advice on areas to save.

If a tool returns an error with a `recovery_instruction`, follow the `recovery_instruction` immediately to self-correct.""",
    tools=[
        log_expense,
        request_purchase_approval,
        resolve_parent_approval,
        update_expense_record,
        get_monthly_expenses_report,
        analyze_spending_and_savings,
        save_category_rule,
    ],
    sub_agents=[
        classifier_agent,
        evaluator_agent,
        spending_analyst_agent,
    ],
    before_model_callback=before_model_guardrail_callback,
    before_tool_callback=before_tool_guardrail_callback,
    after_tool_callback=after_tool_guardrail_callback,
)

_cache_cfg = get_context_cache_config()
try:
    app = App(
        root_agent=root_agent,
        name="app",
        context_cache_config=_cache_cfg if not isinstance(_cache_cfg, dict) else None,
    )
except TypeError:
    app = App(
        root_agent=root_agent,
        name="app",
    )
