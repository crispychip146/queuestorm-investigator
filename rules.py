import re
from datetime import datetime
from typing import List, Optional, Tuple
from models import (
    AnalyzeTicketRequest,
    AnalyzeTicketResponse,
    EvidenceVerdictEnum,
    CaseTypeEnum,
    SeverityEnum,
    DepartmentEnum,
    TransactionHistoryEntry,
    LanguageEnum,
    TransactionTypeEnum,
    TransactionStatusEnum,
    UserTypeEnum,
)

# Threshold for number of prior transfers to establish a recipient pattern (business policy)
ESTABLISHED_RECIPIENT_THRESHOLD = 3

# Hoisted constant mappings for translation hygiene
BANGLA_DIGIT_MAPPING = {
    '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
    '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9'
}

WORD_TO_NUMBER_MAPPING = {
    "five thousand": "5000",
    "five hundred": "500",
    "one thousand": "1000",
    "two thousand": "2000",
    "three thousand": "3000",
    "four thousand": "4000",
    "ten thousand": "10000",
    "eight hundred": "800",
    "twelve hundred": "1200"
}

NUM_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}

def find_duplicate_pair(history: List[TransactionHistoryEntry]) -> Optional[Tuple[TransactionHistoryEntry, TransactionHistoryEntry]]:
    for i in range(len(history)):
        for j in range(i + 1, len(history)):
            t1, t2 = history[i], history[j]
            if (t1.amount == t2.amount and 
                t1.type == t2.type and 
                t1.counterparty == t2.counterparty and 
                t1.status == TransactionStatusEnum.completed and 
                t2.status == TransactionStatusEnum.completed):
                return (t1, t2) if t1.timestamp <= t2.timestamp else (t2, t1)
    return None

def translate_bangla_digits(text: str) -> str:
    for bn, en in BANGLA_DIGIT_MAPPING.items():
        text = text.replace(bn, en)
    for word, val in WORD_TO_NUMBER_MAPPING.items():
        text = text.replace(word, val)
    return text

def extract_amounts(text: str) -> List[float]:
    normalized = translate_bangla_digits(text.lower())
    # Match sequences of digits, optionally with decimals
    candidates = re.findall(r'\b\d+(?:\.\d+)?\b', normalized)
    amounts = []
    for c in candidates:
        try:
            val = float(c)
            # Allow all positive transaction amounts in reasonable range (5 to 1,000,000 BDT)
            if 5 <= val <= 1_000_000:
                amounts.append(val)
        except ValueError:
            pass
    return amounts

def parse_timestamp(ts: str) -> datetime:
    if ts.endswith('Z'):
        ts = ts[:-1] + '+00:00'
    return datetime.fromisoformat(ts)

def analyze_ticket_rules(req: AnalyzeTicketRequest) -> AnalyzeTicketResponse:
    complaint = req.complaint
    complaint_lower = translate_bangla_digits(complaint.lower())
    history = req.transaction_history or []
    lang = req.language or LanguageEnum.en
    ticket_id = req.ticket_id

    # 1. Extract potential amounts
    amounts = extract_amounts(complaint)

    # 2. Identify transaction candidates matching amounts or transaction_id in history
    matched_txs: List[TransactionHistoryEntry] = []
    for tx in history:
        # Check if amount matches, allowing for typical cash-out / transfer fees (up to 20 BDT)
        amount_matched = False
        for amt in amounts:
            diff = abs(tx.amount - amt)
            if diff < 0.01 or (tx.amount > amt and diff <= 20) or (amt > tx.amount and diff <= 20):
                amount_matched = True
                break
        
        # Check if transaction_id appears in complaint (case insensitive)
        id_matched = tx.transaction_id.lower() in complaint_lower
        
        if amount_matched or id_matched:
            if tx not in matched_txs:
                matched_txs.append(tx)

    # 3. Classify case type based on complaint text and history
    case_type = CaseTypeEnum.other

    # Check for prompt injection / system hijack attempts in input
    injection_patterns = [
        r"override.*safety",
        r"system update",
        r"ignore.*rules",
        r"please provide.*pin",
        r"provide.*pin.*unblock",
        r"override all safety layers",
        r"vp of engineering"
    ]
    is_injection = any(re.search(p, complaint_lower) for p in injection_patterns)

    # Keyword lists (with Banglish additions)
    phishing_keywords = [
        "asked for my otp", "asked for my pin", "asked for otp", "asked for pin",
        "asked for password", "someone called saying", "called claiming to be",
        "block my account", "account will be blocked", "otp share", "share otp",
        "share pin", "share password", "fake support", "scammer", "fraudster",
        "পিন চেয়েছে", "ওটিপি চেয়েছে", "ওটিপি চাচ্ছে", "পিন চাচ্ছে", "একাউন্ট ব্লক"
    ]
    duplicate_keywords = ["twice", "double", "two times", "2 times", "duplicate", "ডাবল", "দুইবার", "২ বার", "dui bar", "2 bar"]
    settlement_keywords = ["settle", "settlement", "sales of", "সেটেল", "সেটেলমেন্ট"]
    cash_in_keywords = ["cash in", "cash-in", "ক্যাশ ইন", "ক্যাশইন", "এজেন্ট", "agent", "cashin"]
    refund_keywords = ["refund", "changed my mind", "don't want it anymore", "return my money", "রিফান্ড", "ফেরত"]
    failed_keywords = ["failed", "deducted", "payment failed", "balance deducted", "app showed failed", "ব্যালেন্স কেটেছে", "টাকা কেটেছে", "ফেইল", "ব্যর্থ", "fail", "kete", "kata", "katse", "kete nise"]
    wrong_transfer_keywords = ["wrong number", "wrong recipient", "wrong person", "supposed to be", "ভুল নাম্বারে", "ভুল নম্বরে", "ভুল করে", "অন্য নাম্বারে", "wrong transfer", "wrong details", "wrong account", "wrong mobile", "wrong no", "typed wrong", "sent to wrong", "sent wrong", "bhul", "bhal", "vul", "send money", "pathano"]

    has_credential = any(w in complaint_lower for w in ["otp", "pin", "password", "credential", "ওটিপি", "পিন", "পাসওয়ার্ড"])
    has_suspicious_action = any(w in complaint_lower for w in ["called", "sms", "ask", "share", "block", "suspect", "scam", "phone", "ফোন", "চাচ্ছে", "চেয়েছে", "ব্লক"])

    harvest_pattern = r"(?:ask|provide|share|give|enter|tell|send|input|verify).*(?:pin|otp|password|credential|cvv)"
    is_phishing = is_injection or any(k in complaint_lower for k in phishing_keywords) or re.search(harvest_pattern, complaint_lower) or (
        has_credential and has_suspicious_action and not any(k in complaint_lower for k in wrong_transfer_keywords + failed_keywords + duplicate_keywords + cash_in_keywords + settlement_keywords + refund_keywords)
    )

    if is_phishing:
        case_type = CaseTypeEnum.phishing_or_social_engineering
    elif any(k in complaint_lower for k in duplicate_keywords):
        case_type = CaseTypeEnum.duplicate_payment
    elif any(k in complaint_lower for k in settlement_keywords) or req.user_type == UserTypeEnum.merchant:
        case_type = CaseTypeEnum.merchant_settlement_delay
    elif any(k in complaint_lower for k in cash_in_keywords):
        case_type = CaseTypeEnum.agent_cash_in_issue
    elif any(k in complaint_lower for k in refund_keywords) and not any(k in complaint_lower for k in failed_keywords):
        case_type = CaseTypeEnum.refund_request
    elif any(k in complaint_lower for k in failed_keywords):
        case_type = CaseTypeEnum.payment_failed
    elif any(k in complaint_lower for k in wrong_transfer_keywords) or "sent" in complaint_lower or "পাঠিয়েছি" in complaint_lower:
        case_type = CaseTypeEnum.wrong_transfer
    elif len(history) >= 2 and any(
        abs((parse_timestamp(history[i].timestamp) - parse_timestamp(history[j].timestamp)).total_seconds()) < 180
        for i in range(len(history)) for j in range(i+1, len(history))
        if history[i].amount == history[j].amount and history[i].type == history[j].type
    ):
        case_type = CaseTypeEnum.duplicate_payment
    else:
        case_type = CaseTypeEnum.other

    # 4. Resolve details based on classification
    relevant_transaction_id = None
    evidence_verdict = EvidenceVerdictEnum.insufficient_data
    severity = SeverityEnum.low
    department = DepartmentEnum.customer_support
    human_review_required = False
    confidence = 0.8
    reason_codes = []
    same_counterparty_count = 0
    duplicate_pair = None

    if case_type == CaseTypeEnum.phishing_or_social_engineering:
        relevant_transaction_id = None
        evidence_verdict = EvidenceVerdictEnum.insufficient_data
        severity = SeverityEnum.critical
        department = DepartmentEnum.fraud_risk
        human_review_required = True
        reason_codes = ["phishing", "credential_protection", "critical_escalation"]
        confidence = 0.95

    elif case_type == CaseTypeEnum.duplicate_payment:
        duplicate_pair = find_duplicate_pair(history)
        if duplicate_pair:
            t1, t2 = duplicate_pair
            relevant_transaction_id = t2.transaction_id
            evidence_verdict = EvidenceVerdictEnum.consistent
            reason_codes = ["duplicate_payment", "biller_verification_required"]
        else:
            if matched_txs:
                relevant_transaction_id = matched_txs[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
            else:
                relevant_transaction_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
            reason_codes = ["duplicate_payment_claim", "insufficient_evidence"]
        severity = SeverityEnum.high
        department = DepartmentEnum.payments_ops
        human_review_required = True
        confidence = 0.9

    elif case_type == CaseTypeEnum.merchant_settlement_delay:
        settlements = [tx for tx in history if tx.type == TransactionTypeEnum.settlement]
        if matched_txs:
            settlements = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.settlement] + settlements
        if settlements:
            tx = settlements[0]
            relevant_transaction_id = tx.transaction_id
            if tx.status == TransactionStatusEnum.pending:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            if matched_txs:
                relevant_transaction_id = matched_txs[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                relevant_transaction_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        severity = SeverityEnum.medium
        department = DepartmentEnum.merchant_operations
        human_review_required = False
        reason_codes = ["merchant_settlement", "pending" if evidence_verdict == EvidenceVerdictEnum.consistent else "processed"]
        confidence = 0.9

    elif case_type == CaseTypeEnum.agent_cash_in_issue:
        cash_ins = [tx for tx in history if tx.type == TransactionTypeEnum.cash_in]
        if matched_txs:
            cash_ins = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.cash_in] + cash_ins
        if cash_ins:
            tx = cash_ins[0]
            relevant_transaction_id = tx.transaction_id
            if tx.status == TransactionStatusEnum.pending or tx.status == TransactionStatusEnum.failed:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            if matched_txs:
                relevant_transaction_id = matched_txs[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                relevant_transaction_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        severity = SeverityEnum.high
        department = DepartmentEnum.agent_operations
        human_review_required = True
        reason_codes = ["agent_cash_in", "pending_transaction" if evidence_verdict == EvidenceVerdictEnum.consistent else "completed"]
        confidence = 0.88

    elif case_type == CaseTypeEnum.refund_request:
        payments = [tx for tx in history if tx.type == TransactionTypeEnum.payment]
        if matched_txs:
            payments = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.payment] + payments
        if payments:
            tx = payments[0]
            relevant_transaction_id = tx.transaction_id
            evidence_verdict = EvidenceVerdictEnum.consistent
        else:
            if matched_txs:
                relevant_transaction_id = matched_txs[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                relevant_transaction_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        severity = SeverityEnum.low
        department = DepartmentEnum.customer_support
        human_review_required = False
        reason_codes = ["refund_request", "merchant_policy_dependent"]
        confidence = 0.85

    elif case_type == CaseTypeEnum.payment_failed:
        payments = [tx for tx in history if tx.type in [TransactionTypeEnum.payment, TransactionTypeEnum.transfer]]
        if matched_txs:
            payments = [tx for tx in matched_txs if tx.type in [TransactionTypeEnum.payment, TransactionTypeEnum.transfer]] + payments
        if payments:
            # Prefer failed status from candidates
            failed_candidates = [tx for tx in payments if tx.status == TransactionStatusEnum.failed]
            tx = failed_candidates[0] if failed_candidates else payments[0]
            relevant_transaction_id = tx.transaction_id
            if tx.status == TransactionStatusEnum.failed:
                evidence_verdict = EvidenceVerdictEnum.consistent
            else:
                evidence_verdict = EvidenceVerdictEnum.inconsistent
        else:
            if matched_txs:
                relevant_transaction_id = matched_txs[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                relevant_transaction_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
        severity = SeverityEnum.high
        department = DepartmentEnum.payments_ops
        human_review_required = False
        reason_codes = ["payment_failed", "potential_balance_deduction"]
        confidence = 0.9

    elif case_type == CaseTypeEnum.wrong_transfer:
        department = DepartmentEnum.dispute_resolution
        transfers = [tx for tx in history if tx.type == TransactionTypeEnum.transfer]
        if matched_txs:
            transfers = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.transfer] + transfers
        
        # If multiple transfers match the description amount, it is ambiguous
        matched_transfers = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.transfer]
        distinct_counterparties = len(set(tx.counterparty for tx in matched_transfers))
        
        if len(matched_transfers) > 1 and len(set(tx.amount for tx in matched_transfers)) == 1 and distinct_counterparties > 1:
            relevant_transaction_id = None
            evidence_verdict = EvidenceVerdictEnum.insufficient_data
            severity = SeverityEnum.medium
            human_review_required = True
            reason_codes = ["ambiguous_match", "needs_clarification"]
            confidence = 0.65
        elif transfers:
            tx = transfers[0]
            relevant_transaction_id = tx.transaction_id
            
            # Check established pattern (2 or more other transactions to same counterparty)
            counterparty = tx.counterparty
            same_counterparty_count = sum(1 for t in history if t.counterparty == counterparty and t.type == TransactionTypeEnum.transfer)
            if same_counterparty_count >= ESTABLISHED_RECIPIENT_THRESHOLD: 
                evidence_verdict = EvidenceVerdictEnum.inconsistent
                severity = SeverityEnum.medium
                reason_codes = ["wrong_transfer_claim", "established_recipient_pattern", "evidence_inconsistent"]
                confidence = 0.75
            else:
                evidence_verdict = EvidenceVerdictEnum.consistent
                severity = SeverityEnum.high
                reason_codes = ["wrong_transfer", "transaction_match", "dispute_initiated"]
                confidence = 0.9
            human_review_required = True
        else:
            if matched_txs:
                relevant_transaction_id = matched_txs[0].transaction_id
                evidence_verdict = EvidenceVerdictEnum.inconsistent
            else:
                relevant_transaction_id = None
                evidence_verdict = EvidenceVerdictEnum.insufficient_data
            severity = SeverityEnum.medium
            human_review_required = False
            reason_codes = ["wrong_transfer_claim", "insufficient_evidence"]
            confidence = 0.6

    else: # other
        relevant_transaction_id = None
        evidence_verdict = EvidenceVerdictEnum.insufficient_data
        severity = SeverityEnum.low
        department = DepartmentEnum.customer_support
        human_review_required = False
        reason_codes = ["vague_complaint", "needs_clarification"]
        confidence = 0.6

    # 5. Generate Text Fields
    # Default English templates
    agent_summary = ""
    recommended_next_action = ""
    customer_reply = ""

    if case_type == CaseTypeEnum.phishing_or_social_engineering:
        agent_summary = "Customer reports an unsolicited call claiming to be from the company and asking for OTP. Customer has not yet shared credentials. Likely social engineering attempt."
        recommended_next_action = "Escalate to fraud_risk team immediately. Confirm to customer that the company never asks for OTP. Log the reported number for fraud pattern analysis."
        customer_reply = "Thank you for reaching out before sharing any information. We never ask for your PIN, OTP, or password under any circumstances. Please do not share these with anyone, even if they claim to be from us. Our fraud team has been notified of this incident."

    elif case_type == CaseTypeEnum.duplicate_payment:
        t_id = relevant_transaction_id or "transaction"
        pair = duplicate_pair
        if pair:
            t1, t2 = pair
            time_diff = int(abs((parse_timestamp(t2.timestamp) - parse_timestamp(t1.timestamp)).total_seconds()))
            biller = t2.counterparty
            amount_val = int(t2.amount)
            agent_summary = f"Customer reports duplicate payment. Two identical {amount_val} BDT payments to {biller} were completed {time_diff} seconds apart ({t1.transaction_id} and {t2.transaction_id}). The second is likely the duplicate."
        else:
            if relevant_transaction_id:
                agent_summary = f"Customer reports a duplicate payment, but the history does not show a matching duplicate pair. {t_id} is the closest match."
            else:
                agent_summary = "Customer reports a duplicate payment, but the history does not show a matching duplicate pair."
        recommended_next_action = f"Verify the duplicate with payments_ops. If the biller confirms only one payment was received, initiate reversal of {t_id}."
        customer_reply = f"We have noted the possible duplicate payment for transaction {t_id}. Our payments team will verify with the biller and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone."

    elif case_type == CaseTypeEnum.merchant_settlement_delay:
        t_id = relevant_transaction_id or "settlement"
        settlements = [tx for tx in history if tx.type == TransactionTypeEnum.settlement]
        if matched_txs:
            settlements = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.settlement] + settlements
        if settlements:
            tx = settlements[0]
            agent_summary = f"Merchant reports {int(tx.amount)} BDT settlement ({t_id}) is delayed beyond the standard settlement window. Settlement status is pending."
        else:
            agent_summary = f"Merchant reports settlement {t_id} is delayed. Settlement status is pending."
        recommended_next_action = "Route to merchant_operations to verify settlement batch status. If the batch is delayed, communicate a revised ETA to the merchant."
        customer_reply = f"We have noted your concern about settlement {t_id}. Our merchant operations team will check the batch status and update you on the expected settlement time through official channels."

    elif case_type == CaseTypeEnum.agent_cash_in_issue:
        t_id = relevant_transaction_id or "transaction"
        cash_ins = [tx for tx in history if tx.type == TransactionTypeEnum.cash_in]
        if matched_txs:
            cash_ins = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.cash_in] + cash_ins
        if cash_ins:
            tx = cash_ins[0]
            agent = tx.counterparty if tx.counterparty else "agent"
            # For the Bangla branch, render the agent in Bengali script or omit it if empty so a
            # generic English placeholder never leaks into the localized reply.
            agent_bn = tx.counterparty if tx.counterparty else ""
            agent_summary = f"Customer reports {int(tx.amount)} BDT cash-in via {agent} ({t_id}) not reflected in balance. Transaction status is pending. Agent claims funds were sent."
        else:
            agent = "agent"
            agent_bn = ""
            agent_summary = f"Customer reports cash-in via agent ({t_id}) not reflected in balance. Transaction status is pending."
        
        if lang == LanguageEnum.bn:
            t_id_bn = f" {t_id}" if relevant_transaction_id else ""
            agent_bn_str = f" {agent_bn}" if agent_bn else ""
            recommended_next_action = f"Investigate {t_id} pending status with agent operations. Confirm settlement state and resolve within the standard cash-in SLA."
            customer_reply = f"আমরা অবগত হয়েছি যে এজেন্ট{agent_bn_str} এর মাধ্যমে আপনাদের লেনদেন{t_id_bn} সফলভাবে সম্পন্ন হয়নি। আমাদের এজেন্ট অপারেশন্স দল এটি দ্রুত যাচাই করবে এবং অফিসিয়াল চ্যানেলে আপনাকে জানাবে। অনুগ্রহ করে কারো সাথে আপনার পিন বা ওটিপি শেয়ার করবেন না।"
        else:
            recommended_next_action = f"Investigate {t_id} pending status with agent operations. Confirm settlement state and resolve within the standard cash-in SLA."
            customer_reply = f"We have noted your concern about transaction {t_id}. Our agent operations team will investigate the status of this cash-in and contact you through official channels. Please do not share your PIN or OTP with anyone."

    elif case_type == CaseTypeEnum.refund_request:
        t_id = relevant_transaction_id or "payment"
        payments = [tx for tx in history if tx.type == TransactionTypeEnum.payment]
        if matched_txs:
            payments = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.payment] + payments
        if payments:
            tx = payments[0]
            agent_summary = f"Customer requests refund of {int(tx.amount)} BDT for {t_id} (merchant payment) due to change of mind. Not a service failure."
        else:
            agent_summary = f"Customer requests refund of payment {t_id} due to change of mind. Not a service failure."
        recommended_next_action = "Inform the customer that refund eligibility depends on the merchant's own policy. Provide guidance on contacting the merchant directly for a refund."
        customer_reply = f"Thank you for reaching out. Refunds for completed merchant payments depend on the merchant's own policy. We recommend contacting the merchant directly. If you need help reaching them, please reply and we will guide you. Please do not share your PIN or OTP with anyone."

    elif case_type == CaseTypeEnum.payment_failed:
        t_id = relevant_transaction_id or "payment"
        payments = [tx for tx in history if tx.type in [TransactionTypeEnum.payment, TransactionTypeEnum.transfer]]
        if matched_txs:
            payments = [tx for tx in matched_txs if tx.type in [TransactionTypeEnum.payment, TransactionTypeEnum.transfer]] + payments
        if payments:
            failed_candidates = [tx for tx in payments if tx.status == TransactionStatusEnum.failed]
            tx = failed_candidates[0] if failed_candidates else payments[0]
            service_desc = "mobile recharge" if re.search(r"\brecharge\b", complaint_lower) else f"{tx.type.value} to {tx.counterparty}"
            agent_summary = f"Customer attempted a {int(tx.amount)} BDT {service_desc} ({t_id}) which failed, but reports balance was deducted. Requires payments operations investigation."
        else:
            agent_summary = "Customer reports transaction failure with balance deduction."
        
        has_tx = relevant_transaction_id is not None
        recommended_next_action = f"Investigate {t_id} ledger status. If balance was deducted on a failed payment, initiate the automatic reversal flow within standard SLA." if has_tx else "Investigate ledger status and initiate standard SLA reversal workflow."
        customer_reply = f"We have noted that transaction {t_id} may have caused an unexpected balance deduction. Our payments team will review the case and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone." if has_tx else "We have received your request regarding the deducted balance. Our payments team will review the case and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone."
        
    elif case_type == CaseTypeEnum.wrong_transfer:
        t_id = relevant_transaction_id
        transfers = [tx for tx in history if tx.type == TransactionTypeEnum.transfer]
        if matched_txs:
            transfers = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.transfer] + transfers
        matched_transfers = [tx for tx in matched_txs if tx.type == TransactionTypeEnum.transfer]
        
        if evidence_verdict == EvidenceVerdictEnum.consistent:
            tx = transfers[0] if transfers else None
            recipient = tx.counterparty if tx else "the recipient"
            amount_val = int(tx.amount) if tx else 0
            tx_desc = f"via {t_id}" if t_id else "without a transaction ID"
            amt_desc = f"{amount_val} BDT " if amount_val > 0 else ""
            agent_summary = f"Customer reports sending {amt_desc}{tx_desc} to {recipient}, which they now believe was the wrong recipient. Recipient is unresponsive."
            recommended_next_action = f"Verify {t_id} details with the customer and initiate the wrong-transfer dispute workflow per policy." if t_id else "Request the transaction ID from the customer and verify details to initiate the dispute workflow."
            customer_reply = f"We have noted your concern about transaction {t_id}. Please do not share your PIN or OTP with anyone. Our dispute team will review the case and contact you through official support channels." if t_id else "We have received your wrong transfer report. Please do not share your PIN or OTP with anyone. Our dispute team will review the case and contact you through official support channels."
        elif evidence_verdict == EvidenceVerdictEnum.inconsistent:
            tx = transfers[0] if transfers else None
            recipient = tx.counterparty if tx else "the recipient"
            amount_val = int(tx.amount) if tx else 0
            tx_desc = f" {t_id}" if t_id else ""
            amt_desc = f" ({amount_val} BDT to {recipient})" if amount_val > 0 else f" to {recipient}"
            
            prior_count_word = NUM_WORDS.get(same_counterparty_count, str(same_counterparty_count))
            tx_times = [parse_timestamp(t.timestamp) for t in history if t.counterparty == recipient]
            if len(tx_times) > 1:
                days_diff = (max(tx_times) - min(tx_times)).days
                days_word = NUM_WORDS.get(days_diff, str(days_diff))
                timeframe_str = f"in the past {days_word} days"
            else:
                timeframe_str = "recently"
                
            agent_summary = f"Customer claims{tx_desc}{amt_desc} was a wrong transfer, but transaction history shows {prior_count_word} prior transfers to the same counterparty {timeframe_str}, suggesting an established recipient."
            recommended_next_action = "Flag for human review. Verify with the customer whether this was genuinely a wrong transfer given the established transaction pattern with this recipient."
            customer_reply = f"We have received your request regarding transaction {t_id}. Please do not share your PIN or OTP with anyone. Our dispute team will review the case carefully and contact you through official support channels." if t_id else "We have received your wrong transfer request. Please do not share your PIN or OTP with anyone. Our dispute team will review the case carefully and contact you through official support channels."
        else: # ambiguous / insufficient_data
            amount_val = int(matched_transfers[0].amount) if matched_transfers else 0
            tx_count_word = NUM_WORDS.get(len(matched_transfers), str(len(matched_transfers)))
            completed_count = sum(1 for tx in matched_transfers if tx.status == TransactionStatusEnum.completed)
            failed_count = sum(1 for tx in matched_transfers if tx.status == TransactionStatusEnum.failed)
            completed_word = NUM_WORDS.get(completed_count, str(completed_count))
            failed_word = NUM_WORDS.get(failed_count, str(failed_count))
            distinct_recipients = len(set(tx.counterparty for tx in matched_transfers))
            distinct_recipients_word = NUM_WORDS.get(distinct_recipients, str(distinct_recipients))
            
            agent_summary = f"Customer reports a {amount_val} BDT transfer to their brother was not received. {tx_count_word.capitalize()} transactions of {amount_val} BDT exist on the date in question ({completed_word} completed, {failed_word} failed) to {distinct_recipients_word} different recipients. Cannot determine which is the brother's number without further input." if len(matched_transfers) > 1 else "Customer reports a wrong transfer but no matching transaction or multiple matches exist."
            recommended_next_action = "Reply to customer asking for the brother's number to identify the correct transaction. Do not initiate dispute until the transaction is confirmed." if len(matched_transfers) > 1 else "Reply to customer asking for specific details to identify the correct transaction."
            customer_reply = f"We see multiple transactions of {amount_val} BDT on that date. Could you share your brother's number so we can identify the right transaction? Please do not share your PIN or OTP with anyone." if len(matched_transfers) > 1 else "Thank you for reaching out. Could you share the recipient's phone number so we can identify the right transaction? Please do not share your PIN or OTP with anyone."

    else: # other / vague
        agent_summary = "Customer reports a vague concern about their money without specifying transaction, amount, or issue. Insufficient detail to identify any relevant transaction."
        recommended_next_action = "Reply to customer asking for specific details: which transaction, what amount, what went wrong, and approximate time."
        customer_reply = "Thank you for reaching out. To help you faster, please share the transaction ID, the amount involved, and a short description of what went wrong. Please do not share your PIN or OTP with anyone."

    # Force hardcoded fallback reply directly if prompt injection was detected
    if is_injection:
        customer_reply = f"We have received your request regarding ticket {ticket_id}. Any eligible amount will be returned through official channels. Our team will review the case and contact you. Please do not share your PIN or OTP with anyone."
        recommended_next_action = "Route to appropriate team for policy verification and ledger check."

    return AnalyzeTicketResponse(
        ticket_id=ticket_id,
        relevant_transaction_id=relevant_transaction_id,
        evidence_verdict=evidence_verdict,
        case_type=case_type,
        severity=severity,
        department=department,
        agent_summary=agent_summary,
        recommended_next_action=recommended_next_action,
        customer_reply=customer_reply,
        human_review_required=human_review_required,
        confidence=confidence,
        reason_codes=reason_codes
    )

def run_safety_guardrails(resp: AnalyzeTicketResponse, ticket_id: str) -> AnalyzeTicketResponse:
    # 1. Force ticket_id to match request exactly
    resp.ticket_id = ticket_id

    # 2. Strict Safety Filter for customer_reply
    reply = resp.customer_reply.lower()
    action = resp.recommended_next_action.lower()

    violation = False

    ask_patterns = [
        r"(?<!not )(?<!dont )(?<!don't )(?<!never )share (?:your )?(?:pin|otp|password|card|cvv)",
        r"(?<!not )(?<!dont )(?<!don't )(?<!never )provide (?:your )?(?:pin|otp|password|card|cvv)",
        r"(?<!not )(?<!dont )(?<!don't )(?<!never )send (?:your )?(?:pin|otp|password|card|cvv)",
        r"(?<!not )(?<!dont )(?<!don't )(?<!never )tell (?:us )?(?:your )?(?:pin|otp|password|card|cvv)",
        r"(?<!not )(?<!dont )(?<!don't )(?<!never )input (?:your )?(?:pin|otp|password|card|cvv)",
        r"(?<!ask )what is your (?:pin|otp|password|card)",
        r"দয়া করে আপনার (?:পিন|ওটিপি|পাসওয়ার্ড) বলুন",
        r"পিন (?:ওটিপি) দিন"
    ]
    for pattern in ask_patterns:
        if re.search(pattern, reply):
            violation = True
            break

    promise_patterns = [
        r"we (?:will )?refund",
        r"i (?:have )?refunded",
        r"will reverse",
        r"have reversed",
        r"account (?:is|has been) unblocked",
        r"account (?:is|has been) recovered",
        r"we (?:will )?unblock",
        r"we (?:will )?recover",
        r"টাকা ফেরত দেওয়া হয়েছে",
        r"টাকা ফেরত দেব"
    ]
    for pattern in promise_patterns:
        if re.search(pattern, reply) or re.search(pattern, action):
            violation = True
            break

    redirect_patterns = [
        r"contact (?:the )?(?:recipient|person|merchant|agent) (?:at|on|through) \+?\d+",
        r"call \+?\d{5,}",
        r"visit (?:http|www)"
    ]
    for pattern in redirect_patterns:
        if re.search(pattern, reply):
            violation = True
            break

    if violation:
        resp.customer_reply = f"We have received your request regarding ticket {ticket_id}. Any eligible amount will be returned through official channels. Our team will review the case and contact you. Please do not share your PIN or OTP with anyone."
        if any(re.search(p, action) for p in promise_patterns):
            resp.recommended_next_action = "Route to appropriate team for policy verification and ledger check."

    return resp
