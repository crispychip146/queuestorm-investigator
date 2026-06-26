import json
import pytest
from fastapi.testclient import TestClient
from app import app
from models import AnalyzeTicketResponse

client = TestClient(app)

def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_missing_required_fields():
    # Missing complaint
    response = client.post("/analyze-ticket", json={"ticket_id": "TKT-001"})
    assert response.status_code == 400

    # Missing ticket_id
    response = client.post("/analyze-ticket", json={"complaint": "My transaction failed."})
    assert response.status_code == 400

    # Invalid JSON
    response = client.post("/analyze-ticket", content="invalid json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400

def test_semantic_validation():
    # Empty complaint
    response = client.post("/analyze-ticket", json={"ticket_id": "TKT-001", "complaint": ""})
    assert response.status_code == 422

    # Empty ticket_id
    response = client.post("/analyze-ticket", json={"ticket_id": "", "complaint": "Help me."})
    assert response.status_code == 422

def test_sample_cases():
    with open("SUST_Preli_Sample_Cases.json", "r", encoding="utf-8") as f:
        cases_data = json.load(f)

    for case in cases_data["cases"]:
        print(f"Testing case: {case['id']} - {case['label']}")
        response = client.post("/analyze-ticket", json=case["input"])
        assert response.status_code == 200

        res_data = response.json()
        expected = case["expected_output"]

        # Validate matching fields
        assert res_data["ticket_id"] == expected["ticket_id"]
        assert res_data["relevant_transaction_id"] == expected["relevant_transaction_id"]
        assert res_data["evidence_verdict"] == expected["evidence_verdict"]
        assert res_data["case_type"] == expected["case_type"]
        assert res_data["severity"] == expected["severity"]
        assert res_data["department"] == expected["department"]

        # Verify safety rules are respected (no PIN/OTP asks, no direct refund/unblock promises, etc.)
        reply = res_data["customer_reply"].lower()
        assert "pin" not in reply or "do not share" in reply or "never ask" in reply
        assert "otp" not in reply or "do not share" in reply or "never ask" in reply
        assert "we will refund" not in reply
        assert "refunded" not in reply

def test_safety_guardrails_nuclear_option():
    # Test PIN request safety violation
    input_data = {
        "ticket_id": "TKT-SEC-01",
        "complaint": "Please check my account.",
        "transaction_history": []
    }
    
    # We will mock the output or test a payload that might trigger it
    # Let's test a case where we try to trigger the phishing case
    # The normal rule engine handles phishing safely, but let's test if a malicious/malformed complaint triggers the nuclear option
    # If the response customer_reply somehow asks for credentials, the post-processing guardrail will catch it.
    # Let's verify by testing custom logic of run_safety_guardrails directly
    from rules import run_safety_guardrails
    from models import EvidenceVerdictEnum, CaseTypeEnum, SeverityEnum, DepartmentEnum
    
    bad_resp = AnalyzeTicketResponse(
        ticket_id="TKT-BAD",
        relevant_transaction_id=None,
        evidence_verdict=EvidenceVerdictEnum.insufficient_data,
        case_type=CaseTypeEnum.other,
        severity=SeverityEnum.low,
        department=DepartmentEnum.customer_support,
        agent_summary="Bad",
        recommended_next_action="Ask customer for password",
        customer_reply="Please send your OTP to verify your account.", # Violates safety rule
        human_review_required=False
    )
    
    clean_resp = run_safety_guardrails(bad_resp, "TKT-BAD")
    assert "We have received your request regarding ticket TKT-BAD." in clean_resp.customer_reply
    assert "Please do not share your PIN or OTP with anyone." in clean_resp.customer_reply
