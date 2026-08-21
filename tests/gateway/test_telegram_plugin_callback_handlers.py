"""Regression tests for owner-scoped Telegram plugin callback handlers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from plugins.platforms.telegram.adapter import TelegramAdapter


def _context(manager=None, name="test-plugin"):
    manager = manager or PluginManager()
    manifest = PluginManifest(name=name, version="1.0.0", description="test")
    return manager, PluginContext(manifest, manager)


def _adapter():
    adapter = TelegramAdapter(
        PlatformConfig(enabled=True, token="test-token", extra={})
    )
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


def test_registration_returns_true_and_manager_returns_a_copy():
    manager, ctx = _context()
    callback = AsyncMock(return_value=True)

    assert ctx.register_telegram_callback_handler("wp:", callback) is True
    handlers = manager.get_telegram_callback_handlers()
    assert handlers == [("wp:", callback, "test-plugin")]
    handlers.clear()
    assert manager.get_telegram_callback_handlers() == [
        ("wp:", callback, "test-plugin")
    ]


@pytest.mark.parametrize("prefix", [None, "", "   "])
def test_empty_prefix_is_rejected(prefix):
    _manager, ctx = _context()
    with pytest.raises(ValueError, match="empty callback prefix"):
        ctx.register_telegram_callback_handler(prefix, lambda **_: True)


def test_non_callable_is_rejected():
    _manager, ctx = _context()
    with pytest.raises(ValueError, match="non-callable"):
        ctx.register_telegram_callback_handler("wp:", "not-callable")


@pytest.mark.parametrize("second", ["wp:", "wp:t:", "w"])
def test_overlapping_plugin_prefixes_fail_closed(second):
    manager, first = _context(name="first")
    first.register_telegram_callback_handler("wp:", lambda **_: True)
    _manager, other = _context(manager, "second")
    with pytest.raises(ValueError, match="overlaps"):
        other.register_telegram_callback_handler(second, lambda **_: True)


@pytest.mark.parametrize(
    "prefix",
    ["da:", "ea:", "sc:", "cl:", "gt:", "mp:", "mpg:", "mb", "mx", "update_prompt:"],
)
def test_built_in_namespaces_are_reserved(prefix):
    _manager, ctx = _context()
    with pytest.raises(ValueError, match="reserved built-in"):
        ctx.register_telegram_callback_handler(prefix, lambda **_: True)


@pytest.mark.parametrize("prefix", ["d", "m", "update", "x" * 65])
def test_shadowing_or_oversized_prefixes_are_rejected(prefix):
    _manager, ctx = _context()
    with pytest.raises(ValueError):
        ctx.register_telegram_callback_handler(prefix, lambda **_: True)


def test_per_plugin_unload_removes_only_exact_owned_tuple():
    manager, first = _context(name="first")
    _manager, second = _context(manager, "second")
    first_callback = AsyncMock(return_value=True)
    second_callback = AsyncMock(return_value=True)
    first.register_telegram_callback_handler("one:", first_callback)
    second.register_telegram_callback_handler("two:", second_callback)

    assert manager.unload("first") is True
    assert manager.get_telegram_callback_handlers() == [
        ("two:", second_callback, "second")
    ]
    assert manager.unload("first") is False


def test_failed_plugin_registration_rolls_back_callback_ownership(tmp_path):
    plugin = tmp_path / "broken"
    plugin.mkdir()
    (plugin / "plugin.yaml").write_text(
        "name: broken\nversion: 1.0.0\n", encoding="utf-8"
    )
    (plugin / "__init__.py").write_text(
        "async def callback(**kwargs):\n    return True\n\n"
        "def register(ctx):\n"
        "    ctx.register_telegram_callback_handler('broken:', callback)\n"
        "    raise RuntimeError('fixture failure')\n",
        encoding="utf-8",
    )
    manager = PluginManager()
    manifest = PluginManifest(
        name="broken", version="1.0.0", path=plugin, source="user"
    )

    manager._load_plugin(manifest)

    assert manager.get_telegram_callback_handlers() == []


@pytest.mark.asyncio
async def test_authorized_callback_dispatches_before_builtins():
    adapter = _adapter()
    callback = AsyncMock(return_value=True)
    manager = MagicMock()
    manager.get_telegram_callback_handlers.return_value = [
        ("wp:", callback, "weed-pickup")
    ]
    query = AsyncMock()
    query.data = "wp:toggle:flower"
    query.message = MagicMock(chat_id=12345)
    query.message.chat = MagicMock(type="private")
    query.message.message_thread_id = 99
    query.from_user = MagicMock(id="777", first_name="Tester")
    update = MagicMock(callback_query=query)

    with patch.dict("os.environ", {"TELEGRAM_ALLOWED_USERS": "*"}), patch(
        "hermes_cli.plugins.get_plugin_manager", return_value=manager
    ):
        await adapter._handle_callback_query(update, MagicMock())

    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_unauthorized_callback_never_reaches_plugin():
    adapter = _adapter()
    callback = AsyncMock(return_value=True)
    manager = MagicMock()
    manager.get_telegram_callback_handlers.return_value = [
        ("wp:", callback, "weed-pickup")
    ]
    query = AsyncMock()
    query.data = "wp:scan"
    query.message = MagicMock(chat_id=12345)
    query.message.chat = MagicMock(type="private")
    query.message.message_thread_id = None
    query.from_user = MagicMock(id="999", first_name="Intruder")

    with patch.dict("os.environ", {"TELEGRAM_ALLOWED_USERS": "777"}), patch(
        "hermes_cli.plugins.get_plugin_manager", return_value=manager
    ):
        await adapter._handle_callback_query(
            MagicMock(callback_query=query), MagicMock()
        )

    callback.assert_not_awaited()
    assert "not authorized" in query.answer.call_args.kwargs["text"].lower()
