"""Contract tests for the in-process gateway_startup plugin hook."""

from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig
from gateway.run import GatewayRunner
from hermes_cli.plugins import (
    PluginContext,
    PluginManager,
    PluginManifest,
    SHELL_UNSUPPORTED_HOOKS,
    VALID_HOOKS,
)


def test_gateway_startup_is_valid_but_shell_unsupported():
    assert "gateway_startup" in VALID_HOOKS
    assert "gateway_startup" in SHELL_UNSUPPORTED_HOOKS


def test_plugin_discovery_registers_without_invoking_startup():
    manager = PluginManager()
    ctx = PluginContext(
        PluginManifest(name="fixture", version="1.0.0"), manager
    )
    callback = MagicMock()

    ctx.register_hook("gateway_startup", callback)

    callback.assert_not_called()
    assert manager._hooks["gateway_startup"] == [callback]


@pytest.mark.asyncio
async def test_one_actual_gateway_start_invokes_hook_once(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    runner = GatewayRunner(
        GatewayConfig(platforms={}, sessions_dir=tmp_path / "sessions")
    )
    monkeypatch.setattr(runner, "_start_secondary_profile_adapters", lambda: 0)

    with patch("hermes_cli.plugins.invoke_hook") as invoke:
        assert await runner.start() is True

    calls = [call for call in invoke.call_args_list if call.args == ("gateway_startup",)]
    assert len(calls) == 1
    assert calls[0].kwargs == {"gateway": runner}
