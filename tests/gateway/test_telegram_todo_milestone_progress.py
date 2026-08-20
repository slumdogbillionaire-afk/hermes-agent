"""Canonical regression checks for honest todo milestone progress."""

import json
from types import SimpleNamespace

import pytest

from gateway.run import _ProgressReplacement, _render_todo_milestone


def test_mixed_todo_statuses_render_completed_only_progress():
    rendered = _render_todo_milestone({"todos": [
        {"id": "hidden-1", "content": "Done", "status": "completed"},
        {"id": "hidden-2", "content": " Implement\n safely ", "status": "in_progress"},
        {"id": "hidden-3", "content": "Later", "status": "pending"},
        {"id": "hidden-4", "content": "Obsolete", "status": "cancelled"},
    ]})
    assert rendered == (
        "Task progress\n"
        "\u2588\u2588\u2591\u2591\u2591\u2591\u2591\u2591\u2591\u2591 1/4 \u00b7 25%\n"
        "Implement safely\n"
        "1 complete \u00b7 1 cancelled \u00b7 1 waiting"
    )
    assert "hidden-" not in rendered


def test_json_and_live_result_objects_are_supported_without_fake_100():
    live = _render_todo_milestone(SimpleNamespace(todos=[
        SimpleNamespace(content="Done", status="completed"),
        SimpleNamespace(content="Cancelled", status="cancelled"),
    ]))
    serialized = _render_todo_milestone(json.dumps({"todos": [
        {"content": "Done", "status": "completed"},
        {"content": "Cancelled", "status": "cancelled"},
    ]}))
    assert live == serialized
    assert "1/2 \u00b7 50%" in live
    assert "100%" not in live


@pytest.mark.parametrize(
    "payload",
    [None, "not json", {}, {"error": "failed"}, {"todos": []},
     {"todos": ["bad"]}, {"todos": [{"content": "x", "status": "unknown"}]}],
)
def test_malformed_results_fail_soft(payload):
    assert _render_todo_milestone(payload) is None


def test_multiple_active_items_and_pathological_content_are_bounded():
    rendered = _render_todo_milestone({"todos": [
        {"content": "first " + "x" * 1000, "status": "in_progress"},
        {"content": "second", "status": "in_progress"},
        {"content": "later", "status": "pending"},
    ]})
    assert "+1 active" in rendered
    assert "\u2026" in rendered
    assert len(rendered) <= 320


def test_typed_replacement_exposes_only_key_and_display_text():
    event = _ProgressReplacement("todo-milestone", "safe display")
    assert event.key == "todo-milestone"
    assert event.text == "safe display"
    assert set(event.__dataclass_fields__) == {"key", "text"}
