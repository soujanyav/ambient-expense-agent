# Ambient Expense Validation Agent (`ambient-expense-agent`)

An intelligent expense validation, tracking, and financial advisory agent built using the **Google Agent Development Kit (ADK)** and powered by **Gemini 3.5 Flash**.

The agent processes user expenses in natural language, classifies them into categories (`necessity` vs. `entertainment`/`luxury`), applies automated approval policies, persists records in a local SQLite database, and generates monthly spending breakdowns with actionable financial advice.

---

## 📋 Product Requirements Document (PRD) & Scope

### 1. Product Vision & Overview
Managing day-to-day personal or corporate expenses often involves tedious manual categorization and delayed policy compliance checks. The **Ambient Expense Validation Agent** acts as an always-on financial assistant that logs expenses conversationally in a single low-latency turn, enforces deterministic approval policies, learns user-specific category preferences, and delivers monthly budget optimization insights.

### 2. Core Functional Scope
1. **Conversational Expense Logging (`log_expense`)**
   - Extracts expense `description`, `amount`, `category`, and `month` (`YYYY-MM`) from natural language inputs.
   - Automatically classifies expenses when a category is not explicitly provided using:
     1. Stored user category preferences in SQLite (`category_preferences` table).
     2. Fast keyword heuristics (e.g., groceries, milk, rent, utilities, medicine $\rightarrow$ `necessity`; concert, movie, restaurant, spa, vacation $\rightarrow$ `entertainment`).
2. **Deterministic Policy Evaluation (`evaluate_policy`)**
   - **`auto_approved`**: Expenses strictly under **$100.00** (`< $100`) AND categorized as **`necessity`**.
   - **`needs_review`**: Any expense **$\ge$ $100.00** OR categorized as **`entertainment`** or **`luxury`**.
3. **Expense Record Management (`update_expense_record`)**
   - Updates existing expense entries by ID (`description`, `amount`, or `category`).
   - Automatically re-evaluates the approval status (`auto_approved` vs. `needs_review`) whenever an amount or category is modified.
4. **Custom Category Rule Learning (`save_category_rule`)**
   - Persists user clarifications or custom preferences (e.g., classifying a specific merchant or item type as `necessity` or `entertainment`) for future automated classification.
5. **Monthly Reporting & Financial Advisory (`get_monthly_expenses_report`)**
   - Aggregates monthly expenses (`YYYY-MM`) with total spend, item counts, and category-wise totals.
   - Separates `auto_approved` items from `needs_review` items requiring human attention.
   - Synthesizes actionable financial advice on reducing discretionary spend and optimizing budget allocation.

### 3. Example Use Cases
| Scenario | Example User Prompt | Classification & Policy Outcome |
| :--- | :--- | :--- |
| **Necessity < $100** | *"I spent $45 on groceries today."* | Classified as `necessity` $\rightarrow$ **`auto_approved`** |
| **Luxury / Entertainment** | *"Spent $80 on concert tickets."* | Classified as `entertainment` $\rightarrow$ **`needs_review`** |
| **High-Value Necessity ($\ge$ $100)** | *"Paid $150 for a desk chair."* | Classified as `necessity`, amount $\ge$ $100 $\rightarrow$ **`needs_review`** |
| **Expense Modification** | *"Update expense #3 amount to $60."* | Updated in DB, policy re-evaluated $\rightarrow$ status updated |
| **Category Rule Preference** | *"Always classify gym memberships as a necessity."* | Rule saved in `category_preferences` table |
| **Monthly Summary & Advice** | *"Show me my expenses for this month and how I can save."* | Returns category breakdown, `needs_review` list, and savings advice |

### 4. Constraints, Guardrails & Success Criteria
- **Strict Policy Adherence**: 100% deterministic compliance on threshold rules (`< $100` necessity $\rightarrow$ `auto_approved`; `>= $100` or `entertainment`/`luxury` $\rightarrow$ `needs_review`).
- **Low-Latency Single-Turn Execution**: Root orchestrator (`ambient_expense_agent`) executes tools directly without unnecessary sub-agent handoff overhead.
- **Persistent State**: All expenses and learned category rules are persisted in SQLite (`data/expenses.db` across `expenses` and `category_preferences` tables).

---

## 🏗️ Architecture & Project Structure

```
ambient-expense-agent/
├── app/                       # Core agent application
│   ├── agent.py               # Root ADK Agent definition (Gemini 3.5 Flash)
│   ├── tools.py               # Expense tools, policy engine & fast classifier
│   ├── database.py            # SQLite persistence layer (expenses & preferences)
│   ├── fast_api_app.py        # FastAPI / A2A backend server
│   └── app_utils/             # Telemetry, typing, and service helpers
├── tests/                     # Unit, integration, and evaluation suites
│   ├── unit/                  # Policy & database unit tests
│   ├── integration/           # End-to-end agent & server tests
│   └── eval/                  # Evaluation datasets (25 & 50 cases) & configs
├── deployment/                # Terraform infrastructure templates
├── scripts/                   # Dataset generation utilities
├── GEMINI.md                  # AI-assisted development guide
└── pyproject.toml             # Project dependencies & configuration
```

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
