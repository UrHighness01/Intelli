import json
import re
import time
from jsonschema import validate, ValidationError
from pathlib import Path
from typing import Any, Dict, Literal

try:
    from tools.capability import CapabilityVerifier, ToolManifest
    _has_cap = True
except Exception:
    CapabilityVerifier = None  # type: ignore
    ToolManifest = None        # type: ignore
    _has_cap = False


# ---------------------------------------------------------------------------
# Risk scoring
# ---------------------------------------------------------------------------

RiskLevel = Literal['low', 'medium', 'high']

# Tools that always require human approval regardless of args
_HIGH_RISK_TOOLS = {
    'system.exec', 'system.update', 'system.kill',
    'file.write', 'file.delete', 'file.chmod',
    'network.request', 'network.proxy',
}
# Tools considered medium-risk by default (read-only but potentially sensitive)
_MEDIUM_RISK_TOOLS = {
    'file.read', 'file.list', 'system.env',
    'clipboard.read', 'browser.cookies',
}

# Patterns in arg *values* that raise risk
_SENSITIVE_ARG_PATTERNS = re.compile(
    r'\.\.[\\/]|/etc/|/proc/|/sys/|cmd\.exe|powershell|eval\(|exec\(|'
    r'drop\s+table|delete\s+from|format\s+c|rm\s+-rf',
    re.I,
)

# Suspicious arg key names (beyond SENSITIVE_KEYS)
_RISKY_ARG_KEYS = re.compile(r'command|cmd|exec|shell|script|query|sql|path|file|url', re.I)


def _score_args(args: Dict[str, Any]) -> int:
    """Return an integer risk contribution from the call's arguments (0–3)."""
    score = 0
    for key, val in args.items():
        val_str = str(val)
        if _SENSITIVE_ARG_PATTERNS.search(val_str):
            score += 2  # traversal / injection patterns
        if _RISKY_ARG_KEYS.search(key):
            score += 1  # suspicious parameter names
        if isinstance(val, str) and len(val) > 512:
            score += 1  # unusually large string payloads
    return score


def compute_risk(payload: Dict[str, Any]) -> RiskLevel:
    """Compute a risk level for a tool-call payload.

    Returns 'high', 'medium', or 'low' based on:
      - the tool name (high/medium risk lists),
      - suspicious arg keys and values (path traversal, injection patterns),
      - overall arg size.

    Decision table
    --------------
      tool in HIGH_RISK_TOOLS   → high (regardless of args)
      arg_score >= 2            → high
      tool in MEDIUM_RISK_TOOLS → medium
      arg_score >= 1            → medium
      otherwise                 → low
    """
    tool = payload.get('tool', '')
    args = payload.get('args', {})
    if not isinstance(args, dict):
        args = {}

    if tool in _HIGH_RISK_TOOLS:
        return 'high'

    arg_score = _score_args(args)

    if arg_score >= 2:
        return 'high'
    if tool in _MEDIUM_RISK_TOOLS or arg_score >= 1:
        return 'medium'
    return 'low'


class ApprovalQueue:
    def __init__(self):
        self._store = {}
        self._next = 1

    def submit(self, payload: Dict[str, Any], risk: RiskLevel = 'high') -> int:
        id_ = self._next
        self._store[id_] = {
            "payload": payload,
            "status": "pending",
            "risk": risk,
            "enqueued_at": time.time(),
        }
        self._next += 1
        return id_

    def expire_pending(self, timeout_secs: float) -> list:
        """Auto-reject pending items older than *timeout_secs*; return their ids.

        Returns an empty list if *timeout_secs* is 0 or negative (disabled).
        """
        if timeout_secs <= 0:
            return []
        now = time.time()
        expired = []
        for id_, item in list(self._store.items()):
            if item["status"] == "pending":
                age = now - item.get("enqueued_at", now)
                if age >= timeout_secs:
                    item["status"] = "rejected"
                    expired.append(id_)
        return expired

    def approve(self, id_: int):
        if id_ in self._store:
            self._store[id_]["status"] = "approved"
            return True
        return False

    def reject(self, id_: int):
        if id_ in self._store:
            self._store[id_]["status"] = "rejected"
            return True
        return False

    def status(self, id_: int):
        return self._store.get(id_)

    def list_pending(self):
        return {k: v for k, v in self._store.items() if v["status"] == "pending"}


class Supervisor:
    SENSITIVE_KEYS = re.compile(r"password|secret|token|api_key|cvv|card|ssn|credentials", re.I)

    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
        self.queue = ApprovalQueue()
        # Kept for backward-compat; actual risk logic lives in compute_risk()
        self.high_risk_tools = _HIGH_RISK_TOOLS
        # tool schema registry path
        self.schema_dir = Path(__file__).with_name("schemas")
        # capability verifier — checks declared tool caps against allow-list
        self._cap_verifier = CapabilityVerifier() if _has_cap else None

    def _validate_schema(self, payload: Dict[str, Any]):
        try:
            validate(instance=payload, schema=self.schema)
        except ValidationError as e:
            raise e

    def _make_validation_error(self, exc: ValidationError, payload: Dict[str, Any], phase: str = "schema") -> Dict[str, Any]:
        """Create a deterministic error token and structured feedback for validation errors."""
        import hashlib, json

        # canonical payload string
        try:
            canon = json.dumps(payload, sort_keys=True)
        except Exception:
            canon = str(payload)

        token_source = f"{phase}:{exc.message}:{canon}"
        token = hashlib.sha256(token_source.encode('utf-8')).hexdigest()[:12]

        feedback = {
            'error_code': f'{phase}_validation_error',
            'message': exc.message,
            'path': list(exc.path) if hasattr(exc, 'path') else [],
            'token': token,
        }
        return {'status': 'validation_error', 'error_token': token, 'feedback': feedback}

    def _sanitize(self, obj: Any):
        if isinstance(obj, dict):
            sanitized = {}
            for k, v in obj.items():
                if self.SENSITIVE_KEYS.search(k):
                    sanitized[k] = "[REDACTED]"
                else:
                    sanitized[k] = self._sanitize(v)
            return sanitized
        if isinstance(obj, list):
            return [self._sanitize(i) for i in obj]
        return obj

    def _load_tool_schema(self, tool_name: str):
        # map tool id to filename: replace dots with path separators
        filename = tool_name.replace('.', '/') + '.json'
        candidate = self.schema_dir.joinpath(filename)
        if candidate.exists():
            return load_tool_schema(candidate)
        # also try direct filename under schema_dir (tool names with slashes not used)
        candidate2 = self.schema_dir.joinpath(tool_name + '.json')
        if candidate2.exists():
            return load_tool_schema(candidate2)
        return None

    def approval_required(self, payload: Dict[str, Any]) -> bool:
        """Return True when the tool call must be held for human approval.

        Decision order:
        1. If a capability manifest exists for the tool, its ``requires_approval``
           field is authoritative (True → always approve, False → always skip).
        2. If no manifest exists, fall back to the heuristic risk score: only
           ``'high'`` risk triggers the approval queue.
        """
        tool = payload.get('tool', '')
        if ToolManifest is not None and tool:
            manifest = ToolManifest.load(tool)
            if manifest is not None:
                return manifest.requires_approval
        return compute_risk(payload) == 'high'

    def process_call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # Validate schema
        try:
            self._validate_schema(payload)
        except ValidationError as e:
            return self._make_validation_error(e, payload, phase="schema")

        # If there's a per-tool schema, validate args against it
        tool = payload.get("tool")
        if tool:
            tschema = self._load_tool_schema(tool)
            if tschema is not None:
                try:
                    validate(instance=payload.get("args", {}), schema=tschema)
                except ValidationError as e:
                    return self._make_validation_error(e, payload.get('args', {}), phase="tool_args")

        # Capability check — reject if tool requires capabilities not in allow-list
        if self._cap_verifier and tool:
            allowed, denied = self._cap_verifier.check(tool, payload.get('args', {}))
            if not allowed:
                return {
                    'status': 'capability_denied',
                    'tool': tool,
                    'denied_capabilities': denied,
                    'message': (
                        'This tool requires capabilities that are not permitted in '
                        'this deployment. Set AGENT_GATEWAY_ALLOWED_CAPS to grant access.'
                    ),
                }

        # Sanitize sensitive values
        sanitized = {"tool": payload.get("tool"), "args": self._sanitize(payload.get("args", {}))}

        # Risk assessment (used for labelling and fallback routing)
        risk: RiskLevel = compute_risk(payload)

        # Manifest-driven approval routing (overrides heuristic risk score)
        manifest = ToolManifest.load(tool) if (ToolManifest is not None and tool) else None
        if manifest is not None:
            if manifest.requires_approval:
                # Manifest explicitly requires human sign-off
                req_id = self.queue.submit(sanitized, risk=risk)
                return {"status": "pending_approval", "id": req_id, "risk": risk}
            else:
                # Manifest explicitly opts out of the approval queue
                return {
                    "tool": sanitized["tool"],
                    "args": sanitized["args"],
                    "status": "accepted",
                    "risk": risk,
                    "message": "validated and sanitized (supervisor; manifest auto-approved)",
                }

        # No manifest — fall back to heuristic risk score
        if risk == 'high':
            req_id = self.queue.submit(sanitized, risk=risk)
            return {"status": "pending_approval", "id": req_id, "risk": risk}

        # medium or low — accept immediately, include risk level in response
        return {
            "tool": sanitized["tool"],
            "args": sanitized["args"],
            "status": "accepted",
            "risk": risk,
            "message": "validated and sanitized (supervisor)",
        }


def load_schema_from_file(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_tool_schema(path: Path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None



if __name__ == "__main__":
    # simple smoke
    schema = load_schema_from_file(Path(__file__).with_name("tool_schema.json"))
    sup = Supervisor(schema)
    print(sup.process_call({"tool": "echo", "args": {"text": "hi", "password": "secret"}}))
