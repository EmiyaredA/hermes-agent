"""Native QQ media delivery retains an inbound passive reply anchor."""

from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import SendResult
from plugins.platforms.qqbot.adapter import QQAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["send_image", "send_document"])
@pytest.mark.parametrize("explicit", [None, "explicit-message"])
async def test_group_media_uses_last_addressed_message_without_overriding_explicit_reply(method, explicit):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={"app_id": "test", "client_secret": "secret"}))
    adapter._chat_type_map["group"] = "group"
    adapter._last_msg_id["group"] = "last-request"
    adapter._ensure_connected = AsyncMock(return_value=True)
    adapter._upload_media = AsyncMock(return_value={"file_info": "uploaded"})
    adapter._post_message = AsyncMock(return_value=SendResult(success=True, message_id="sent"))
    result = await getattr(adapter, method)("group", "https://media.example/test.png", reply_to=explicit)
    assert result.success
    path, body = adapter._post_message.await_args.args
    assert path == "/v2/groups/group/messages"
    assert body["msg_id"] == (explicit or "last-request")
