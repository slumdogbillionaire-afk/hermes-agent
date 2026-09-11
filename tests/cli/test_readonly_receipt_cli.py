import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("result,status,code", [
    ({"completed": True, "final_response": "final"}, "completed", 0),
    ({"compression_deferred": True, "final_response": "busy", "partial": True}, "deferred", 0),
    ({"failed": True}, "failed", 1),
    ({"interrupted": True, "final_response": "partial"}, "cancelled", 0),
    ({"final_response": "ambiguous"}, "failed", 0),
])
def test_quiet_cli_receipt_and_legacy_output(tmp_path, monkeypatch, capsys, result, status, code):
    import cli

    calls = []

    class FakeCLI:
        def __init__(self, **kwargs):
            self.provider = "synthetic"
            self.model = "synthetic"
            self.session_id = "parent"
            self.conversation_history = []
            self._active_agent_route_signature = "same"
            self.agent = SimpleNamespace(session_id="child", run_conversation=self.run_conversation)

        def run_conversation(self, **kwargs):
            calls.append(kwargs)
            return result

        def _claim_active_session(self, *args, **kwargs):
            return True

        def _ensure_runtime_credentials(self):
            return True

        def _resolve_turn_agent_config(self, query):
            return {"signature": "same", "model": None, "runtime": None}

        def _init_agent(self, **kwargs):
            return True

    monkeypatch.setattr(cli, "HermesCLI", FakeCLI)
    monkeypatch.setattr(cli.atexit, "register", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "_finalize_single_query", lambda c: None)
    outputs = []
    for receipt in (None, str(tmp_path / "result.json")):
        with pytest.raises(SystemExit) as exc:
            cli.main(query="private query", quiet=True, toolsets="search", result_file=receipt)
        assert exc.value.code == code
        outputs.append(capsys.readouterr())
    assert outputs[0] == outputs[1]
    value = json.loads((tmp_path / "result.json").read_text())
    assert value["status"] == status
    assert value["session_id"] == "child"
    assert "private query" not in json.dumps(value)
    assert len(calls) == 2


def test_invalid_path_before_cli_construction():
    import cli

    with patch.object(cli, "HermesCLI", side_effect=AssertionError("provider setup")):
        with pytest.raises(ValueError, match="absolute"):
            cli.main(query="query", quiet=True, result_file="relative.json")


def test_parser_query_file_has_no_query_in_argv(tmp_path):
    from hermes_cli._parser import build_top_level_parser

    path = tmp_path / "query.txt"
    query = 'Arbitrary " text ` and $(shell)'
    path.write_text(query, encoding="utf-8")
    argv = ["chat", "--query-file", str(path), "--memory-read-only", "--result-file", str(tmp_path / "result.json")]
    parser, _, _ = build_top_level_parser()
    args = parser.parse_args(argv)
    assert args.memory_read_only
    assert args.query is None
    assert query not in " ".join(argv)
    assert path.read_text() == query
    with pytest.raises(SystemExit):
        parser.parse_args(argv + ["--query", "conflict"])
