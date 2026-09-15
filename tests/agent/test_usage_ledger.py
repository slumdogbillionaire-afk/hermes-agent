"""Behavior tests for the advisory, secret-free model-usage ledger."""

import copy
import json

import pytest

from agent import usage_ledger
from hermes_state import SessionDB


@pytest.fixture()
def ledger_home(tmp_path, monkeypatch):
    monkeypatch.setattr(usage_ledger, "get_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(usage_ledger, "get_default_hermes_root", lambda: tmp_path)
    monkeypatch.setattr(usage_ledger, "_enabled", lambda _home: True)
    usage_ledger._config_cache.clear()
    yield tmp_path
    usage_ledger._context.set(None)
    usage_ledger._config_cache.clear()


def _rows(home):
    path = home / usage_ledger.LEDGER_RELATIVE_PATH
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_per_call_components_are_appended_exactly_once(ledger_home, tmp_path):
    token = usage_ledger.set_usage_context(
        surface="telegram", logical_run_id="turn-1", max_iterations=48
    )
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.queue_token_counts(
            "telegram-session",
            input_tokens=101,
            output_tokens=23,
            cache_read_tokens=900,
            cache_write_tokens=17,
            reasoning_tokens=11,
            model="model-v1",
            billing_provider="provider-v1",
            cost_status="priced",
            api_call_count=1,
        )
        assert db.flush_token_counts()
    finally:
        db.close()
        usage_ledger.reset_usage_context(token)

    rows = _rows(ledger_home)
    assert len(rows) == 1
    assert rows[0]["usage"] == {
        "fresh_input": 101,
        "output": 23,
        "cache_read": 900,
        "cache_write": 17,
        "reasoning": 11,
    }
    assert rows[0]["api_call_count"] == 1
    assert rows[0]["surface"] == "telegram"
    assert rows[0]["budget"] == {"max_iterations": 48}


def test_coalescing_keeps_one_record_per_original_call(ledger_home, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.queue_token_counts("session", input_tokens=3, api_call_count=1)
        db.queue_token_counts("session", input_tokens=5, api_call_count=1)
        assert db.flush_token_counts()
    finally:
        db.close()

    assert [row["usage"]["fresh_input"] for row in _rows(ledger_home)] == [3, 5]


def test_cumulative_absolute_update_is_not_readded(ledger_home, tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.queue_token_counts("session", input_tokens=7, api_call_count=1)
        assert db.flush_token_counts()
        db.update_token_counts(
            "session", input_tokens=700, output_tokens=20,
            api_call_count=4, absolute=True,
        )
    finally:
        db.close()

    rows = _rows(ledger_home)
    assert len(rows) == 1
    assert rows[0]["usage"]["fresh_input"] == 7


def test_content_shaped_fields_are_omitted_and_labels_fail_closed(ledger_home):
    token = usage_ledger.set_usage_context(
        surface="api_server", logical_run_id="private raw id", max_iterations=8
    )
    try:
        assert usage_ledger.record_accounting_delta(
            "credential-derived-session",
            {
                "input_tokens": 1,
                "api_call_count": 1,
                "model": "https://secret.example/path?key=value",
                "billing_provider": "Authorization: Bearer secret-value",
                "prompt": "private prompt text",
                "messages": [{"content": "private body"}],
                "headers": {"X-Token": "secret-value"},
                "url": "https://secret.example/tool",
                "tool_arguments": {"password": "secret-value"},
                "tool_result": "private tool output",
            },
        )
    finally:
        usage_ledger.reset_usage_context(token)

    row = _rows(ledger_home)[0]
    assert set(row) == {
        "version", "record_id", "recorded_at", "surface", "task", "provider",
        "model", "logical_run_id", "session_id", "status", "cost_status",
        "api_call_count", "usage", "budget",
    }
    encoded = json.dumps(row, sort_keys=True)
    for forbidden in (
        "private prompt", "private body", "secret-value", "secret.example",
        "password", "tool_arguments", "tool_result", "credential-derived-session",
    ):
        assert forbidden not in encoded
    assert row["provider"] == "unknown"
    assert row["model"] == "unknown"
    assert row["session_id"].startswith("sha256:")
    assert row["logical_run_id"].startswith("sha256:")


@pytest.mark.parametrize("status", ["failed", "prevented"])
def test_content_free_nonaccepted_run_status_is_supported(ledger_home, status):
    assert usage_ledger.record_run_status(
        "session", model="model-a", provider="provider-a",
        task="logical_run", status=status,
    )

    row = _rows(ledger_home)[0]
    assert row["status"] == status
    assert row["api_call_count"] == 0
    assert all(value == 0 for value in row["usage"].values())


def test_telemetry_failure_cannot_block_canonical_accounting(
    ledger_home, tmp_path, monkeypatch
):
    def broken_telemetry(*_args, **_kwargs):
        raise ValueError("malformed telemetry state")

    monkeypatch.setattr(usage_ledger, "record_accounting_delta", broken_telemetry)
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.queue_token_counts("owner-session", input_tokens=13, api_call_count=1)
        assert db.flush_token_counts()
        session = db.get_session("owner-session")
    finally:
        db.close()

    assert session["input_tokens"] == 13
    assert session["api_call_count"] == 1
    assert _rows(ledger_home) == []


def test_five_ordinary_turns_preserve_static_prompt_cache_prefix():
    from agent.prompt_caching import apply_anthropic_cache_control

    stable = "stable owner instructions"
    history = [{"role": "system", "content": stable + "\n\nsession context"}]
    observed_prefixes = []

    for turn in range(5):
        history.append({"role": "user", "content": f"ordinary request {turn}"})
        wire = apply_anthropic_cache_control(
            copy.deepcopy(history),
            static_system_prefix=stable,
            native_anthropic=True,
        )
        observed_prefixes.append(wire[0]["content"][0])
        history.append({"role": "assistant", "content": f"ordinary reply {turn}"})

    assert observed_prefixes == [
        {
            "type": "text",
            "text": stable,
            "cache_control": {"type": "ephemeral"},
        }
    ] * 5
