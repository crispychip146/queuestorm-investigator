# QueueStorm Investigator

### Hackathon Submission Metadata
* **Team Name**: aespo
* **Team ID**: [Insert Team ID here]
* **GitHub Repository URL**: https://github.com/crispychip146/queuestorm-investigator
* **Public Endpoint Base URL**: https://queuestorm-investigator-std0.onrender.com

---

QueueStorm Investigator is a safe, high-performance, and robust AI/API SupportOps copilot built for digital finance ticket investigation. It matches customer complaints with their transaction history, detects security and fraud patterns, and outputs structured case classifications and safe, pre-validated replies.

---

## 1. Tech Stack
* **FastAPI**: Modern, high-performance web framework for Python APIs.
* **Pydantic**: Data validation and settings management using Python type annotations.
* **Uvicorn**: Lightning-fast ASGI server implementation.
* **google-genai**: Modern Google GenAI SDK for Gemini API integration.
* **Docker**: For containerized deployment.
* **pytest**: Comprehensive testing tool (used as a development/validation dependency).

---

## 2. AI & Model Usage Explanation
To ensure 100% reproducibility, zero runtime API cost under standard test settings, and robust LLM capabilities when keys are available, the service uses a **Hybrid Rule + AI Architecture**:
1. **Gemini API Reasoning (AI Path)**: If `GEMINI_API_KEY` is present in the environment, the service calls the Gemini API (defaulting to `gemini-2.5-flash`) utilizing Structured JSON Outputs mapping directly to our Pydantic response models. The prompt treats user complaint text as untrusted content to guard against prompt injections.
2. **Deterministic RuleEngine (Fallback Path)**: If `GEMINI_API_KEY` is absent or the API call fails/times out (strict 4.0s boundary), the service falls back to a custom, high-fidelity Python RuleEngine. It parses amounts, dates, counterparties, types, and statuses using regex.

---

## 3. Safety Logic & Safeguards Explanation
Regardless of whether the AI Path or Fallback Path produces the response, it is passed through a deterministic safety processor. It scans `customer_reply` and `recommended_next_action` for security vulnerabilities:
* **Credential Harvest Protection**: Strict negative-lookbehind regexes (`(?<!not )(?<!dont )(?<!don't )(?<!never )`) verify that the agent never asks the customer for PIN, OTP, password, card number, or CVV. This permits safe disclaimers (e.g. *"Please do not share your PIN"*) while blocking credential harvests.
* **No Reversal/Refund Promises**: Verifies that the agent never makes binding refund or unblock promises in customer-facing communication, steering copy toward compliant statements (*"any eligible amount will be returned through official channels"*).
* **Anti-Redirect Safeguards**: Restricts the system from directing customers to suspicious third-party links or phone numbers.
* **The Nuclear Option**: If any guardrail violation is detected, the entire `customer_reply` is discarded and replaced with a hardcoded, universally safe fallback message:
  > *"We have received your request regarding ticket [ticket_id]. Any eligible amount will be returned through official channels. Our team will review the case and contact you. Please do not share your PIN or OTP with anyone."*
* **Anti-Injection Sanitization**: If a prompt injection attempt is detected on the incoming complaint (e.g., commands containing override instructions), the service immediately overwrites `customer_reply` and `recommended_next_action` with safe, generic placeholders.

---

## 4. Known Limitations & Edge Cases
* **Ambiguous Matches**: For wrong transfers where multiple transactions have the exact same amount on the same date but to different counterparties, the system cannot deduce the recipient. It flags the case for human review (`human_review_required=True`) and prompts the user for clarification.
* **Stray Single-Digit Noise**: The amount parser filters out numbers below 5 BDT to avoid classifying stray numbers (like "1 complaint" or "2 days") as transaction amounts, but this prevents handling extremely small transactions below 5 BDT.
* **Text Length Bounds**: To prevent Denial of Service or Denial of Wallet attacks via token bloating, `ticket_id` is constrained to a maximum of 100 characters.

---

## 5. Environment Variables

| Variable Name | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `PORT` | Optional | `8000` | Port on which the FastAPI application binds and runs. |
| `GEMINI_API_KEY` | Optional | `None` | API key for Google Gemini (triggers fallback when absent). |
| `MODEL_NAME` | Optional | `gemini-2.5-flash` | Model version for advanced NLP ticket triage. |

---

## 6. Setup and Installation

### Local Setup
1. Navigate into the repository directory:
   ```bash
   cd queue_storm
   ```
2. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install production dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Install development dependencies (required for testing):
   ```bash
   pip install -r requirements-dev.txt
   ```

### Running the API
Start the server using `uvicorn`:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```
The API will be available locally at `http://localhost:8000`.

### Running Tests
Execute the comprehensive test suite (which auto-discovers and runs all test suites) using `pytest`:
```bash
pytest -v
```

Our testing suites consist of:
- **`test_app.py`**: Health probe readiness, structural request validations, and validation of the 10 basic sample cases.
- **`test_adversarial_cases.py`**: Handles mixed scripts, Banglish complaints, and adversarial prompt injection payloads.
- **`test_hidden_edge_cases.py`**: Tests timezone/temporal offsets, extreme amount values, safety boundaries, and other edge scenarios.

---

## 7. Docker Deployment & Fallback Rules Acknowledgment
As required by the **SUST Preliminary Round Docker Fallback Rules (Section 8)**, the containerized setup strictly conforms to the following operational parameters:
* **Image Size**: The Docker image built on `python:3.12-slim` is optimized to be **under 500MB** (well below the **1GB hard limit**).
* **GPU & Weights**: No GPUs are required, and no large local model weights are used.
* **Evaluation Network Limits**: The RuleEngine runs locally and offline, ensuring **no multi-GB downloads** or internet connectivity dependencies are required during judging evaluation.
* **Runtime Training**: No runtime model training or state changes are performed.
* **Port Binding**: The container binds and exposes port `8000` via host `0.0.0.0` (FastAPI standard).
* **Health Readiness**: The `/health` endpoint responds instantly (under 1 second) upon container start (well within the **60-second limit**).
* **Secrets Policy**: Secrets (like `GEMINI_API_KEY`) are passed via environment variables at runtime only and are **never baked into the Docker image**.

### Build the Docker image:
```bash
docker build -t queuestorm-team .
```

### Run the container:
```bash
docker run -p 8000:8000 --env-file judging.env queuestorm-team
```
*(Ensure `judging.env` contains any API keys if required; if empty or missing, the API fallback is automatically activated).*

---

## 8. API Endpoints

### GET `/health`
* **Purpose**: Readiness probe.
* **Response**: `{"status": "ok"}`

### POST `/analyze-ticket`
* **Purpose**: Investigates a support ticket against transaction history.
* **Request Body Schema**: Refer to `models.py` or the problem statement.
* **Response Body Schema**: Structured JSON conforming to `AnalyzeTicketResponse`.
* **Validation HTTP Status Code Contracts**:
  - **HTTP 400 (Bad Request)**: Returned when structural constraints are violated (e.g. missing required fields or malformed JSON).
  - **HTTP 422 (Unprocessable Entity)**: Returned for semantic validation constraints (e.g. unrecognized/invalid enum values like `"user_type": "hacker"`).
* **`metadata` Field Design Policy**: The `metadata` field is reserved for future extension. The API accepts it as a flexible optional dictionary (`Optional[Dict[str, Any]]`) to preserve caller compatibility. To ensure GDPR compliance and absolute protection against prompt injections, metadata keys are logged but their content is not forwarded to the LLM prompt boundary.

### Sample Request and Response JSON

#### Sample Request Body (POST `/analyze-ticket`)
```json
{
  "ticket_id": "TKT-10001",
  "complaint": "I paid my bill 1000 BDT but it deducted twice from my account. Please check, I only paid once.",
  "language": "en",
  "channel": "in_app_chat",
  "user_type": "customer",
  "transaction_history": [
    {
      "transaction_id": "TXN-10001",
      "timestamp": "2026-04-14T10:00:00Z",
      "type": "payment",
      "amount": 1000.0,
      "counterparty": "BILLER-DESCO",
      "status": "completed"
    },
    {
      "transaction_id": "TXN-10002",
      "timestamp": "2026-04-14T10:00:12Z",
      "type": "payment",
      "amount": 1000.0,
      "counterparty": "BILLER-DESCO",
      "status": "completed"
    }
  ]
}
```

#### Sample Response Body
```json
{
  "ticket_id": "TKT-10001",
  "relevant_transaction_id": "TXN-10002",
  "evidence_verdict": "consistent",
  "case_type": "duplicate_payment",
  "severity": "high",
  "department": "payments_ops",
  "agent_summary": "Customer reports duplicate payment. Two identical 1000 BDT payments to BILLER-DESCO were completed 12 seconds apart (TXN-10001 and TXN-10002). The second is likely the duplicate.",
  "recommended_next_action": "Verify the duplicate with payments_ops. If the biller confirms only one payment was received, initiate reversal of TXN-10002.",
  "customer_reply": "We have noted the possible duplicate payment for transaction TXN-10002. Our payments team will verify with the biller and any eligible amount will be returned through official channels. Please do not share your PIN or OTP with anyone.",
  "human_review_required": true,
  "confidence": 0.9,
  "reason_codes": [
    "duplicate_payment",
    "biller_verification_required"
  ]
}
```
*(See `sample_output.json` for another full example reference).*

---

## 9. Submission Confirmations
* **No Secrets Committed**: We confirm that no API keys, secrets, or `.env` files containing sensitive configurations have been committed to this repository. All credentials are passed through secure container runtime environment variables.
* **No Real Customer Data**: We confirm that only mock/synthetic customer data is utilized for testing and development within this service.
