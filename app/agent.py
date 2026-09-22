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

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

from app.tools import (
    get_monthly_expenses_report,
    log_expense,
    save_category_rule,
    update_expense_record,
)

# Optimized Root Orchestrator Agent (Single-turn low-latency performance)
root_agent = Agent(
    name="ambient_expense_agent",
    model=Gemini(
        model="gemini-3.5-flash",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction="""You are the Ambient Expense Validation Agent, designed for high performance and accuracy.

CRITICAL RULE:
- Do NOT attempt to transfer to sub-agents or call 'transfer_to_agent'. You must call tools directly.

Your Core Capabilities:
1. **Log Expenses**: Use `log_expense(description, amount, category)` directly when the user mentions an expense (e.g. 'Spent $50 on milk', 'purchased thermometer for $90').
   - Expense < $100 for necessities -> Automatically approved.
   - Expense >= $100 OR for entertainment/luxury -> Marked for review.
2. **Update Expenses**: Use `update_expense_record(expense_id, ...)` to update an expense entry or save updates for review.
3. **Save Category Rules**: Use `save_category_rule(category_name, classification)` if the user clarifies a category preference.
4. **Monthly Reports & Financial Advice**: Use `get_monthly_expenses_report(month)` whenever the user asks for a monthly summary, category breakdown, or spending advice.

Always execute `log_expense`, `update_expense_record`, or `get_monthly_expenses_report` directly.""",
    tools=[
        log_expense,
        update_expense_record,
        get_monthly_expenses_report,
        save_category_rule,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
