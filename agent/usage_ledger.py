"""Secret-free incremental usage ledger for deterministic local monitoring.

``session_model_usage`` remains canonical accounting. This module only mirrors
already-normalized deltas into a versioned JSONL seam and is strictly
best-effort: telemetry can never fail an owner turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping, Optional

from hermes_constants import get_default_hermes_root, get_hermes_home

logger = logging.getLogger(__name__)

LEDGER_VERSION = 1
LEDGER_RELATIVE_PATH = Path("usage") / "model_usage.v1.jsonl"
MAX_TOKEN_COMPONENT = 10**12
MAX_LABEL_LENGTH = 160
_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+@-]{0,159}\Z")
_STATUSES = frozenset({"accepted", "failed", "prevented"})
_context: ContextVar[Optional[dict[str, Any]]] = ContextVar(
    "usage_ledger_context", default=None
)
_config_cache: dict[str, bool] = {}
_config_lock = threading.Lock()


def set_usage_context(*, surface: str, logical_run_id: str, max_iterations: Any):
    """Bind non-content attribution for one logical agent run."""
    budget = max_iterations if type(max_iterations) is int and max_iterations > 0 else None
    return _context.set(
        {
            "surface": _label(surface, "unknown"),
            "logical_run_id": _opaque_id(logical_run_id),
            "max_iterations": budget,
        }
    )


def reset_usage_context(token) -> None:
    try:
        _context.reset(token)
    except Exception:
        _context.set(None)


def _label(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_LABEL_LENGTH or not _LABEL_RE.fullmatch(text):
        return default
    lowered = text.casefold()
    if "://" in lowered or lowered.startswith(("authorization:", "bearer ")):
        return default
    return text


def _opaque_id(value: Any) -> str:
    """Return a stable bounded correlation id without persisting raw ids."""
    raw = str(value or "").encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:24]


def _token(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_TOKEN_COMPONENT:
        raise ValueError("invalid token component")
    return value


def _enabled(home: Path) -> bool:
    cache_key = str(home.resolve())
    with _config_lock:
        if cache_key in _config_cache:
            return _config_cache[cache_key]
        enabled = True
        try:
            from hermes_cli.config import load_config

            config = load_config()
            section = config.get("usage_ledger", {}) if isinstance(config, dict) else {}
            if isinstance(section, dict) and isinstance(section.get("enabled"), bool):
                enabled = section["enabled"]
        except Exception:
            # The ledger is advisory. A malformed/unreadable config must not
            # block accounting or the owner lane; retain the shipped default.
            logger.debug("Usage ledger config read failed; using enabled default", exc_info=True)
        _config_cache[cache_key] = enabled
        return enabled


def build_record(
    *,
    session_id: str,
    model: Any,
    provider: Any,
    task: Any,
    input_tokens: Any,
    output_tokens: Any,
    cache_read_tokens: Any,
    cache_write_tokens: Any,
    reasoning_tokens: Any,
    api_call_count: Any,
    status: str = "accepted",
    cost_status: Any = None,
) -> dict[str, Any]:
    """Build the closed, content-free v1 record schema."""
    if status not in _STATUSES:
        raise ValueError("invalid usage status")
    calls = _token(api_call_count)
    context = _context.get() or {}
    return {
        "version": LEDGER_VERSION,
        "record_id": uuid.uuid4().hex,
        "recorded_at": time.time(),
        "surface": _label(context.get("surface"), "unknown"),
        "task": _label(task, "model_call"),
        "provider": _label(provider, "unknown"),
        "model": _label(model, "unknown"),
        "logical_run_id": context.get("logical_run_id") or _opaque_id(session_id),
        "session_id": _opaque_id(session_id),
        "status": status,
        "cost_status": _label(cost_status, "unknown"),
        "api_call_count": calls,
        "usage": {
            "fresh_input": _token(input_tokens),
            "output": _token(output_tokens),
            "cache_read": _token(cache_read_tokens),
            "cache_write": _token(cache_write_tokens),
            "reasoning": _token(reasoning_tokens),
        },
        "budget": {"max_iterations": context.get("max_iterations")},
    }


def append_usage_record(**kwargs: Any) -> bool:
    """Append one closed-schema record; return False on any telemetry failure."""
    try:
        config_home = get_hermes_home()
        if not _enabled(config_home):
            return False
        record = build_record(**kwargs)
        line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        path = get_default_hermes_root() / LEDGER_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        return True
    except Exception:
        logger.warning("Usage ledger append failed (non-fatal)", exc_info=True)
        return False


def record_accounting_delta(
    session_id: str,
    values: Mapping[str, Any],
    *,
    task: str = "",
    status: str = "accepted",
) -> bool:
    """Mirror one normalized accounting delta without accepting extra fields."""
    component_names = (
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "api_call_count",
    )
    components = {name: values.get(name, 0) or 0 for name in component_names}
    # Finalization paths sometimes issue a no-op accounting update. It is not
    # a completed model call and should not create a synthetic ledger record.
    if not any(_token(value) for value in components.values()):
        return False
    return append_usage_record(
        session_id=session_id,
        model=values.get("model"),
        provider=values.get("billing_provider"),
        task=task or "model_call",
        input_tokens=components["input_tokens"],
        output_tokens=components["output_tokens"],
        cache_read_tokens=components["cache_read_tokens"],
        cache_write_tokens=components["cache_write_tokens"],
        reasoning_tokens=components["reasoning_tokens"],
        api_call_count=components["api_call_count"],
        status=status,
        cost_status=values.get("cost_status"),
    )


def record_run_status(
    session_id: str,
    *,
    model: Any,
    provider: Any,
    task: str,
    status: str,
) -> bool:
    """Record a content-free logical run outcome with no invented tokens."""
    return append_usage_record(
        session_id=session_id,
        model=model,
        provider=provider,
        task=task,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        reasoning_tokens=0,
        api_call_count=0,
        status=status,
        cost_status=None,
    )
