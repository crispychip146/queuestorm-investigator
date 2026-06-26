import asyncio
import logging
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from models import AnalyzeTicketRequest, AnalyzeTicketResponse
from rules import analyze_ticket_rules, run_safety_guardrails
from llm import analyze_ticket_llm

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="QueueStorm Investigator",
    description="A safe, high-performance, and robust AI/API SupportOps copilot built for digital finance ticket investigation.",
    version="1.0.0"
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    errors = exc.errors()
    # If any error is a missing field, or if JSON is invalid, return 400
    is_missing = any(err.get("type") == "missing" for err in errors)
    is_json_invalid = any("json" in err.get("type", "") for err in errors)

    status_code = 400 if (is_missing or is_json_invalid) else 422
    return JSONResponse(
        status_code=status_code,
        content={"detail": "Schema validation failed", "errors": errors}
    )

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    # Enforce non-sensitive error messages on internal errors, no stack traces
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred."}
    )

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/analyze-ticket", response_model=AnalyzeTicketResponse)
async def analyze_ticket(request: AnalyzeTicketRequest):
    # Semantic validation
    if not request.complaint.strip():
        raise HTTPException(status_code=422, detail="Complaint cannot be empty")
    if not request.ticket_id.strip():
        raise HTTPException(status_code=422, detail="Ticket ID cannot be empty")
    if len(request.ticket_id) > 100:
        raise HTTPException(status_code=422, detail="Ticket ID is too long (max 100 characters)")

    resp = None
    # 1. Try LLM first with 4 seconds strict timeout
    try:
        resp = await asyncio.wait_for(
            asyncio.to_thread(analyze_ticket_llm, request),
            timeout=4.0
        )
    except asyncio.TimeoutError:
        logger.warning("LLM analysis timed out after 4.0s; falling back to RuleEngine")
    except Exception as e:
        logger.exception("LLM analysis failed; falling back to RuleEngine: %s", e)

    # 2. Fallback to rules if LLM failed/absent
    if not resp:
        resp = analyze_ticket_rules(request)

    # 3. Always apply safety guardrails and enforce request ticket_id
    resp = run_safety_guardrails(resp, request.ticket_id)

    return resp
