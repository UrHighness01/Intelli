"""Tests for the supervisor risk scorer and updated process_call behaviour."""
import pytest
from supervisor import compute_risk, Supervisor, load_schema_from_file
from pathlib import Path


SCHEMA = load_schema_from_file(Path(__file__).parent.parent / 'tool_schema.json')


# ── compute_risk ───────────────────────────────────────────────────────────

class TestComputeRisk:
    def test_safe_tool_no_suspicious_args_is_low(self):
        assert compute_risk({'tool': 'echo', 'args': {'text': 'hello'}}) == 'low'

    def test_noop_tool_is_low(self):
        assert compute_risk({'tool': 'noop', 'args': {}}) == 'low'

    def test_high_risk_tool_always_high(self):
        assert compute_risk({'tool': 'system.exec', 'args': {}}) == 'high'
        assert compute_risk({'tool': 'file.write', 'args': {}}) == 'high'
        assert compute_risk({'tool': 'file.delete', 'args': {}}) == 'high'
        assert compute_risk({'tool': 'network.request', 'args': {}}) == 'high'

    def test_medium_risk_tool_is_medium(self):
        assert compute_risk({'tool': 'file.read', 'args': {}}) == 'medium'
        assert compute_risk({'tool': 'clipboard.read', 'args': {}}) == 'medium'

    def test_path_traversal_in_arg_value_raises_to_high(self):
        assert compute_risk({'tool': 'echo', 'args': {'path': '../../etc/passwd'}}) == 'high'

    def test_proc_path_in_arg_value_raises_to_high(self):
        assert compute_risk({'tool': 'echo', 'args': {'src': '/proc/self/mem'}}) == 'high'

    def test_suspicious_arg_key_raises_to_medium(self):
        risk = compute_risk({'tool': 'echo', 'args': {'command': 'ls'}})
        assert risk in ('medium', 'high')

    def test_sql_injection_pattern_raises_to_high(self):
        risk = compute_risk({'tool': 'echo', 'args': {'q': "'; DROP TABLE users; --"}})
        assert risk == 'high'

    def test_large_arg_value_raises_to_medium_at_least(self):
        risk = compute_risk({'tool': 'echo', 'args': {'data': 'x' * 600}})
        assert risk in ('medium', 'high')

    def test_non_dict_args_treated_safely(self):
        # args that are not a dict should not crash
        risk = compute_risk({'tool': 'echo', 'args': None})
        assert risk in ('low', 'medium', 'high')


# ── Supervisor.process_call with risk levels ───────────────────────────────

class TestSupervisorRisk:
    @pytest.fixture
    def sup(self):
        return Supervisor(SCHEMA)

    def test_low_risk_call_accepted(self, sup):
        result = sup.process_call({'tool': 'echo', 'args': {'text': 'hi'}})
        assert result['status'] == 'accepted'
        assert result['risk'] == 'low'

    def test_high_risk_tool_queued_for_approval(self, sup):
        result = sup.process_call({'tool': 'file.write', 'args': {'path': '/tmp/x', 'content': 'y'}})
        assert result['status'] == 'pending_approval'
        assert result['risk'] == 'high'
        assert 'id' in result

    def test_path_traversal_queued(self, sup):
        # Use a tool with no capability manifest so the heuristic applies.
        # 'echo' now has a manifest with requires_approval=false which would
        # override the high heuristic score and accept the call instead.
        # 'custom.nomanifest' has no manifest, so arg-pattern scoring decides.
        result = sup.process_call({'tool': 'custom.nomanifest', 'args': {'path': '../../etc/passwd'}})
        assert result['status'] == 'pending_approval'

    def test_approval_queue_item_has_risk_field(self, sup):
        sup.process_call({'tool': 'system.exec', 'args': {}})
        pending = sup.queue.list_pending()
        assert any(v.get('risk') == 'high' for v in pending.values())

    def test_medium_risk_accepted_immediately(self, sup):
        result = sup.process_call({'tool': 'file.read', 'args': {'path': '/tmp/safe.txt'}})
        assert result['status'] == 'accepted'
        assert result['risk'] == 'medium'

    def test_sensitive_key_still_redacted_in_accepted_call(self, sup):
        result = sup.process_call({'tool': 'echo', 'args': {'text': 'hi', 'token': 'secret'}})
        assert result['args']['token'] == '[REDACTED]'

    def test_validation_error_returned_on_invalid_schema(self, sup):
        result = sup.process_call({'not_a_tool_key': 'bad'})
        assert result['status'] == 'validation_error'
        assert 'error_token' in result
