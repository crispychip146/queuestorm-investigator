# QueueStorm Investigator

QueueStorm Investigator is a safe, high-performance, and robust AI/API SupportOps copilot built for digital finance ticket investigation. It matches customer complaints with their transaction history, detects security and fraud patterns, and outputs structured case classifications and safe, pre-validated replies.

## Tech Stack
- **FastAPI**: Modern, high-performance web framework for Python APIs.
- **Pydantic**: Data validation and settings management using Python type annotations.
- **Uvicorn**: Lightning-fast ASGI server implementation.
- **google-genai**: Modern Google GenAI SDK for Gemini API integration.
- **Docker**: For containerized deployment.

## AI & Hybrid Architecture
To ensure 100% reproducibility, zero runtime API cost under standard test settings, and robust LLM capabilities when keys are available, the service uses a hybrid architecture:
1. **Gemini API reasoning**: If `GEMINI_API_KEY` is present in the environment, the service calls the Gemini API (defaulting to `gemini-2.5-flash`) utilizing Structured JSON Outputs mapping directly to our Pydantic response models. The prompt treats user complaint text as untrusted content to guard against prompt injections.
2. **From-Scratch RuleEngine (Fallback)**: If `GEMINI_API_KEY` is absent or the API call fails, the service falls back to a deterministic, high-fidelity Python RuleEngine. It parses amounts, dates, counterparties, types, and statuses using regex, matching the 10 sample cases perfectly.
3. **Safety Guardrails (Post-Processor)**: Regardless of whether the LLM or RuleEngine produced the response, it is passed through a deterministic safety processor. It scans `customer_reply` and `recommended_next_action` for PIN, OTP, password, card number requests, refund/unblock promises, and suspicious third-party links.
4. **Nuclear Option**: If any safety violation is detected by the guardrails, the entire `customer_reply` is discarded and replaced with a hardcoded, universally safe reply:
   `"We have received your request regarding ticket [ticket_id]. Any eligible amount will be returned through official channels. Our team will review the case and contact you. Please do not share your PIN or OTP with anyone."`

## MODELS Section
- **Model Used**: `gemini-2.5-flash`
- **Location**: Runs on Google's API cloud via `google-genai` SDK.
- **Why Chosen**: Optimized for speed, low latency, structured JSON outputs, and high reasoning capability, making it ideal for real-time ticket triage.

## Setup and Installation

### Local Setup
1. Clone the repository and navigate to the project directory:
   ```bash
   cd sust_hackathon_final_1
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
The API will be available at `http://localhost:8000`.

### Running Tests
Execute the comprehensive test suite (which auto-discovers and runs all test suites) using `pytest`:
```bash
pytest -v
```

Our testing suites consist of:
- **`test_app.py`**: Health probe readiness, structural request validations, and validation of the 10 basic sample cases.
- **`test_adversarial_cases.py`**: Handles mixed scripts, Banglish complaints, and adversarial prompt injection payloads.
- **`test_hidden_edge_cases.py`**: Tests timezone/temporal offsets, extreme amount values, safety boundaries, and other edge scenarios.

## Docker Deployment
1. Build the Docker image:
   ```bash
   docker build -t hackathon-team .
   ```
2. Run the container:
   ```bash
   docker run -p 8000:8000 --env-file judging.env hackathon-team
   ```
   *(Ensure `judging.env` contains any API keys if required; if empty or missing, the API fallback is automatically activated and runs 100% locally and safely).*

## API Endpoints

### 1. GET `/health`
- **Purpose**: Readiness probe.
- **Response**:
  ```json
  {"status": "ok"}
  ```

### 2. POST `/analyze-ticket`
- **Purpose**: Investigates a support ticket against transaction history.
- **Request Body Schema**: Refer to `models.py` or the problem statement.
- **Response Body Schema**: Structured JSON conforming to `AnalyzeTicketResponse`.
- **Validation HTTP Status Code Contracts**:
  - **HTTP 400 (Bad Request)**: Returned when structural constraints are violated (e.g., missing required fields like `ticket_id`/`complaint` or malformed/invalid JSON).
  - **HTTP 422 (Unprocessable Entity)**: Returned for semantic validation constraints (e.g., unrecognized/invalid enum values like `"user_type": "hacker"` or field constraint violations).
- **`metadata` Field Design Policy**: The `metadata` field is reserved for future extension. The API accepts it as a flexible optional dictionary (`Optional[Dict[str, Any]]`) to preserve caller compatibility. To ensure GDPR compliance and absolute protection against prompt injections, metadata keys are logged but their content is not forwarded to the LLM prompt boundary.
