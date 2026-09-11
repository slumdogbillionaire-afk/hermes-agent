"""Opt-in, local single-query receipts. Never serialize agent internals."""

import math
import os
from pathlib import Path
import stat
import tempfile
from datetime import datetime, timezone

from utils import atomic_json_write


def validate_result_path(value: str) -> Path:
    """Require a new local file in an existing, writable, unlinked directory.

    The parent is caller-controlled for the duration of the run. Refusing an
    existing target also prevents stale receipts from looking like new results.
    """
    path = Path(value)
    if not path.is_absolute() or value.startswith(("\\\\", "//")):
        raise ValueError("--result-file must be an absolute local path")
    if os.name == "nt":
        if ":" in str(path)[2:] or any(part.endswith((" ", ".")) for part in path.parts):
            raise ValueError("Invalid --result-file path")
        if (os.path.isreserved(str(path)) if hasattr(os.path, "isreserved") else path.is_reserved()):
            raise ValueError("Reserved --result-file path")
        import ctypes
        if ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 3:
            raise ValueError("--result-file must be on a local fixed drive")
    for component in (path, *path.parents):
        if component.is_symlink() or (hasattr(component, "is_junction") and component.is_junction()):
            raise ValueError("--result-file cannot traverse links")
    if path.exists() or not path.parent.is_dir():
        raise ValueError("--result-file requires a new file in an existing directory")
    if not path.parent.stat().st_mode & stat.S_IWUSR:
        raise ValueError("--result-file parent is not writable")
    try:
        fd, probe = tempfile.mkstemp(prefix=".receipt-probe-", dir=path.parent)
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        raise ValueError("--result-file parent is not writable") from exc
    return path


def build_result_receipt(result, session_id: str) -> dict:
    result = result if isinstance(result, dict) else {}
    final = result.get("final_response")
    if result.get("interrupted"):
        status = "cancelled"
    elif result.get("failed"):
        status = "failed"
    elif result.get("compression_deferred"):
        status = "deferred"
    elif (result.get("completed") is True and not result.get("partial")
          and isinstance(final, str) and final.strip() and session_id):
        status = "completed"
    else:
        status = "failed"
    receipt = {
        "version": 1,
        "status": status,
        "session_id": session_id,
        # Closed vocabulary: a provider-supplied error/reason can contain input.
        "end_reason": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if status == "completed":
        receipt["final_response"] = final
    for key in ("input_tokens", "output_tokens", "total_tokens", "api_calls"):
        value = result.get(key)
        if type(value) is int and value >= 0:
            receipt[key] = value
    value = result.get("estimated_cost_usd")
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        receipt["estimated_cost_usd"] = value
    for key in ("cost_status", "cost_source", "model", "provider"):
        value = result.get(key)
        if isinstance(value, str) and len(value) <= 256:
            receipt[key] = value
    return receipt


def write_result_receipt(path: Path, result, session_id: str) -> None:
    validate_result_path(str(path))
    atomic_json_write(path, build_result_receipt(result, session_id), mode=0o600)
