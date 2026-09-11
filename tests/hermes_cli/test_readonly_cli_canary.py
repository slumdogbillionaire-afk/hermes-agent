"""Real public CLI against a synthetic loopback provider; no account access."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def test_public_cli_query_file_receipt(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "messages" not in request:
                self.send_error(404)
                return
            requests.append(request)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in [({"role": "assistant", "content": "Synthetic CLI final."}, None), ({}, "stop")]:
                chunk = {"id": "synthetic", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.yaml").write_text(
        f"model:\n  provider: custom\n  default: test-model\n  base_url: http://127.0.0.1:{server.server_port}/v1\n"
        "  context_length: 256000\nauxiliary:\n  title_generation:\n    enabled: false\n"
        "memory:\n  memory_enabled: true\n  user_profile_enabled: true\n",
        encoding="utf-8",
    )
    query = tmp_path / "query.txt"
    query.write_text("Synthetic query with quotes \" and $(literal)", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    env = {key: value for key, value in os.environ.items() if key.upper() in
           {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA"}}
    env.update(HERMES_HOME=str(home), HOME=str(home), USERPROFILE=str(home),
               PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               HTTP_PROXY="http://127.0.0.1:1", HTTPS_PROXY="http://127.0.0.1:1", NO_PROXY="127.0.0.1")
    argv = [sys.executable, "-B", "-m", "hermes_cli.main", "chat", "--query-file", str(query),
            "--memory-read-only", "--result-file", str(receipt), "--provider", "custom", "--model", "test-model",
            "--toolsets", "search", "--source", "voice", "--run-budget", "90", "--max-turns", "6", "--quiet"]
    try:
        assert query.read_text() not in " ".join(argv)
        result = subprocess.run(argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr[-4000:]
        value = json.loads(receipt.read_text(encoding="utf-8"))
        assert value["status"] == "completed"
        assert value["final_response"] == "Synthetic CLI final."
        assert value["session_id"] in result.stderr
        assert len(requests) == 1
        assert query.read_text() in str(requests[0]["messages"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
