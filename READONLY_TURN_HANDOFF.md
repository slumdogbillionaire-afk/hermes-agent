# Read-only turn seam checkpoint — 2026-09-11

Base: `f194fdc437941cf3fa2fa7c533c27a15ebf8c4e7`, branch
`release/voice-readonly-seam-20260911`. Sole author; uncommitted candidate.

## Contract

`hermes chat --query-file PATH --memory-read-only --result-file ABSOLUTE_PATH`
uses the existing single-query pipeline. Read-only mode preserves context,
identity, built-in memory snapshots and synchronous provider recall. It gates
MemoryStore mutations before approval staging, all MemoryManager write and
background paths, external tool schemas, and background review. Delegates inherit
the restriction. The mode is persisted in creation-time model_config, including
named sessions. Legacy sessions are normal; either resume mismatch is an error.

Provider initialization is itself potentially mutating. Providers must implement
`initialize_read_only` to prepare retrieval without resource creation, durable
caches or background writers. Its base implementation fails closed. Ordinary
initialize/shutdown are never invoked in read-only mode. **Existing external
providers without this implementation cannot serve a read-only turn.** No
provider capability metadata or voice-specific core behavior was introduced.

Receipt targets must be new absolute local files in existing writable directories,
without symlinks/junctions, UNC/mapped network drives or Windows special paths.
The caller must control that directory throughout execution. Receipts use
`utils.atomic_json_write` once, after CLI session resynchronization/finalization.
Only an explicit complete, nonpartial, nonempty final is completed. Deferred,
interrupted, failed and unknown states cannot emit final text. The whitelist
contains version/status/session_id/end_reason/timestamp and the specified existing
cost/token/model/provider/API-call fields. No raw result dictionary is serialized.
No flags preserves output and exit behavior; deferred legacy exit remains zero.

## Exact changed paths

- agent/agent_init.py
- agent/memory_manager.py
- agent/memory_provider.py
- cli.py
- hermes_cli/_parser.py
- hermes_cli/cli_agent_setup_mixin.py
- hermes_cli/main.py
- hermes_cli/result_receipt.py
- hermes_state.py
- run_agent.py
- tools/delegate_tool.py
- tools/memory_tool.py
- tests/agent/test_memory_read_only.py
- tests/cli/test_readonly_receipt_cli.py
- tests/hermes_cli/test_readonly_cli_canary.py
- tests/hermes_cli/test_result_receipt.py
- tests/hermes_cli/test_oneshot_surrogate.py
- READONLY_TURN_HANDOFF.md

The existing surrogate test now accepts native Windows CRLF while retaining its
Unicode replacement assertions. No production stdout behavior was changed.

## Checkpoint verification

- Canonical `scripts/run_tests.sh`: **275 passed, 0 failed, 34 files**.
- New contract tests included: **36 passed** across four new test files.
- Ruff with `pyproject.toml`: **17 exact changed Python paths passed**.
- Compilation: **17 changed Python paths passed**.
- Isolated real imports: **6 passed**.
- Real public CLI synthetic canary: **1 passed**, included above. It invokes
  `python -m hermes_cli.main chat` with query-file, read-only and result-file,
  isolated HERMES_HOME and a local HTTP/SSE provider; no real credential.
- Real AIAgent local-provider comparison: normal sync/end each once; read-only
  zero writes; identical system prompt, context and memory injection retained.
- Atomic replacement failure leaves no partial receipt. Receipt state tests cover
  completed/deferred/cancelled/failed/partial/unknown. CLI tests compare default
  and receipt-enabled stdout/stderr/exit behavior and post-resync session IDs.

Run logs and official protocol cache are task-local under `scripts/out/readonly/`
(ignored). Tests used the existing native Python 3.11 venv and Git Bash; no install.

## Release boundary and next steps

Part B is now implemented in `C:/EmberManualCodex/ember-voice-spine-v1`; its consumer uses
only this public CLI contract and binds the private receipt path to its obligation,
generation and session. Backend cost provenance stays explicit: this receipt supplies
already-produced estimates, not a new per-tool dollar accounting mechanism.

After both candidates are frozen: independent exact-tree Fable review; resolve any
findings; obtain separate release authorization; deploy through the existing
user-level update-safe process with rollback and installed-path/PID verification;
run the installed synthetic CLI canary; only then let Adam enter the key locally
and run the separately approved tiny metered GPT-Live canary.

**NOT RUN:** commit, push, merge, deployment, live restart, real credential access,
real model/GPT-Live calls, device microphone/playback, metered canary. No Presence,
companion-home, telephony, UAC, public listener or V2 changes.

## Two-tree freeze

Voice final evidence is `out/readonly-final`: **136 tests, 12 smoke checks,
21 contract checks included in those tests, four compiler targets, 35 file/format
scans, nine PowerShell parses, eight static invariants, zero warnings/errors/
findings/survivors, and a 16-file portable package with no private state**.
`docs/CODEX_HANDOFF.md` in Voice lists all 24 Voice repair paths and these 18 Hermes
paths, exact contracts, residuals and the ordered review/deploy/canary sequence.
Both source trees are frozen for independent review, without commits or deployment.

Hermes freeze manifest: `scripts/out/readonly/candidate-manifest.json` (base HEAD
plus SHA-256 of these exact 18 changed/new paths). Voice freeze manifest:
`out/readonly-final/source-manifest.json` (all 35 candidate source files).
No Python production source changed after the green Part A checkpoint.

Voice now plays natural provider PCM, uses the fixed read-only CLI consumer and
renders exact deep text. Headsets have simultaneous capture/playback with local
barge-in; speakers are explicitly half duplex. All deep-result speech points to
the screen. Bounded closure reconciliation refuses a new primary until matching
current-session usage is confirmed. The three-minute canary path is ready but
NOT RUN. Configured external memory provider compatibility and real audio behavior
remain explicit independent review/installed/live gates, not synthetic-test claims.
