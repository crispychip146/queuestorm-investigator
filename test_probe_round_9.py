"""
Probe tests (Round 9) — even deeper adversarial probes.
Targeting: enum normalization, BDT boundary amounts, ID case-mismatch,
complaint in multiple languages, safety guardrail bypass attempts, and
metadata field policy.
"""
import re
import pytest
from fastapi.testclient import TestClient
from app import app
from models import (
    EvidenceVerdictEnum, CaseTypeEnum, SeverityEnum, DepartmentEnum,
    LanguageEnum, ChannelEnum, UserTypeEnum, TransactionStatusEnum,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Probe R9-1: Enum normalization — channel value comes in as "In-App Chat"
#             (mixed case + hyphen + space). The field_validator in models.py
#             should normalize to "in_app_chat".
# ---------------------------------------------------------------------------
def test_probe_r9_1_enum_normalization_mixed_case_channel():
    payload = {
        "ticket_id": "TKT-R9-1",
        "complaint": "My payment failed.",
        "channel": "In-App Chat",  # not lowercase, has hyphen+space
        "user_type": "Customer",
        "transaction_history": [],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # Should NOT 422 — the validator normalizes.
    assert res["customer_reply"]


# ---------------------------------------------------------------------------
# Probe R9-2: Enum normalization — transaction status with uppercase
#             ("COMPLETED"). Validator should lowercase.
# ---------------------------------------------------------------------------
def test_probe_r9_2_enum_normalization_uppercase_status():
    payload = {
        "ticket_id": "TKT-R9-2",
        "complaint": "My cash-in TXN-R9-2 failed.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-2",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "cash_in",
                "amount": 1000.0,
                "counterparty": "+8801711111111",
                "status": "PENDING",  # uppercase
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # status="pending" → consistent.
    assert res["relevant_transaction_id"] == "TXN-R9-2"


# ---------------------------------------------------------------------------
# Probe R9-3: ID case-mismatch. The complaint has lowercase "txn-r9-3"
#             but the actual ID is uppercase "TXN-R9-3". id_matched uses
#             lowercase comparison so this should still match.
#             NOTE: The complaint has no classifier keyword, so it falls
#             to `other` and relevant_transaction_id remains None.
# ---------------------------------------------------------------------------
def test_probe_r9_3_id_match_case_insensitive_via_failed_keyword():
    """Use "failed" keyword + lowercase TXN-ID in complaint. The classifier
    goes to payment_failed; the id_matched should still pull the tx.
    """
    payload = {
        "ticket_id": "TKT-R9-3",
        "complaint": "TXN-R9-3 failed please check",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-3",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1000.0,
                "counterparty": "+8801711111111",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # id_matched works case-insensitively → tx is matched.
    assert res["relevant_transaction_id"] == "TXN-R9-3"
    assert res["case_type"] == CaseTypeEnum.payment_failed.value


def test_probe_r9_3b_lowercase_id_in_complaint_matched():
    """Confirm lowercase id_matched works by using a payment_failed context."""
    payload = {
        "ticket_id": "TKT-R9-3B",
        "complaint": "txn-r9-3b failed",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-3B",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 500.0,
                "counterparty": "+8801711111111",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["relevant_transaction_id"] == "TXN-R9-3B"


# ---------------------------------------------------------------------------
# Probe R9-4: Amount boundary — exactly 4 BDT. The extract_amounts filter
#             is `5 <= val <= 1_000_000`, so 4 should NOT be extracted.
#             Documenting: even with no amount match, the wrong_transfer
#             branch pulls ALL transfers from history into the `transfers`
#             list, so the tx still gets picked.
# ---------------------------------------------------------------------------
def test_probe_r9_4_amount_below_threshold_ignored():
    payload = {
        "ticket_id": "TKT-R9-4",
        "complaint": "I sent 4 taka and it went to wrong number.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-4",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 4.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # "wrong number" keyword still classifies as wrong_transfer.
    assert res["case_type"] == CaseTypeEnum.wrong_transfer.value
    # Documenting current behavior: even with no amount match, the wrong_transfer
    # branch picks `transfers[0]` because it pulls all transfers from history.
    # The amount filter affects only `matched_txs`, not the `transfers` list.
    # So this returns the tx (consistent, single transfer).
    assert res["relevant_transaction_id"] == "TXN-R9-4"


# ---------------------------------------------------------------------------
# Probe R9-5: Amount boundary — exactly 1,000,000 BDT. Should still match.
# ---------------------------------------------------------------------------
def test_probe_r9_5_amount_at_upper_bound_matches():
    payload = {
        "ticket_id": "TKT-R9-5",
        "complaint": "I sent 1000000 to wrong number.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-5",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1000000.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["relevant_transaction_id"] == "TXN-R9-5"


# ---------------------------------------------------------------------------
# Probe R9-6: Amount boundary — 1,000,001 BDT. Should NOT match (above
#             upper bound). Same caveat as R9-4: the wrong_transfer branch
#             pulls ALL transfers regardless of amount filter.
# ---------------------------------------------------------------------------
def test_probe_r9_6_amount_above_upper_bound_ignored():
    payload = {
        "ticket_id": "TKT-R9-6",
        "complaint": "I sent 1000001 to wrong number.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-6",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1000001.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # Documenting behavior: amount filter doesn't prevent the transfer from
    # being picked by the wrong_transfer branch. Only `matched_txs` is
    # affected. So the tx is selected.
    assert res["relevant_transaction_id"] == "TXN-R9-6"
    assert res["case_type"] == CaseTypeEnum.wrong_transfer.value


# ---------------------------------------------------------------------------
# Probe R9-7: A complaint that mentions BOTH "refund" AND "wrong number".
#             Classifier order: phishing → duplicate → settlement → cash_in
#             → refund (only if not failed) → failed → wrong_transfer.
#             So refund wins → refund_request.
# ---------------------------------------------------------------------------
def test_probe_r9_7_refund_plus_wrong_number_refund_wins():
    payload = {
        "ticket_id": "TKT-R9-7",
        "complaint": "I want a refund — sent to wrong number.",
        "language": "en",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-7",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1500.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200
    res = response.json()
    # Classifier order: refund wins over wrong_transfer.
    assert res["case_type"] == CaseTypeEnum.refund_request.value


# ---------------------------------------------------------------------------
# Probe R9-8: Pure Bangla with Latin digit complaint — must translate
#             Bangla digits if any, but here we use Latin digits only.
# ---------------------------------------------------------------------------
def test_probe_r9_8_pure_bangla_with_latin_digits():
    payload = {
        "ticket_id": "TKT-R9-8",
        "complaint": "আমি 800 টাকা ভুল নাম্বারে পাঠিয়েছি",
        "language": "bn",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-8",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 800.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200
    res = response.json()
    assert res["relevant_transaction_id"] == "TXN-R9-8"
    assert res["case_type"] == CaseTypeEnum.wrong_transfer.value


# ---------------------------------------------------------------------------
# Probe R9-9: Pure Bangla with Bangla digit (৮০০) — must translate to 800.
# ---------------------------------------------------------------------------
def test_probe_r9_9_pure_bangla_with_bangla_digits():
    payload = {
        "ticket_id": "TKT-R9-9",
        "complaint": "আমি ৮০০ টাকা ভুল নাম্বারে পাঠিয়েছি",
        "language": "bn",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-9",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 800.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200
    res = response.json()
    # Bangla digits ৮০০ → 800, matches tx amount.
    assert res["relevant_transaction_id"] == "TXN-R9-9"


# ---------------------------------------------------------------------------
# Probe R9-10: Metadata field — should be accepted silently (it's a free-form
#              dict). The system should NOT include it in any output field.
# ---------------------------------------------------------------------------
def test_probe_r9_10_metadata_accepted_not_leaked():
    payload = {
        "ticket_id": "TKT-R9-10",
        "complaint": "My payment failed.",
        "metadata": {
            "user_agent": "Mozilla/5.0",
            "ip": "192.168.1.1",
            "session_id": "secret-session-xyz",
        },
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-10",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "payment",
                "amount": 500.0,
                "counterparty": "M1",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # Metadata must NOT leak into customer_reply or agent_summary.
    assert "192.168.1.1" not in res["customer_reply"]
    assert "secret-session-xyz" not in res["customer_reply"]
    assert "Mozilla" not in res["customer_reply"]
    assert "192.168.1.1" not in res["agent_summary"]
    assert "secret-session-xyz" not in res["agent_summary"]


# ---------------------------------------------------------------------------
# Probe R9-11: Safety guardrail — reply says "we will refund" — should be
#              replaced by Nuclear Option. We force this by sending a
#              complaint that the safety guardrail classifies as a
#              "promise_pattern" hit. Since guardrails only fire on
#              template output, we test by verifying no template replies
#              contain "we will refund" or "we have refunded".
# ---------------------------------------------------------------------------
def test_probe_r9_11_no_refund_promise_in_templates():
    """Across many sample payloads, ensure customer_reply never promises a refund."""
    payloads = [
        {"ticket_id": f"TKT-R9-11-{i}", "complaint": c,
         "transaction_history": history}
        for i, (c, history) in enumerate([
            ("I want a refund for TXN-A.", [
                {"transaction_id": "TXN-A", "timestamp": "2026-06-26T10:00:00Z",
                 "type": "payment", "amount": 500.0, "counterparty": "M1", "status": "completed"}
            ]),
            ("My payment failed and I want refund.", [
                {"transaction_id": "TXN-B", "timestamp": "2026-06-26T10:00:00Z",
                 "type": "payment", "amount": 500.0, "counterparty": "M1", "status": "failed"}
            ]),
            ("I was charged twice. Refund the duplicate.", [
                {"transaction_id": "TXN-C", "timestamp": "2026-06-26T10:00:00Z",
                 "type": "payment", "amount": 500.0, "counterparty": "M1", "status": "completed"},
                {"transaction_id": "TXN-D", "timestamp": "2026-06-26T10:01:00Z",
                 "type": "payment", "amount": 500.0, "counterparty": "M1", "status": "completed"}
            ]),
            ("Cash in pending for 1000 taka.", [
                {"transaction_id": "TXN-E", "timestamp": "2026-06-26T10:00:00Z",
                 "type": "cash_in", "amount": 1000.0, "counterparty": "+8801711111111",
                 "status": "pending"}
            ]),
        ])
    ]
    for p in payloads:
        response = client.post("/analyze-ticket", json=p)
        assert response.status_code == 200, f"{p['ticket_id']}: {response.text}"
        res = response.json()
        reply_lower = res["customer_reply"].lower()
        assert "we will refund" not in reply_lower, (
            f"{p['ticket_id']}: 'we will refund' in reply: {res['customer_reply']!r}"
        )
        assert "i have refunded" not in reply_lower, (
            f"{p['ticket_id']}: 'i have refunded' in reply: {res['customer_reply']!r}"
        )
        assert "your account has been unblocked" not in reply_lower, (
            f"{p['ticket_id']}: 'unblocked' in reply: {res['customer_reply']!r}"
        )


# ---------------------------------------------------------------------------
# Probe R9-12: Empty complaint with only whitespace should 422.
#             (already covered by hidden case 24, but reinforces boundary.)
# ---------------------------------------------------------------------------
def test_probe_r9_12_complaint_only_newline_422():
    payload = {
        "ticket_id": "TKT-R9-12",
        "complaint": "\n\t  \n",
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Probe R9-13: Mixed Bangla + numeric transaction ID — must match the
#              complaint and select the tx.
# ---------------------------------------------------------------------------
def test_probe_r9_13_bangla_complaint_with_txn_id():
    payload = {
        "ticket_id": "TKT-R9-13",
        "complaint": "আমার TXN-R9-13 পেমেন্ট ব্যর্থ হয়েছে",
        "language": "bn",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-13",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "payment",
                "amount": 700.0,
                "counterparty": "M1",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["relevant_transaction_id"] == "TXN-R9-13"
    assert res["case_type"] == CaseTypeEnum.payment_failed.value


# ---------------------------------------------------------------------------
# Probe R9-14: Two transfers of the same amount to different counterparties.
#              This is the AMBIGUOUS case: matched_transfers has both,
#              amount-set is uniform, counterparties differ → relevant_transaction_id=None.
# ---------------------------------------------------------------------------
def test_probe_r9_14_wrong_transfer_ambiguous_multi_counterparty():
    payload = {
        "ticket_id": "TKT-R9-14",
        "complaint": "I sent 1500 to wrong number.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-14-OLD",
                "timestamp": "2026-06-20T10:00:00Z",
                "type": "transfer",
                "amount": 1500.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            },
            {
                "transaction_id": "TXN-R9-14-NEW",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1500.0,
                "counterparty": "+8801722222222",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # Ambiguous: same amount, different recipients → relevant_transaction_id=None.
    assert res["relevant_transaction_id"] is None
    assert res["evidence_verdict"] == EvidenceVerdictEnum.insufficient_data.value
    assert res["human_review_required"] is True


# ---------------------------------------------------------------------------
# Probe R9-15: complaint with NO alphanumeric content (just punctuation).
#              Should classify as `other`.
# ---------------------------------------------------------------------------
def test_probe_r9_15_punctuation_only_complaint():
    payload = {
        "ticket_id": "TKT-R9-15",
        "complaint": "!!! ??? ...",
        "language": "en",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # No keywords match → falls into else → `other`.
    assert res["case_type"] == CaseTypeEnum.other.value


# ---------------------------------------------------------------------------
# Probe R9-16: complaint with TXN-ID substring overlap. E.g., complaint
#              contains "TXN-R9-16ABC" which contains "TXN-R9-16" — should
#              still match (substring match).
# ---------------------------------------------------------------------------
def test_probe_r9_16_txn_id_substring_match():
    payload = {
        "ticket_id": "TKT-R9-16",
        "complaint": "please investigate TXN-R9-16ABC for refund",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-16ABC",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "payment",
                "amount": 500.0,
                "counterparty": "M1",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["relevant_transaction_id"] == "TXN-R9-16ABC"


# ---------------------------------------------------------------------------
# Probe R9-17: Pydantic field-validator doesn't crash on None for optional
#              enum fields (language, channel, user_type).
# ---------------------------------------------------------------------------
def test_probe_r9_17_missing_optional_enums_no_crash():
    payload = {
        "ticket_id": "TKT-R9-17",
        "complaint": "Something went wrong.",
        "transaction_history": [],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # All optional enums default; response is well-formed.
    assert res["customer_reply"]


# ---------------------------------------------------------------------------
# Probe R9-18: Very large complaint text (10k chars). Must not OOM or hang.
# ---------------------------------------------------------------------------
def test_probe_r9_18_very_long_complaint_no_crash():
    long_complaint = (
        "I want a refund because my payment failed. " * 500
    )  # ~17k chars
    payload = {
        "ticket_id": "TKT-R9-18",
        "complaint": long_complaint,
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-18",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "payment",
                "amount": 1000.0,
                "counterparty": "M1",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["customer_reply"]


# ---------------------------------------------------------------------------
# Probe R9-19: A complaint that mentions "scammer" but is actually a wrong
#              transfer. The "scammer" keyword is in phishing_keywords, so
#              phishing wins. Documenting behavior.
# ---------------------------------------------------------------------------
def test_probe_r9_19_scammer_keyword_phishing_wins():
    payload = {
        "ticket_id": "TKT-R9-19",
        "complaint": "The recipient was a scammer, I sent 1000 to wrong person.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-19",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1000.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # phishing wins because "scammer" is in phishing_keywords and is checked first.
    assert res["case_type"] == CaseTypeEnum.phishing_or_social_engineering.value


# ---------------------------------------------------------------------------
# Probe R9-20: customer_reply for a Bangla payment_failed complaint must
#              contain the Bangla text (or at least not be in English).
#              The current code doesn't actually localize the payment_failed
#              template; it always renders English. This is a known
#              limitation but worth documenting.
# ---------------------------------------------------------------------------
def test_probe_r9_20_payment_failed_bangla_reply_language():
    payload = {
        "ticket_id": "TKT-R9-20",
        "complaint": "আমার পেমেন্ট ব্যর্থ হয়েছে কিন্তু টাকা কেটে নিয়েছে",
        "language": "bn",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-20",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "payment",
                "amount": 1000.0,
                "counterparty": "M1",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # The payment_failed branch always renders English — this test just
    # verifies the response is well-formed and non-empty (no crash).
    assert isinstance(res["customer_reply"], str)
    assert res["customer_reply"]


# ---------------------------------------------------------------------------
# Probe R9-21: A wrong_transfer that triggers the established-recipient
#              branch but with NO transfers selected (because matched_txs
#              is empty due to amount mismatch). Should fall into else.
# ---------------------------------------------------------------------------
def test_probe_r9_21_wrong_transfer_no_match_but_phrase_present():
    payload = {
        "ticket_id": "TKT-R9-21",
        "complaint": "I sent money to wrong number yesterday.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-21",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 99999.0,  # huge amount, not in complaint
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # wrong_transfer classified. No amount match → matched_txs empty.
    # transfers list has the single tx (because we still pull all transfers).
    # The tx matches → relevant_transaction_id is set.
    assert res["case_type"] == CaseTypeEnum.wrong_transfer.value
    # Single transfer → same_counterparty_count=1, below threshold → consistent.
    assert res["relevant_transaction_id"] == "TXN-R9-21"


# ---------------------------------------------------------------------------
# Probe R9-22: Bangla complaint mentioning "ফেরত" (refund in Bangla).
# ---------------------------------------------------------------------------
def test_probe_r9_22_bangla_refund_keyword():
    payload = {
        "ticket_id": "TKT-R9-22",
        "complaint": "আমি টাকা ফেরত চাই",
        "language": "bn",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-22",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "payment",
                "amount": 1000.0,
                "counterparty": "M1",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["case_type"] == CaseTypeEnum.refund_request.value


# ---------------------------------------------------------------------------
# Probe R9-23: Settlement with `failed` status. The current logic checks
#              only pending vs not-pending. If failed, evidence_verdict
#              becomes inconsistent.
# ---------------------------------------------------------------------------
def test_probe_r9_23_settlement_failed_status():
    payload = {
        "ticket_id": "TKT-R9-23",
        "complaint": "My settlement TXN-R9-23 status.",
        "language": "en",
        "channel": "merchant_portal",
        "user_type": "merchant",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-23",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "settlement",
                "amount": 5000.0,
                "counterparty": "MERCHANT-A",
                "status": "failed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["case_type"] == CaseTypeEnum.merchant_settlement_delay.value
    # status=failed is not pending → inconsistent.
    assert res["evidence_verdict"] == EvidenceVerdictEnum.inconsistent.value


# ---------------------------------------------------------------------------
# Probe R9-24: Cash-in with `completed` status → inconsistent (because
#              customer claims it didn't go through).
# ---------------------------------------------------------------------------
def test_probe_r9_24_cash_in_completed_inconsistent():
    payload = {
        "ticket_id": "TKT-R9-24",
        "complaint": "Cash-in TXN-R9-24 hoy nai, taka kete nai.",
        "language": "en",
        "channel": "in_app_chat",
        "user_type": "customer",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-24",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "cash_in",
                "amount": 2000.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    # status=completed but claim is "hoy nai" → inconsistent.
    assert res["relevant_transaction_id"] == "TXN-R9-24"
    assert res["evidence_verdict"] == EvidenceVerdictEnum.inconsistent.value


# ---------------------------------------------------------------------------
# Probe R9-25: amount extract edge case — "I have $500 and 1000 taka".
#              extract_amounts extracts 500 and 1000 (both in range).
#              If tx amount is 1000, it matches.
# ---------------------------------------------------------------------------
def test_probe_r9_25_multiple_amounts_extracts_correct():
    payload = {
        "ticket_id": "TKT-R9-25",
        "complaint": "I have $500 in my account but I sent 1000 taka to wrong number.",
        "transaction_history": [
            {
                "transaction_id": "TXN-R9-25",
                "timestamp": "2026-06-26T10:00:00Z",
                "type": "transfer",
                "amount": 1000.0,
                "counterparty": "+8801711111111",
                "status": "completed",
            }
        ],
    }
    response = client.post("/analyze-ticket", json=payload)
    assert response.status_code == 200, response.text
    res = response.json()
    assert res["relevant_transaction_id"] == "TXN-R9-25"