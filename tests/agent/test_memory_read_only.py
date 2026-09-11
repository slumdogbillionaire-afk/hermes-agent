import json
from unittest.mock import patch

import pytest

from agent.memory_manager import MemoryManager
from agent.memory_provider import MemoryProvider
from tools.memory_tool import MemoryStore, memory_tool


class RecordingProvider(MemoryProvider):
    name = "recording"

    def __init__(self):
        self.writes = []
        self.recalls = []

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        self.writes.append("initialize")

    def initialize_read_only(self, session_id, **kwargs):
        self.session_id = session_id

    def prefetch(self, query, *, session_id=""):
        self.recalls.append((query, session_id))
        return "Canonical recalled context"

    def system_prompt_block(self):
        return "Canonical identity"

    def get_tool_schemas(self):
        return [{"name": "external_memory", "description": "Memory", "parameters": {"type": "object", "properties": {}}}]

    def sync_turn(self, *args, **kwargs):
        self.writes.append("sync")

    def on_session_end(self, *args, **kwargs):
        self.writes.append("end")

    def queue_prefetch(self, *args, **kwargs):
        self.writes.append("queue")

    def on_turn_start(self, *args, **kwargs):
        self.writes.append("start")

    def on_session_switch(self, *args, **kwargs):
        self.writes.append("switch")

    def on_pre_compress(self, *args, **kwargs):
        self.writes.append("compress")

    def on_memory_write(self, *args, **kwargs):
        self.writes.append("write")

    def on_delegation(self, *args, **kwargs):
        self.writes.append("delegation")

    def shutdown(self):
        self.writes.append("shutdown")


@pytest.mark.parametrize("read_only", [False, True])
def test_lifecycle_retrieves_but_read_only_never_writes(read_only):
    provider = RecordingProvider()
    manager = MemoryManager(read_only=read_only)
    manager.add_provider(provider)
    manager.initialize_all("session")
    assert manager.prefetch_all("remember my project", session_id="session") == "Canonical recalled context"
    assert provider.recalls == [("remember my project", "session")]
    assert manager.build_system_prompt() == "Canonical identity"
    assert bool(manager.get_all_tool_schemas()) is not read_only
    assert manager.has_tool("external_memory") is not read_only
    manager.on_turn_start(1, "query")
    manager.sync_all("query", "final")
    manager.queue_prefetch_all("query")
    assert manager.flush_pending(timeout=5)
    manager.on_pre_compress([])
    manager.on_memory_write("add", "memory", "fact")
    manager.on_delegation("task", "result")
    manager.commit_session_boundary_async([], new_session_id="next")
    assert manager.flush_pending(timeout=5)
    manager.on_session_end([])
    manager.shutdown_all()
    if read_only:
        assert provider.writes == []
        assert manager._sync_executor is None
    else:
        assert provider.writes == ["initialize", "start", "sync", "queue", "compress", "write", "delegation", "end", "switch", "end", "shutdown"]


@pytest.mark.parametrize("target", ["memory", "user"])
def test_store_denies_before_approval_and_preserves_bytes(tmp_path, monkeypatch, target):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    directory = tmp_path / "memories"
    directory.mkdir()
    for name in ("MEMORY.md", "USER.md"):
        (directory / name).write_text("Known fact\n", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    normal = MemoryStore()
    normal.load_from_disk()
    store = MemoryStore(read_only=True)
    store.load_from_disk()
    assert store.format_for_system_prompt(target) == normal.format_for_system_prompt(target)
    with patch("tools.memory_tool._apply_write_gate", side_effect=AssertionError("approval must not run")):
        assert not json.loads(memory_tool(action="add", content="new", store=store))["success"]
    assert not store.add(target, "new")["success"]
    assert not store.replace(target, "Known fact", "new")["success"]
    assert not store.remove(target, "Known fact")["success"]
    assert not store.apply_batch(target, [{"action": "add", "content": "new"}])["success"]
    with pytest.raises(PermissionError):
        store.save_to_disk(target)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


def test_unsupported_provider_cannot_initialize_or_write():
    provider = RecordingProvider()
    provider.initialize_read_only = MemoryProvider.initialize_read_only.__get__(provider)
    manager = MemoryManager(read_only=True)
    manager.add_provider(provider)
    with pytest.raises(ValueError, match="does not support"):
        manager.initialize_all("session")
    manager.shutdown_all()
    assert provider.writes == []


@pytest.mark.parametrize("stored,requested,conflict", [(False, True, True), (True, False, True), (None, True, True), (None, False, False), (True, True, False), (False, False, False)])
def test_real_session_mode_before_agent_setup(tmp_path, stored, requested, conflict):
    from hermes_state import SessionDB
    from run_agent import AIAgent

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("session", source="cli", model_config={} if stored is None else {"memory_read_only": stored})
        if conflict:
            with patch("run_agent.OpenAI", side_effect=AssertionError("provider setup")):
                with pytest.raises(ValueError, match="conflicts"):
                    AIAgent(session_id="session", session_db=db, memory_read_only=requested)
        else:
            db.require_session_memory_mode("session", requested)
    finally:
        db.close()


def test_background_review_denied_even_when_explicit():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent.memory_read_only = True
    with patch("agent.background_review.spawn_background_review_thread", side_effect=AssertionError("review")):
        agent._spawn_background_review([], review_memory=True, focus="explicit")


def test_real_agent_local_provider_retrieval_and_stable_prefix(tmp_path, monkeypatch):
    """Exercise real imports, setup, HTTP dispatch, finalization and shutdown."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    import socket

    from hermes_state import SessionDB
    from run_agent import AIAgent

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path))
    (tmp_path / "SOUL.md").write_text("Canonical synthetic identity", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Synthetic project context", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "memory:\n  provider: recording\n  memory_enabled: true\n  user_profile_enabled: true\n", encoding="utf-8"
    )
    memory = tmp_path / "memories"
    memory.mkdir()
    for name in ("MEMORY.md", "USER.md"):
        (memory / name).write_text("Synthetic durable fact", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in memory.iterdir()}
    requests = []
    connect = socket.socket.connect

    def local_connect(sock, address):
        if address[0] != "127.0.0.1":
            raise OSError("Synthetic canary allows loopback only")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", local_connect)
    monkeypatch.setattr("agent.title_generator._auto_title_enabled", lambda: False)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if requests[-1].get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for delta, finish in [({"role": "assistant", "content": "Synthetic final."}, None), ({}, "stop")]:
                    chunk = {"id": "synthetic", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            body = json.dumps({"id": "synthetic", "object": "chat.completion", "created": 1,
                               "model": "test-model", "choices": [{"index": 0,
                               "message": {"role": "assistant", "content": "Synthetic final."},
                               "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        for read_only in (False, True):
            provider = RecordingProvider()
            with patch("plugins.memory.load_memory_provider", return_value=provider):
                agent = AIAgent(api_key="synthetic", base_url=f"http://127.0.0.1:{server.server_port}/v1",
                                provider="openai-compat", model="test-model", max_iterations=2,
                                enabled_toolsets=[], quiet_mode=True, memory_read_only=read_only,
                                session_db=db, session_id=f"session-{read_only}", platform="cli")
            try:
                result = agent.run_conversation("Recall the synthetic project context")
                assert result["completed"], result.get("final_response")
                assert result["final_response"] == "Synthetic final."
                assert provider.recalls
                assert agent._memory_store.read_only is read_only
                if read_only:
                    assert agent.skip_background_review
                agent.shutdown_memory_provider(result["messages"])
                if read_only:
                    assert provider.writes == []
                else:
                    assert provider.writes.count("sync") == 1
                    assert provider.writes.count("end") == 1
            finally:
                agent.close()
        requests = [request for request in requests if "messages" in request]
        assert len(requests) == 2
        assert requests[0]["messages"][0] == requests[1]["messages"][0]
        assert "Canonical synthetic identity" in str(requests[1]["messages"])
        assert "Synthetic project context" in str(requests[1]["messages"])
        assert "Synthetic durable fact" in str(requests[1]["messages"])
        assert {p.name: p.read_bytes() for p in memory.iterdir()} == before
    finally:
        db.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
