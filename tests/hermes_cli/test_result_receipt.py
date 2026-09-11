import json
from unittest.mock import patch

import pytest

from hermes_cli.result_receipt import build_result_receipt, validate_result_path, write_result_receipt


@pytest.mark.parametrize("result,status", [
    ({"completed": True, "final_response": "Exact final"}, "completed"),
    ({"completed": True, "final_response": "partial", "partial": True}, "failed"),
    ({"completed": True, "final_response": "partial", "compression_deferred": True}, "deferred"),
    ({"completed": True, "final_response": "partial", "interrupted": True}, "cancelled"),
    ({"completed": True, "final_response": "partial", "failed": True}, "failed"),
    ({"completed": True, "final_response": "  "}, "failed"),
    ({"final_response": "unproven"}, "failed"), ({}, "failed"), (None, "failed"),
])
def test_receipt_states(tmp_path, result, status):
    path = tmp_path / "receipt.json"
    write_result_receipt(path, result, "resynced-session")
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["status"] == status
    assert value["session_id"] == "resynced-session"
    assert ("final_response" in value) == (status == "completed")
    assert [p for p in tmp_path.iterdir() if p.is_file()] == [path]


def test_whitelist():
    value = build_result_receipt({
        "completed": True, "final_response": "public final",
        "prompt": "PRIVATE", "last_reasoning": "PRIVATE", "pre_transform_response": "PRIVATE",
        "messages": [{"tool_calls": "PRIVATE"}], "config": "PRIVATE", "api_key": "PRIVATE",
        "turn_exit_reason": "PRIVATE", "estimated_cost_usd": 0.02, "input_tokens": 10,
        "output_tokens": 2, "total_tokens": 12, "api_calls": 1, "cost_source": "provider",
        "cost_status": "estimated", "model": "synthetic", "provider": "local",
    }, "session")
    assert "PRIVATE" not in json.dumps(value)
    assert value["total_tokens"] == 12
    assert value["estimated_cost_usd"] == 0.02
    assert set(value) == {"version", "status", "session_id", "end_reason", "timestamp", "final_response", "estimated_cost_usd", "input_tokens", "output_tokens", "total_tokens", "api_calls", "cost_source", "cost_status", "model", "provider"}


@pytest.mark.parametrize("name", ["relative.json", "//server/share/file", "missing/receipt.json"])
def test_invalid_path(name, tmp_path):
    path = str(tmp_path / name) if name.startswith("missing") else name
    with pytest.raises(ValueError):
        validate_result_path(path)


def test_unwritable_and_atomic_failure(tmp_path):
    path = tmp_path / "receipt.json"
    with patch("hermes_cli.result_receipt.tempfile.mkstemp", side_effect=PermissionError):
        with pytest.raises(ValueError, match="not writable"):
            validate_result_path(str(path))
    with patch("utils.atomic_replace", side_effect=OSError("synthetic replacement failure")):
        with pytest.raises(OSError):
            write_result_receipt(path, {"completed": True, "final_response": "final"}, "s")
    assert not path.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_existing_receipt_refused(tmp_path):
    path = tmp_path / "receipt.json"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(ValueError):
        write_result_receipt(path, {}, "s")
    assert path.read_text() == "old"
