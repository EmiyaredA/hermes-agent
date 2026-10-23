"""Passive group media is retained as metadata and fetched only for addressed references."""

import json
import time
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.qqbot.adapter import QQAdapter


def adapter(tmp_path, app_id="media-app"):
    bot = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": app_id, "client_secret": "secret", "group_policy": "allowlist",
        "group_allow_from": ["g1", "g2"], "group_context": {"enabled": True},
    }))
    bot._remember_qq_identity({"user": {"id": "self"}})
    bot.handle_message = AsyncMock()
    cached = tmp_path / "cached-media"
    cached.write_bytes(b"test media")
    bot._download_and_cache = AsyncMock(return_value=str(cached))
    bot._stt_voice_attachment = AsyncMock(return_value="转写")
    return bot


def message(mid, text="", attachments=None, group="g1", addressed=False):
    data = {"id": mid, "content": text, "group_openid": group,
            "author": {"member_openid": "alice", "username": "Alice"},
            "timestamp": "2026-10-23T23:31:03+08:00"}
    if attachments:
        data["attachments"] = attachments
    if addressed:
        data["mentions"] = [{"id": "group-self", "is_you": True}]
    return data


def attachment(mime="image/jpeg", name="photo.jpg"):
    return {"content_type": mime, "filename": name, "url": "https://media.example/" + name}


@pytest.mark.asyncio
@pytest.mark.parametrize("mime,name,question", [
    ("image/jpeg", "photo.jpg", "基于这个图生成表情包"),
    ("video/mp4", "clip.mp4", "看看刚才的视频"),
    ("application/pdf", "report.pdf", "读一下上面的文档"),
])
async def test_passive_media_survives_restart_and_is_fetched_for_reference(tmp_path, mime, name, question):
    first = adapter(tmp_path)
    media = attachment(mime, name)
    await first._on_message("GROUP_MESSAGE_CREATE", message("media", attachments=[media]))
    first._download_and_cache.assert_not_awaited()
    first._stt_voice_attachment.assert_not_awaited()
    first.handle_message.assert_not_awaited()
    second = adapter(tmp_path)
    request = message("ask", question, addressed=True)
    await second._on_message("GROUP_MESSAGE_CREATE", request)
    second._download_and_cache.assert_awaited_once_with(media["url"], mime, name)
    event = second.handle_message.await_args.args[0]
    path = str(tmp_path / "cached-media")
    if mime.startswith("image/"):
        assert event.media_urls == [path]
    else:
        assert path in event.text
    assert "media.example" not in event.channel_context
    await second._on_message("GROUP_AT_MESSAGE_CREATE", request)
    second.handle_message.assert_awaited_once()
    second._download_and_cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_smalltalk_commands_other_groups_and_other_bots_never_fetch_background_files(tmp_path):
    bot = adapter(tmp_path)
    await bot._on_message("GROUP_MESSAGE_CREATE", message("media", attachments=[attachment()]))
    for mid, text, group in [("chat", "你刚刚说啥了", "g1"), ("cmd", "/new", "g1"),
                             ("other", "看看这个图", "g2")]:
        await bot._on_message("GROUP_AT_MESSAGE_CREATE", message(mid, text, group=group))
    bot._download_and_cache.assert_not_awaited()
    other = adapter(tmp_path, "other-app")
    await other._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "看看这个图"))
    other._download_and_cache.assert_not_awaited()


@pytest.mark.asyncio
async def test_named_older_document_wins_over_newer_attachment(tmp_path):
    bot = adapter(tmp_path)
    old, new = attachment("application/pdf", "budget.pdf"), attachment("application/pdf", "meeting.pdf")
    await bot._on_message("GROUP_MESSAGE_CREATE", message("old", attachments=[old]))
    await bot._on_message("GROUP_MESSAGE_CREATE", message("new", attachments=[new]))
    await bot._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "帮我读一下 budget.pdf"))
    bot._download_and_cache.assert_awaited_once_with(old["url"], old["content_type"], old["filename"])


@pytest.mark.asyncio
@pytest.mark.parametrize("quoted", [False, True])
async def test_current_and_quoted_attachments_take_precedence_over_group_history(tmp_path, quoted):
    bot = adapter(tmp_path)
    await bot._on_message("GROUP_MESSAGE_CREATE", message("old", attachments=[attachment()]))
    fresh = attachment("image/png", "current.png")
    request = message("ask", "看一下这个图", attachments=None if quoted else [fresh])
    if quoted:
        request.update(message_type=103, msg_elements=[{"attachments": [fresh]}])
    await bot._on_message("GROUP_AT_MESSAGE_CREATE", request)
    bot._download_and_cache.assert_awaited_once_with(fresh["url"], fresh["content_type"], fresh["filename"])


@pytest.mark.asyncio
async def test_expired_media_is_not_fetched_and_legacy_cache_still_works(tmp_path):
    bot = adapter(tmp_path)
    await bot._on_message("GROUP_MESSAGE_CREATE", message("old", attachments=[attachment()]))
    path = bot._group_context._path("g1")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["messages"][0]["received_at"] = time.time() - 25 * 3600
    path.write_text(json.dumps(data), encoding="utf-8")
    await bot._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "看看这个图"))
    bot._download_and_cache.assert_not_awaited()
    bot.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_historical_download_is_reported_without_a_fake_file_path(tmp_path):
    bot = adapter(tmp_path)
    await bot._on_message("GROUP_MESSAGE_CREATE", message("media", attachments=[attachment()]))
    bot._download_and_cache.return_value = None
    await bot._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "看看这个图"))
    event = bot.handle_message.await_args.args[0]
    assert "photo.jpg" in event.text and "失败" in event.text
    assert not event.media_urls


@pytest.mark.asyncio
async def test_richer_duplicate_event_adds_media_metadata_without_replaying_message(tmp_path):
    bot = adapter(tmp_path)
    await bot._on_message("GROUP_MESSAGE_CREATE", message("media", "[图片]"))
    await bot._on_message("GROUP_MESSAGE_CREATE", message("media", attachments=[attachment()]))
    await bot._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "看看这个图"))
    bot._download_and_cache.assert_awaited_once()
    bot.handle_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_historical_image_bytes_reach_the_isolated_media_cache(tmp_path):
    from pathlib import Path

    import httpx

    bot = adapter(tmp_path)
    del bot._download_and_cache
    media = attachment()
    media["url"] = "https://93.184.216.34/photo.jpg"
    import base64
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=image_bytes)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        bot._http_client = client
        await bot._on_message("GROUP_MESSAGE_CREATE", message("media", attachments=[media]))
        assert not requests
        await bot._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "看看这个图"))
    event = bot.handle_message.await_args.args[0]
    assert len(requests) == 1
    assert Path(event.media_urls[0]).read_bytes() == image_bytes


@pytest.mark.asyncio
async def test_history_media_cannot_download_private_network_urls(tmp_path):
    import httpx

    bot = adapter(tmp_path)
    del bot._download_and_cache
    media = attachment()
    media["url"] = "http://127.0.0.1/private.jpg"
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=b"private")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        bot._http_client = client
        await bot._on_message("GROUP_MESSAGE_CREATE", message("media", attachments=[media]))
        await bot._on_message("GROUP_AT_MESSAGE_CREATE", message("ask", "看看这个图"))
    assert not requests
    event = bot.handle_message.await_args.args[0]
    assert not event.media_urls and "失败" in event.text
