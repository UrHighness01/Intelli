from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import json
from jsonschema import validate, ValidationError
from pathlib import Path
from supervisor import Supervisor, load_schema_from_file

app = FastAPI(title="Intelli Agent Gateway (prototype)")

SCHEMA_PATH = Path(__file__).with_name("tool_schema.json")
TOOL_SCHEMA = load_schema_from_file(SCHEMA_PATH)
supervisor = Supervisor(TOOL_SCHEMA)


class ToolCall(BaseModel):
    tool: str
    args: dict


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/validate")
def validate_payload(payload: dict):
    try:
        validate(instance=payload, schema=TOOL_SCHEMA)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"schema validation failed: {e.message}")
    return {"valid": True}


@app.post("/tools/call")
def tool_call(call: ToolCall):
    # Defensive payload extraction (Pydantic v2 compatibility)
    payload = call.model_dump() if hasattr(call, "model_dump") else call.dict()

    result = supervisor.process_call(payload)
    # If schema error, return 400
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    if result.get("status") == "pending_approval":
        # HTTP 202 Accepted
        return {"status": "pending_approval", "id": result.get("id")}

    # Stubbed execution for accepted calls
    return {"tool": result.get("tool"), "args": result.get("args"), "status": "stubbed", "message": result.get("message")}


@app.get("/approvals")
def list_approvals():
    pending = supervisor.queue.list_pending()
    return {"pending": pending}


@app.get("/approvals/{req_id}")
def get_approval(req_id: int):
    status = supervisor.queue.status(req_id)
    if not status:
        raise HTTPException(status_code=404, detail="request not found")
    return status


@app.post("/approvals/{req_id}/approve")
def approve(req_id: int):
    ok = supervisor.queue.approve(req_id)
    if not ok:
        raise HTTPException(status_code=404, detail="request not found")
    return {"status": "approved", "id": req_id}


@app.post("/approvals/{req_id}/reject")
def reject(req_id: int):
    ok = supervisor.queue.reject(req_id)
    if not ok:
        raise HTTPException(status_code=404, detail="request not found")
    return {"status": "rejected", "id": req_id}
