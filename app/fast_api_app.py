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

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import google.auth
from a2a.server.tasks import InMemoryTaskStore
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.cloud import logging as google_cloud_logging

from app.app_utils import services
from app.app_utils.a2a import attach_a2a_routes
from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.schemas import CategoryRuleInput, ExpenseLogInput, ParentDecisionInput
from app.tools import (
    analyze_spending_and_savings,
    get_monthly_expenses_report,
    log_expense,
    resolve_parent_approval,
    save_category_rule,
)

load_dotenv()
setup_telemetry()
try:
    _, project_id = google.auth.default()
    logging_client = google_cloud_logging.Client()
    logger: Any = logging_client.logger(__name__)
except Exception:
    import logging

    logger = logging.getLogger(__name__)

allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_INDEX_PATH = Path(__file__).parent / "static" / "index.html"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app
    from app.agent import root_agent

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    await attach_a2a_routes(
        app,
        agent=root_agent,
        runner=runner,
        task_store=InMemoryTaskStore(),
        rpc_path=f"/a2a/{adk_app.name}",
    )
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=False,
    lifespan=lifespan,
)
app.title = "ambient-expense-agent"
app.description = "API & Dual-Portal Web UI for Family Expense Tracking & Pre-Purchase Parent Approvals"


def _serve_portal_html() -> HTMLResponse:
    """Serve the Kids & Parent Portal HTML with security headers."""
    html_content = STATIC_INDEX_PATH.read_text(encoding="utf-8")
    return HTMLResponse(
        content=html_content,
        headers={
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": (
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; connect-src 'self'"
            ),
        },
    )


@app.get("/ui", response_class=HTMLResponse)
@app.get("/ui/kid", response_class=HTMLResponse)
@app.get("/ui/parent", response_class=HTMLResponse)
def render_family_portal() -> HTMLResponse:
    """Render the interactive Kids & Parent Expense Approval Web UI."""
    return _serve_portal_html()


@app.get("/api/family/dashboard")
def get_family_dashboard(
    month: str = Query(default="", pattern=r"^(\d{4}-\d{2})?$"),
    savings_target_pct: float = Query(default=20.0, ge=1.0, le=90.0),
) -> dict[str, Any]:
    """Return monthly expense report, pending parent HITL queue, and spending analyst coaching insights."""
    report = get_monthly_expenses_report(month=month)
    analysis = analyze_spending_and_savings(
        month=month, savings_target_pct=savings_target_pct
    )
    return {"status": "success", "report": report, "analysis": analysis}


@app.post("/api/family/kid-request")
def create_kid_purchase_request(payload: ExpenseLogInput) -> dict[str, Any]:
    """Endpoint for children to submit a pre-purchase approval request or expense."""
    return log_expense(
        description=payload.description,
        amount=payload.amount,
        category=payload.category,
        month=payload.month,
        requester_name=payload.requester_name,
    )


@app.post("/api/family/parent-decision")
def submit_parent_approval_decision(payload: ParentDecisionInput) -> dict[str, Any]:
    """Endpoint for parents to approve or decline a pending kid purchase request in the HITL queue."""
    return resolve_parent_approval(
        expense_id=payload.expense_id,
        decision=payload.decision,
        parent_note=payload.parent_note,
    )


@app.post("/api/family/category-rule")
def save_family_category_rule(payload: CategoryRuleInput) -> dict[str, Any]:
    """Endpoint for parents to teach custom category rules to the classifier."""
    return save_category_rule(
        category_name=payload.category_name,
        classification=payload.classification,
    )


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    if hasattr(logger, "log_struct"):
        logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
