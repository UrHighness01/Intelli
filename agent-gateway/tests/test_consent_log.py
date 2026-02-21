"""Tests for agent-gateway/consent_log.py.

Covers:
  - log_context_share – entry structure, field-name-only logging
  - get_timeline – filtering by origin / actor, limit, newest-first ordering
  - clear_timeline – clear all / clear by origin
"""
import os
import sys
import time
import pytest
from pathlib import Path
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


# ---------------------------------------------------------------------------
# Fixture: redirect consent file to a temp file
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tmp_consent_path(monkeypatch, tmp_path):
    consent_file = tmp_path / 'consent_timeline_test.jsonl'
    monkeypatch.setenv('AGENT_GATEWAY_CONSENT_PATH', str(consent_file))
    # Reload module so it picks up the new path
    import importlib, consent_log
    importlib.reload(consent_log)
    yield consent_log
    # Cleanup handled by tmp_path fixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SNAPSHOT_BASIC = {
    'url': 'https://example.com/login',
    'title': 'Login Page',
    'selected_text': 'Hello',
    'inputs': [
        {'name': 'username', 'value': 'alice'},
        {'name': 'password', 'value': 'secret'},
    ],
}

SNAPSHOT_EMPTY = {
    'url': 'https://other.com/',
    'title': 'Other',
    'selected_text': '',
    'inputs': [],
}


# ---------------------------------------------------------------------------
# log_context_share
# ---------------------------------------------------------------------------

class TestLogContextShare:
    def test_returns_entry_dict(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://example.com/login',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
            actor='tok123...',
        )
        assert isinstance(entry, dict)
        assert entry['url'] == 'https://example.com/login'
        assert entry['origin'] == 'https://example.com'
        assert entry['actor'] == 'tok123...'

    def test_logs_field_names_not_values(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://example.com/login',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
        )
        assert 'username' in entry['fields']
        assert 'password' in entry['fields']
        # Values must NOT appear
        assert 'alice' not in str(entry)
        assert 'secret' not in str(entry)

    def test_selected_text_len_recorded(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://example.com/',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
        )
        assert entry['selected_text_len'] == len('Hello')

    def test_selected_text_len_zero_when_absent(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://other.com/',
            origin='https://other.com',
            snapshot=SNAPSHOT_EMPTY,
        )
        assert entry['selected_text_len'] == 0

    def test_anonymous_actor_when_none(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://example.com/',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
            actor=None,
        )
        assert entry['actor'] == 'anonymous'

    def test_redacted_fields_recorded(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://example.com/',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
            redacted_fields=['password'],
        )
        assert 'password' in entry['redacted']

    def test_entry_written_to_disk(self, tmp_consent_path, tmp_path):
        tmp_consent_path.log_context_share(
            url='https://example.com/',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
        )
        p = Path(tmp_consent_path.CONSENT_TIMELINE_PATH)
        assert p.exists()
        assert p.stat().st_size > 0

    def test_ts_is_iso_string(self, tmp_consent_path):
        entry = tmp_consent_path.log_context_share(
            url='https://example.com/',
            origin='https://example.com',
            snapshot=SNAPSHOT_BASIC,
        )
        ts = entry['ts']
        assert isinstance(ts, str)
        assert 'T' in ts or 'Z' in ts or '+' in ts


# ---------------------------------------------------------------------------
# get_timeline
# ---------------------------------------------------------------------------

class TestGetTimeline:
    def _seed(self, mod, n=3):
        for i in range(n):
            mod.log_context_share(
                url=f'https://example.com/page{i}',
                origin='https://example.com',
                snapshot=SNAPSHOT_BASIC,
                actor='actor1',
            )
        mod.log_context_share(
            url='https://other.com/',
            origin='https://other.com',
            snapshot=SNAPSHOT_EMPTY,
            actor='actor2',
        )

    def test_returns_all_entries(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        entries = tmp_consent_path.get_timeline()
        assert len(entries) == 4

    def test_newest_first_ordering(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        entries = tmp_consent_path.get_timeline()
        ts_list = [e['ts'] for e in entries]
        assert ts_list == sorted(ts_list, reverse=True)

    def test_filter_by_origin(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        entries = tmp_consent_path.get_timeline(origin='https://other.com')
        assert len(entries) == 1
        assert entries[0]['origin'] == 'https://other.com'

    def test_filter_by_actor(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        entries = tmp_consent_path.get_timeline(actor='actor2')
        assert len(entries) == 1
        assert entries[0]['actor'] == 'actor2'

    def test_limit(self, tmp_consent_path):
        self._seed(tmp_consent_path, n=5)
        entries = tmp_consent_path.get_timeline(limit=2)
        assert len(entries) == 2

    def test_empty_when_no_entries(self, tmp_consent_path):
        entries = tmp_consent_path.get_timeline()
        assert entries == []


# ---------------------------------------------------------------------------
# clear_timeline
# ---------------------------------------------------------------------------

class TestClearTimeline:
    def _seed(self, mod):
        mod.log_context_share('https://a.com/', 'https://a.com', SNAPSHOT_BASIC)
        mod.log_context_share('https://b.com/', 'https://b.com', SNAPSHOT_EMPTY)

    def test_clear_all(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        tmp_consent_path.clear_timeline()
        assert tmp_consent_path.get_timeline() == []

    def test_clear_by_origin(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        tmp_consent_path.clear_timeline(origin='https://a.com')
        entries = tmp_consent_path.get_timeline()
        assert all(e['origin'] != 'https://a.com' for e in entries)
        assert len(entries) == 1

    def test_clear_nonexistent_origin_is_noop(self, tmp_consent_path):
        self._seed(tmp_consent_path)
        tmp_consent_path.clear_timeline(origin='https://nonexistent.io')
        assert len(tmp_consent_path.get_timeline()) == 2
