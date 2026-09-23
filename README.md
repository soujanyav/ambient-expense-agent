# Ambient Expense Validation & Family Savings Coach Agent (`ambient-expense-agent`)

An intelligent **Family & Kids Expense Tracking, Pre-Purchase Parent Approval (HITL), and Spending Analysis Multi-Agent System** built using the **Google Agent Development Kit (ADK)** (`gemini-3.5-flash` + `gemini-2.5-pro`).

Includes a **Dual-Portal Web UI (`/ui`)**:
- **🎒 Kids Pre-Purchase Portal (`/ui/kid`)**: Children ask permission before buying or log expenses. Necessities under `$100` are auto-approved; items `>= $100` or in `entertainment`/`luxury` pause at a **Parent Human-in-the-Loop (HITL) Approval Gate**.
- **🛡️ Parent Approval & Savings Coach Portal (`/ui/parent`)**: Parents approve or decline pending requests with coaching notes, configure custom family category rules, and view **Spending Analyst (`spending_analyst_agent`)** insights on **Needs vs. Wants** and **Areas to Save**.

---

## 📋 Product Requirements Document (PRD) & Multi-Agent Architecture

### 1. Multi-Agent Hierarchy & Strategic Model Routing
1. **`root_agent` (`ambient_expense_agent` — `gemini-3.5-flash`)**
   - Orchestrates requests across specialized sub-agents and provides direct low-latency tool execution.
2. **`classifier_agent` (`gemini-3.5-flash`)**
   - Classifies items into `necessity`, `entertainment`, or `luxury` using stored family rules (`category_preferences`) and fast keyword heuristics, and learns new rules via `save_category_rule`.
3. **`evaluator_agent` (`gemini-3.5-flash` + Parent HITL Gatekeeper)**
   - Enforces deterministic family approval policies (`log_expense`, `request_purchase_approval`, `update_expense_record`, `resolve_parent_approval`):
     - **`auto_approved`**: Expenses strictly `< $100.00` AND categorized as `necessity`.
     - **`needs_review` (Parent HITL Execution Stop)**: Expenses `>= $100.00` OR categorized as `entertainment` / `luxury` trigger `tool_context.request_confirmation` and hold for parent sign-off.
4. **`spending_analyst_agent` (`gemini-2.5-pro` — High-Reasoning Savings Coach)**
   - Uses `analyze_spending_and_savings` and `get_monthly_expenses_report` to calculate **Needs vs. Wants (%)**, pinpoint top discretionary spending drains, compute monthly/annual savings projections, and generate kid-friendly financial coaching tips.

### 2. Enterprise Engineering & Rubric Highlights
- **Strict Pydantic Validation & Error Recovery (`app/schemas.py`)**: All tool inputs/outputs are validated with `BaseModel` (`Field` constraints) and return explicit `recovery_instruction` + `suggested_next_tool` guidance on errors.
- **Sliding-Window History Compaction & Context Caching (`app/memory.py`)**: `before_model_guardrail_callback` compacts older conversation turns into `session.state["compacted_history_summary"]` and caches static system context + preferences.
- **Non-Blocking Async Memory Operations (`app/database.py`)**: Background `asyncio` / thread-pool write queue (`schedule_background_memory_task`) ensures audit and preference persistence never blocks the UI.
- **PII Redaction & Intent-vs-Outcome Tracing (`app/app_utils/telemetry.py`)**: Deterministic `redact_pii` scrubs credit cards, SSNs, emails, phones, and bank accounts prior to SQLite storage or Cloud Logging, while `record_intent_vs_outcome` logs structured JSON (`AGENT_INTENT_VS_OUTCOME_AUDIT`) and OpenTelemetry span attributes.
- **CI/CD & Hardened Container (`.github/workflows/`, `cloudbuild.yaml`, `Dockerfile`)**: Automated lint/test/deploy pipelines with Workload Identity Federation and a non-root `Dockerfile` with `HEALTHCHECK`.

---

## 🖥️ Launching the Kids & Parent Web UI

Start the server locally:
```bash
uv run uvicorn app.fast_api_app:app --host 127.0.0.1 --port 8000
```
Then open:
- **Unified Portal Switcher**: `http://127.0.0.1:8000/ui`
- **Kids Pre-Purchase Portal**: `http://127.0.0.1:8000/ui/kid`
- **Parent Approval & Savings Coach Portal**: `http://127.0.0.1:8000/ui/parent`

### 🔐 Family Login Accounts (Salted `scrypt` Hashed + RBAC)
| Username | Role | Portal Access & Permissions | Initial Strong Password |
| :--- | :--- | :--- | :--- |
| **`mother`** | `parent` | Full access to **Parent Approval Queue**, Approve/Decline HITL decisions, Family Rules & Kids View | `Mom$SmartSave2026!` |
| **`father`** | `parent` | Full access to **Parent Approval Queue**, Approve/Decline HITL decisions, Family Rules & Kids View | `Dad$SmartSave2026!` |
| **`daughter`** | `child` | **Kids Pre-Purchase Portal** (submit requests, view savings coach & parent feedback; cannot self-approve) | `Girl$SaveGoal2026!` |
| **`son`** | `child` | **Kids Pre-Purchase Portal** (submit requests, view savings coach & parent feedback; cannot self-approve) | `Boy$SaveGoal2026!` |



> 💡 **Tip:** Use [Antigravity CLI](https://antigravity.google/) for AI-assisted development - project context is pre-configured in `GEMINI.md`.

## Requirements

Before you begin, ensure you have:
- **uv**: Python package manager (used for all dependency management in this project) - [Install](https://docs.astral.sh/uv/getting-started/installation/) ([add packages](https://docs.astral.sh/uv/concepts/dependencies/) with `uv add <package>`)
- **agents-cli**: Agents CLI - Install with `uv tool install google-agents-cli`
- **Google Cloud SDK**: For GCP services - [Install](https://cloud.google.com/sdk/docs/install)


## Quick Start

Install `agents-cli` and its skills if not already installed:

```bash
uvx google-agents-cli setup
```

Install required packages:

```bash
agents-cli install
```

Test the agent with a local web server:

```bash
agents-cli playground
```

You can also use features from the [ADK](https://adk.dev/) CLI with `uv run adk`.

## Commands

| Command              | Description                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------- |
| `agents-cli install` | Install dependencies using uv                                                         |
| `agents-cli playground` | Launch local development environment                                                  |
| `agents-cli lint`    | Run code quality checks                                                               |
| `agents-cli eval`    | Evaluate agent behavior (generate, grade, analyze, and more — see `agents-cli eval --help`) |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests                                                        |
| `agents-cli deploy`  | Deploy agent to Cloud Run                                                                   || [A2A Inspector](https://github.com/a2aproject/a2a-inspector) | Launch A2A Protocol Inspector                                                        |

## 🛠️ Project Management

| Command | What It Does |
|---------|--------------|
| `agents-cli scaffold enhance` | Add CI/CD pipelines and Terraform infrastructure |
| `agents-cli infra cicd` | One-command setup of entire CI/CD pipeline + infrastructure |
| `agents-cli scaffold upgrade` | Auto-upgrade to latest version while preserving customizations |

---

## Development

Edit your agent logic in `app/agent.py` and test with `agents-cli playground` - it auto-reloads on save.

## Deployment

```bash
gcloud config set project <your-project-id>
agents-cli deploy
```

To add CI/CD and Terraform, run `agents-cli scaffold enhance`.
To set up your production infrastructure, run `agents-cli infra cicd`.

## Observability

Built-in telemetry exports to Cloud Trace, BigQuery, and Cloud Logging.

## A2A Inspector

This agent supports the [A2A Protocol](https://a2a-protocol.org/). Use the [A2A Inspector](https://github.com/a2aproject/a2a-inspector) to test interoperability.
See the [A2A Inspector docs](https://github.com/a2aproject/a2a-inspector) for details.
