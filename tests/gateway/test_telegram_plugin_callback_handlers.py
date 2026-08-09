"""Tests for plugin-registered Telegram inline-button callbacks."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    mod.constants.ParseMode.MARKDOWN = "Markdown"
    mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    mod.constants.ParseMode.HTML = "HTML"
    mod.constants.ChatType.PRIVATE = "private"
    mod.constants.ChatType.GROUP = "group"
    mod.constants.ChatType.SUPERGROUP = "supergroup"
    mod.constants.ChatType.CHANNEL = "channel"
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from gateway.config import PlatformConfig
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_context(name: str = "test-plugin"):
    manager = PluginManager()
    manifest = PluginManifest(name=name, version="1.0.0", description="test")
    return manager, PluginContext(manifest, manager)


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class TestRegisterTelegramCallbackHandler:
    def test_registers_prefix_callback_and_returns_copy(self):
        manager, ctx = _make_context()

        async def callback(query, data, adapter, context):
            return True

        assert ctx.register_telegram_callback_handler("wp:", callback) is True
        handlers = manager.get_telegram_callback_handlers()
        assert handlers == [("wp:", callback, "test-plugin")]
        handlers.clear()
        assert len(manager.get_telegram_callback_handlers()) == 1

    @pytest.mark.parametrize("prefix", [None, "", "   "])
    def test_rejects_empty_prefix(self, prefix):
        _manager, ctx = _make_context()
        with pytest.raises(ValueError, match="empty callback prefix"):
            ctx.register_telegram_callback_handler(prefix, lambda *_: True)

    def test_rejects_non_callable_callback(self):
        _manager, ctx = _make_context()
        with pytest.raises(ValueError, match="non-callable"):
            ctx.register_telegram_callback_handler("wp:", "not-callable")

    @pytest.mark.parametrize("second", ["wp:", "wp:t:", "w"])
    def test_rejects_overlapping_callback_namespaces(self, second):
        manager, first = _make_context("first-plugin")
        first.register_telegram_callback_handler("wp:", lambda **_: True)
        second_manifest = PluginManifest(
            name="second-plugin", version="1.0.0", description="test"
        )
        second_context = PluginContext(second_manifest, manager)
        with pytest.raises(ValueError, match="overlaps"):
            second_context.register_telegram_callback_handler(second, lambda **_: True)

    def test_rejects_prefix_larger_than_telegram_callback_limit(self):
        _manager, ctx = _make_context()
        with pytest.raises(ValueError, match="64-byte"):
            ctx.register_telegram_callback_handler("x" * 65, lambda **_: True)

    @pytest.mark.parametrize(
        "prefix",
        ["da:", "ea:", "sc:", "cl:", "gt:", "mp:", "mpg:", "mb", "mx", "update_prompt:"],
    )
    def test_rejects_built_in_callback_namespaces(self, prefix):
        _manager, ctx = _make_context()
        with pytest.raises(ValueError, match="reserved built-in"):
            ctx.register_telegram_callback_handler(prefix, lambda **_: True)

    @pytest.mark.parametrize("prefix", ["d", "m", "update"])
    def test_rejects_prefixes_that_shadow_built_ins(self, prefix):
        _manager, ctx = _make_context()
        with pytest.raises(ValueError, match="reserved built-in"):
            ctx.register_telegram_callback_handler(prefix, lambda **_: True)


class TestTelegramPluginCallbackDispatch:
    @pytest.mark.asyncio
    async def test_metadata_free_fake_callback_is_supported_with_explicit_test_allowlist(self):
        adapter = _make_adapter()
        callback = AsyncMock(return_value=True)
        manager = MagicMock()
        manager.get_telegram_callback_handlers.return_value = [
            ("wp:", callback, "weed-pickup")
        ]
        query = AsyncMock()
        query.data = "wp:test-only"
        query.message = None
        query.from_user = MagicMock(id="777", first_name="Tester")
        update = MagicMock(callback_query=query)
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "777"}, clear=False), \
             patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            await adapter._handle_callback_query(update, context)

        callback.assert_awaited_once_with(
            query=query,
            data="wp:test-only",
            adapter=adapter,
            context=context,
        )

    @pytest.mark.asyncio
    async def test_matching_authorized_callback_is_invoked_before_builtins(self):
        adapter = _make_adapter()
        callback = AsyncMock(return_value=True)
        manager = MagicMock()
        manager.get_telegram_callback_handlers.return_value = [
            ("wp:", callback, "weed-pickup")
        ]

        query = AsyncMock()
        query.data = "wp:toggle:flower"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat = MagicMock(type="private")
        query.message.message_thread_id = 99
        query.from_user = MagicMock(id="777", first_name="Tester")
        update = MagicMock(callback_query=query)
        context = MagicMock()

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False), \
             patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            await adapter._handle_callback_query(update, context)

        callback.assert_awaited_once_with(
            query=query,
            data="wp:toggle:flower",
            adapter=adapter,
            context=context,
        )

    @pytest.mark.asyncio
    async def test_unauthorized_user_cannot_invoke_plugin_callback(self):
        adapter = _make_adapter()
        callback = AsyncMock(return_value=True)
        manager = MagicMock()
        manager.get_telegram_callback_handlers.return_value = [
            ("wp:", callback, "weed-pickup")
        ]

        query = AsyncMock()
        query.data = "wp:scan"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat = MagicMock(type="private")
        query.message.message_thread_id = None
        query.from_user = MagicMock(id="999", first_name="Intruder")
        update = MagicMock(callback_query=query)

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "777"}, clear=False), \
             patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            await adapter._handle_callback_query(update, MagicMock())

        callback.assert_not_awaited()
        assert "not authorized" in query.answer.call_args.kwargs["text"].lower()

    @pytest.mark.asyncio
    async def test_runner_authorization_can_reject_an_allowed_user_in_wrong_chat(self):
        adapter = _make_adapter()
        callback = AsyncMock(return_value=True)
        manager = MagicMock()
        manager.get_telegram_callback_handlers.return_value = [
            ("wp:", callback, "weed-pickup")
        ]

        class Runner:
            def handle(self):
                return None

            def _is_user_authorized(self, source):
                return source.user_id == "777" and source.chat_id == "777"

        adapter._message_handler = Runner().handle
        query = AsyncMock()
        query.data = "wp:scan"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat = MagicMock(type="private")
        query.message.message_thread_id = None
        query.from_user = MagicMock(id="777", first_name="Tester")
        update = MagicMock(callback_query=query)

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False), \
             patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            await adapter._handle_callback_query(update, MagicMock())

        callback.assert_not_awaited()
        assert "not authorized" in query.answer.call_args.kwargs["text"].lower()

    @pytest.mark.asyncio
    async def test_false_callback_result_is_consumed_and_answered(self):
        adapter = _make_adapter()
        callback = AsyncMock(return_value=False)
        manager = MagicMock()
        manager.get_telegram_callback_handlers.return_value = [
            ("wp:", callback, "weed-pickup")
        ]
        query = AsyncMock()
        query.data = "wp:unknown"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.chat = MagicMock(type="private")
        query.message.message_thread_id = None
        query.from_user = MagicMock(id="777", first_name="Tester")
        update = MagicMock(callback_query=query)

        with patch.dict(os.environ, {"TELEGRAM_ALLOWED_USERS": "*"}, clear=False), \
             patch("hermes_cli.plugins.get_plugin_manager", return_value=manager):
            await adapter._handle_callback_query(update, MagicMock())

        callback.assert_awaited_once()
        assert query.answer.call_args.kwargs["text"] == "This action is unavailable or expired."
