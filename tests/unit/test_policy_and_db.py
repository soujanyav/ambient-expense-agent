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

from fastapi.testclient import TestClient

from app.agent import (
    classifier_agent,
    evaluator_agent,
    root_agent,
    spending_analyst_agent,
)
from app.app_utils.telemetry import AUDIT_LOG_BUFFER, redact_pii
from app.database import add_expense, get_expenses_by_month, init_db, update_expense
from app.fast_api_app import app as fastapi_app
from app.guardrails import (
    after_tool_guardrail_callback,
    before_tool_guardrail_callback,
)
from app.memory import build_cached_system_context, compact_conversation_history
from app.tools import (
    analyze_spending_and_savings,
    evaluate_policy,
    get_monthly_expenses_report,
    log_expense,
    resolve_parent_approval,
    save_category_rule,
    update_expense_record,
)


def test_evaluate_policy_rules():
    # Default threshold (< $100)
    assert evaluate_policy(45.0, "necessity") == "auto_approved"
    assert evaluate_policy(99.99, "necessity") == "auto_approved"
    assert evaluate_policy(100.0, "necessity") == "needs_review"
    assert evaluate_policy(150.0, "necessity") == "needs_review"
    assert evaluate_policy(50.0, "entertainment") == "needs_review"
    assert evaluate_policy(80.0, "luxury") == "needs_review"

    # Son's threshold (< $10)
    assert evaluate_policy(8.50, "necessity", requester_name="Son") == "auto_approved"
    assert evaluate_policy(9.99, "necessity", requester_name="Son") == "auto_approved"
    assert evaluate_policy(10.00, "necessity", requester_name="Son") == "needs_review"
    assert evaluate_policy(15.00, "necessity", requester_name="Son") == "needs_review"
    assert evaluate_policy(5.00, "entertainment", requester_name="Son") == "needs_review"

    # Daughter's threshold (< $30)
    assert evaluate_policy(25.00, "necessity", requester_name="Daughter") == "auto_approved"
    assert evaluate_policy(29.99, "necessity", requester_name="Daughter") == "auto_approved"
    assert evaluate_policy(30.00, "necessity", requester_name="Daughter") == "needs_review"
    assert evaluate_policy(45.00, "necessity", requester_name="Daughter") == "needs_review"
    assert evaluate_policy(12.00, "entertainment", requester_name="Daughter") == "needs_review"


def test_database_crud_and_pii_redaction(tmp_path, monkeypatch):
    test_db = tmp_path / "test_expenses.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)

    init_db()
    res = add_expense(
        "Test milk paid with card 4532-1111-2222-3333 by kid@example.com",
        5.0,
        "necessity",
        "auto_approved",
        "2026-07",
    )
    assert res["id"] == 1
    assert res["status"] == "auto_approved"
    assert "4532-1111-2222-3333" not in res["description"]
    assert "[REDACTED_CARD]" in res["description"]
    assert "[REDACTED_EMAIL]" in res["description"]

    expenses = get_expenses_by_month("2026-07")
    assert len(expenses) == 1
    assert "[REDACTED_CARD]" in expenses[0]["description"]

    updated = update_expense(1, amount=6.0, status="auto_approved")
    assert updated["amount"] == 6.0


def test_tools_monthly_report_and_spending_analyst(tmp_path, monkeypatch):
    test_db = tmp_path / "test_report.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)

    log_expense("Groceries", 45.0, "necessity", month="2026-07", requester_name="Alex")
    log_expense("Concert", 75.0, "entertainment", month="2026-07", requester_name="Alex")

    report = get_monthly_expenses_report("2026-07")
    assert report["total_spent"] == 120.0
    assert report["category_totals"]["necessity"] == 45.0
    assert report["category_totals"]["entertainment"] == 75.0
    assert report["auto_approved_count"] == 1
    assert report["needs_review_count"] == 1

    analysis = analyze_spending_and_savings("2026-07", savings_target_pct=20.0)
    assert analysis["status"] == "success"
    assert analysis["necessity_spend"] == 45.0
    assert analysis["discretionary_spend"] == 75.0
    assert analysis["potential_monthly_savings"] == 15.0
    assert len(analysis["areas_to_save"]) >= 1


def test_error_recovery_instructions(tmp_path, monkeypatch):
    test_db = tmp_path / "test_errors.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)
    init_db()

    err_not_found = update_expense_record(expense_id=999, amount=30.0)
    assert err_not_found["status"] == "error"
    assert "recovery_instruction" in err_not_found
    assert err_not_found["suggested_next_tool"] == "get_monthly_expenses_report"

    err_invalid_rule = save_category_rule("boba", "invalid_category")
    assert err_invalid_rule["status"] == "error"
    assert "recovery_instruction" in err_invalid_rule


def test_multi_agent_hierarchy_and_parent_hitl_workflow(tmp_path, monkeypatch):
    test_db = tmp_path / "test_hitl.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)
    init_db()

    sub_agent_names = [a.name for a in root_agent.sub_agents]
    assert "classifier_agent" in sub_agent_names
    assert "evaluator_agent" in sub_agent_names
    assert "spending_analyst_agent" in sub_agent_names
    assert spending_analyst_agent.model.model == "gemini-2.5-pro"
    assert classifier_agent.model.model == "gemini-3.5-flash"
    assert evaluator_agent.model.model == "gemini-3.5-flash"

    # Kid requests an $85 video game -> paused for parent HITL approval
    req = log_expense("Zelda Switch Game", 85.0, "entertainment", requester_name="Leo")
    assert req["hitl_execution_stopped"] is True
    assert req["parent_approval_required"] is True
    assert req["expense"]["status"] == "needs_review"

    # Parent approves via resolve_parent_approval
    decision = resolve_parent_approval(
        expense_id=req["expense"]["id"],
        decision="approved",
        parent_note="Great grades this week!",
    )
    assert decision["status"] == "success"
    assert decision["hitl_resolved"] is True
    assert decision["expense"]["status"] == "approved"
    assert decision["expense"]["parent_note"] == "Great grades this week!"


def test_history_compaction_and_context_cache(tmp_path, monkeypatch):
    test_db = tmp_path / "test_memory.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)
    init_db()

    messages = [{"role": "user", "content": f"Turn {i} spent $10"} for i in range(10)]
    session_state = {}
    compacted, digest = compact_conversation_history(
        messages, session_state=session_state, max_turns=4
    )
    assert len(compacted) == 4
    assert "Turn 0" in digest
    assert session_state["compacted_turns_count"] == 6

    cache_info = build_cached_system_context("static_policy_rules")
    assert "cache_key" in cache_info


def test_guardrails_and_intent_vs_outcome_audit(tmp_path, monkeypatch):
    test_db = tmp_path / "test_audit.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)
    init_db()
    AUDIT_LOG_BUFFER.clear()

    class DummyContext:
        def __init__(self):
            self.state = {}

    ctx = DummyContext()
    blocked = before_tool_guardrail_callback(log_expense, {"amount": -20.0}, ctx)
    assert blocked is not None
    assert blocked["error_code"] == "GUARDRAIL_INVALID_AMOUNT"
    assert "recovery_instruction" in blocked

    res = log_expense("School notebook", 12.0, "necessity")
    after_tool_guardrail_callback(
        log_expense, {"description": "School notebook", "amount": 12.0}, ctx, res
    )
    assert len(AUDIT_LOG_BUFFER) >= 1
    assert AUDIT_LOG_BUFFER[-1]["intent_outcome_match"] is True
    assert redact_pii("SSN 123-45-6789") == "SSN [REDACTED_SSN]"


def test_family_portal_web_ui_and_endpoints(tmp_path, monkeypatch):
    test_db = tmp_path / "test_web_ui.db"
    monkeypatch.setattr("app.database.DB_PATH", test_db)
    init_db()

    client = TestClient(fastapi_app)
    ui_res = client.get("/ui")
    assert ui_res.status_code == 200
    assert "Family Portal Sign In" in ui_res.text

    # 1. Daughter logs in
    daughter_login = client.post(
        "/api/auth/login",
        json={"username": "daughter", "password": "Girl$SaveGoal2026!"},
    )
    assert daughter_login.status_code == 200
    daughter_token = daughter_login.json()["token"]
    assert daughter_login.json()["user"]["role"] == "child"

    # 2. Daughter submits a pre-purchase request for Roblox Coins ($35 entertainment)
    kid_req = client.post(
        "/api/family/kid-request",
        headers={"Authorization": f"Bearer {daughter_token}"},
        json={
            "requester_name": "Child",
            "description": "Roblox Coins",
            "amount": 35.0,
            "category": "entertainment",
        },
    )
    assert kid_req.status_code == 200
    assert kid_req.json()["parent_approval_required"] is True
    expense_id = kid_req.json()["expense"]["id"]
    assert kid_req.json()["expense"]["requester_name"] == "Daughter"

    # 3. Daughter attempts to approve her own purchase -> 403 Forbidden (RBAC)
    forbidden_res = client.post(
        "/api/family/parent-decision",
        headers={"Authorization": f"Bearer {daughter_token}"},
        json={"expense_id": expense_id, "decision": "approved", "parent_note": "Self approve"},
    )
    assert forbidden_res.status_code == 403

    # 4. Mother logs in and approves Daughter's request
    mother_login = client.post(
        "/api/auth/login",
        json={"username": "mother", "password": "Mom$SmartSave2026!"},
    )
    assert mother_login.status_code == 200
    mother_token = mother_login.json()["token"]
    assert mother_login.json()["user"]["role"] == "parent"

    parent_approve = client.post(
        "/api/family/parent-decision",
        headers={"Authorization": f"Bearer {mother_token}"},
        json={
            "expense_id": expense_id,
            "decision": "approved",
            "parent_note": "Great job finishing your homework!",
        },
    )
    assert parent_approve.status_code == 200
    assert parent_approve.json()["expense"]["status"] == "approved"
    assert "[Mother]" in parent_approve.json()["expense"]["parent_note"]

    dash = client.get("/api/family/dashboard").json()
    assert dash["report"]["parent_approved_count"] == 1
    assert dash["analysis"]["discretionary_spend"] == 35.0

