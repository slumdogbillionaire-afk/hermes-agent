"""TelegramAdapter send-path health gating after reconnect storms.

After sustained Bad Gateway / TimedOut reconnect cycles, the PTB httpx client
can enter a wedged state where ``bot.send_message()`` returns a valid Message
but nothing reaches the recipient.  ``_send_path_degraded`` short-circuits
``send()`` so cron's live-adapter branch falls through to standalone HTTP.
"""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return
    mod = MagicMock()
    mod.error.NetworkError = type("NetworkError", (OSError,), {})
    mod.error.TimedOut = type("TimedOut", (OSError,), {})
    mod.error.BadRequest = type("BadRequest", (Exception,), {})
    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, mod)
    sys.modules.setdefault("telegram.error", mod.error)


_ensure_telegram_mock()

from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402


def _make_adapter() -> TelegramAdapter:
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._bot = MagicMock()
    adapter._bot.send_message = AsyncMock(return_value=MagicMock(message_id=42))
    return adapter


@pytest.mark.asyncio
async def test_send_short_circuits_when_path_degraded():
    """Degraded adapter returns failure WITHOUT calling send_message,
    so cron's live-adapter branch falls through to standalone HTTP."""
    adapter = _make_adapter()
    adapter._send_path_degraded = True

    result = await adapter.send("123", "hello")

    assert result.success is False
    assert result.error == "send_path_degraded"
    assert result.retryable is True
    adapter._bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_image_file_short_circuits_when_path_degraded():
    """2026-08-19 capability repair: send_image_file had no degraded-path
    check, so a batch of images sent after a mid-turn reconnect would each
    attempt real send_photo I/O and time out individually instead of
    failing fast -- the "only one image sporadically sends" symptom."""
    adapter = _make_adapter()
    adapter._bot.send_photo = AsyncMock(return_value=MagicMock(message_id=1))
    adapter._send_path_degraded = True

    result = await adapter.send_image_file("123", "C:/fake/path.png")

    assert result.success is False
    assert result.error == "send_path_degraded"
    assert result.retryable is True
    adapter._bot.send_photo.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_multiple_images_falls_back_fast_when_path_degraded():
    """send_multiple_images must not attempt send_media_group against a
    degraded connection; it should fall straight to the base per-image
    loop, which itself now fails fast via send_image_file's new check."""
    adapter = _make_adapter()
    adapter._bot.send_media_group = AsyncMock()
    adapter._bot.send_photo = AsyncMock()
    adapter._send_path_degraded = True

    with patch.object(
        adapter.__class__.__bases__[0], "send_multiple_images", AsyncMock()
    ) as base_send:
        await adapter.send_multiple_images("123", [("file:///fake.png", "alt")])

    adapter._bot.send_media_group.assert_not_awaited()
    base_send.assert_awaited_once()


