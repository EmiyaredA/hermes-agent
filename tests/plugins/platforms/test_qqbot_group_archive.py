"""Complete QQ archives retain original files and per-sender identity without passive model calls."""

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.qqbot.adapter import QQAdapter

PIXELS = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


@pytest.fixture
def archive_downloads(monkeypatch):
    calls = []

    async def download(client, headers, url, destination):
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_bytes(PIXELS)
        destination.chmod(0o600)
        calls.append(url)
        return len(PIXELS)

    async def avatar(app_id, sender_id, destination):
        return await download(None, {}, "https://q.qlogo.cn/qqapp/" + app_id + "/" + sender_id + "/640", destination)

    monkeypatch.setattr("plugins.platforms.qqbot.group_archive.download_file", download)
    monkeypatch.setattr("plugins.platforms.qqbot.group_archive.download_avatar", avatar)
    return calls


def bot(**overrides):
    extra = {"app_id": "archive-app", "client_secret": "secret", "group_policy": "allowlist",
             "group_allow_from": ["g1", "g2"], "group_context": {"enabled": True, "archive_enabled": True,
             "max_messages": 2}}
    extra.update(overrides)
    result = QQAdapter(PlatformConfig(enabled=True, extra=extra))
    result._remember_qq_identity({"user": {"id": "self"}})
    result._ensure_token = AsyncMock(return_value="test-token")
    result.handle_message = AsyncMock()
    result._stt_voice_attachment = AsyncMock()
    return result


def event(mid, text="", sender="alice", group="g1", files=0):
    data = {"id": mid, "content": text, "group_openid": group, "timestamp": "2026-10-23T23:41:44+08:00",
            "author": {"member_openid": sender, "username": "同名群友"}}
    if files:
        data["attachments"] = [{"url": f"https://media.example/{mid}/{i}.png", "content_type": "image/png",
                                "filename": f"{i}.png"} for i in range(files)]
    return data


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["我发了啥", "这是啥", "怎么样", "刚刚那个呢"])
async def test_plain_reference_sees_stored_pixels_and_sender_without_keyword_gate(archive_downloads, question):
    adapter = bot()
    await adapter._on_message("GROUP_MESSAGE_CREATE", event("image", files=1))
    adapter.handle_message.assert_not_awaited()
    adapter._stt_voice_attachment.assert_not_awaited()
    before = len(archive_downloads)
    await adapter._on_message("GROUP_AT_MESSAGE_CREATE", event("ask", question))
    message = adapter.handle_message.await_args.args[0]
    assert message.media_urls and Path(message.media_urls[0]).read_bytes() == PIXELS
    assert '"sender_id": "alice"' in message.text and '"message_id": "image"' in message.text
    assert '"sender_id": "alice"' in message.channel_context
    assert "messages.jsonl" in message.channel_context
    assert len(archive_downloads) == before  # No download on @: the original was already saved.


@pytest.mark.asyncio
async def test_avatar_question_receives_current_speakers_pixels_instead_of_old_chat_images(archive_downloads):
    adapter = bot()
    await adapter._on_message("GROUP_MESSAGE_CREATE", event("chat-photo", sender="alice", files=1))
    await adapter._on_message("GROUP_MESSAGE_CREATE", event("other-group", sender="bob", group="g2"))
    await adapter._on_message("GROUP_AT_MESSAGE_CREATE", event("avatar-question", "我头像是什么", sender="bob"))
    output = adapter.handle_message.await_args.args[0]
    record = json.loads(adapter._group_archive.record_path("g1", "avatar-question").read_text())
    avatar = record["member"]["avatar_local_path"]
    assert output.media_urls == [avatar]
    assert Path(avatar).read_bytes() == PIXELS
    assert '"kind": "profile_avatar"' in output.text and '"sender_id": "bob"' in output.text
    assert '"message_id": "chat-photo"' not in output.text
    assert str(adapter._group_archive.group_root("g2")) not in output.text
    adapter._stt_voice_attachment.assert_not_awaited()


@pytest.mark.asyncio
async def test_full_history_and_every_attachment_survive_recent_window_eviction(archive_downloads):
    adapter = bot()
    original = event("old", "长文本" * 1500, files=5)
    original["author"].update(signature="这是签名", member_role="admin")
    await adapter._on_message("GROUP_MESSAGE_CREATE", original)
    for i in range(5):
        await adapter._on_message("GROUP_MESSAGE_CREATE", event(str(i), "后续聊天", sender="bob"))
    path = adapter._group_archive.record_path("g1", "old")
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["text"] == original["content"] and record["raw_message"] == original
    assert len(record["files"]) == 5
    assert all(Path(item["local_path"]).read_bytes() == PIXELS for item in record["files"])
    assert path.stat().st_mode & 0o777 == 0o600
    assert record["member"]["signature"] == "这是签名" and record["member"]["member_role"] == "admin"
    recent = json.loads(adapter._group_context._path("g1").read_text(encoding="utf-8"))
    assert len(recent["messages"]) == 2 and all(m["id"] != "old" for m in recent["messages"])
    index = (adapter._group_archive.group_root("g1") / "messages.jsonl").read_text(encoding="utf-8")
    assert "长文本" * 1500 in index
    adapter.handle_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_name_users_files_and_profiles_are_distinct_and_group_scoped(archive_downloads):
    adapter = bot()
    await adapter._on_message("GROUP_MESSAGE_CREATE", event("alice-file", sender="alice", files=1))
    await adapter._on_message("GROUP_MESSAGE_CREATE", event("bob-file", sender="bob", files=1))
    records = [json.loads(adapter._group_archive.record_path("g1", mid).read_text()) for mid in ("alice-file", "bob-file")]
    assert records[0]["name"] == records[1]["name"]
    assert records[0]["sender"] != records[1]["sender"]
    assert records[0]["files"][0]["local_path"] != records[1]["files"][0]["local_path"]
    await adapter._on_message("GROUP_AT_MESSAGE_CREATE", event("ask", "看看", group="g2"))
    output = adapter.handle_message.await_args.args[0]
    assert not output.media_urls and "alice-file" not in output.channel_context
    assert records[0]["member"]["signature"] is None
    assert records[0]["member"]["signature_status"] == "not_provided_by_qq"
    assert Path(records[0]["member"]["avatar_local_path"]).is_file()


@pytest.mark.asyncio
async def test_restart_dual_events_do_not_redownload_saved_files_or_answer_twice(archive_downloads):
    first = bot()
    payload = event("photo", files=1)
    await first._on_message("GROUP_MESSAGE_CREATE", payload)
    count = len(archive_downloads)
    second = bot()
    await second._on_message("GROUP_MESSAGE_CREATE", payload)
    assert len(archive_downloads) == count
    request = event("ask", "我发了啥")
    await second._on_message("GROUP_AT_MESSAGE_CREATE", request)
    await second._on_message("GROUP_MESSAGE_CREATE", {**request, "mentions": [{"id": "self", "is_you": True}]})
    second.handle_message.assert_awaited_once()
    assert second.handle_message.await_args.args[0].media_urls


@pytest.mark.asyncio
async def test_bot_media_original_is_archived_and_failed_archiving_does_not_retry_delivery(archive_downloads, tmp_path):
    from gateway.platforms.base import SendResult
    adapter = bot()
    original = tmp_path / "bot.png"
    original.write_bytes(PIXELS)
    await adapter._remember_group_media("g1", str(original), "image", "发的图", SendResult(success=True, message_id="sent"))
    record = json.loads(adapter._group_archive.record_path("g1", "sent").read_text())
    assert record["sender"] == "assistant" and Path(record["files"][0]["local_path"]).read_bytes() == PIXELS
    adapter._group_archive.record = AsyncMock(side_effect=OSError("disk failure"))
    adapter._send_text_to = AsyncMock(return_value=SendResult(success=True, message_id="text-sent"))
    result = await adapter._send_group_text("g1", "已经发出")
    assert result.success
    adapter._send_text_to.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_record_recovers_original_files_after_restart(archive_downloads):
    from plugins.platforms.qqbot.group_archive_io import write_json
    first = bot()
    await first._on_message("GROUP_MESSAGE_CREATE", event("pending", files=1))
    path = first._group_archive.record_path("g1", "pending")
    record = json.loads(path.read_text())
    record.update(archive_state="pending", files=[])
    write_json(path, record)
    second = bot()
    await second._seed_group_archive("g1")
    recovered = json.loads(path.read_text())
    assert recovered["archive_state"] == "ready"
    assert Path(recovered["files"][0]["local_path"]).read_bytes() == PIXELS
    assert recovered["sender"] == "alice"


@pytest.mark.asyncio
@pytest.mark.parametrize("mime,filename", [(None, None), ("image/png", "dm.png"),
                                          ("video/mp4", "dm.mp4"), ("application/pdf", "dm.pdf")])
async def test_direct_chat_never_uses_group_archive_context_profiles_or_history(archive_downloads, tmp_path, mime, filename):
    adapter = bot(dm_policy="allowlist", allow_from=["dm-user"])
    adapter._group_archive.record = AsyncMock()
    adapter._group_context.observe = AsyncMock()
    adapter._group_context.background_images = AsyncMock()
    picture = tmp_path / "dm.png"
    picture.write_bytes(PIXELS)
    adapter._download_and_cache = AsyncMock(return_value=str(picture))
    payload = {"id": "dm-message", "content": "我头像是什么", "author": {"user_openid": "dm-user"}}
    if mime:
        payload["attachments"] = [{"url": "https://media.example/" + filename, "filename": filename, "content_type": mime}]
    await adapter._on_message("C2C_MESSAGE_CREATE", payload)
    message = adapter.handle_message.await_args.args[0]
    assert message.source.chat_type == "dm" and message.channel_context is None
    assert "群聊背景" not in message.text and "messages.jsonl" not in message.text
    adapter._group_archive.record.assert_not_awaited()
    adapter._group_context.observe.assert_not_awaited()
    adapter._group_context.background_images.assert_not_awaited()
    assert not archive_downloads
    if mime == "image/png":
        assert message.media_urls == [str(picture)]
    elif mime:
        assert str(picture) in message.text


@pytest.mark.asyncio
async def test_direct_chat_media_send_never_archives_as_group_media(archive_downloads, tmp_path):
    from gateway.platforms.base import SendResult
    adapter = bot(dm_policy="open")
    adapter._chat_type_map["dm-user"] = "c2c"
    adapter._ensure_connected = AsyncMock(return_value=True)
    adapter._upload_local_file = AsyncMock(return_value={"file_info": "uploaded"})
    adapter._post_message = AsyncMock(return_value=SendResult(success=True, message_id="dm-out"))
    adapter._remember_group_media = AsyncMock()
    result = await adapter.send_document("dm-user", str(tmp_path / "document.pdf"))
    assert result.success
    adapter._remember_group_media.assert_not_awaited()
    assert not archive_downloads


@pytest.mark.asyncio
async def test_avatar_fetch_keeps_fixed_qq_https_host_and_refuses_redirects(monkeypatch, tmp_path):
    import httpx
    from plugins.platforms.qqbot.group_archive_io import download_avatar
    requests = []
    options = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=PIXELS)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))

    def make_client(**kwargs):
        options.append(kwargs)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    target = tmp_path / "avatar.jpg"
    await download_avatar("app/../../", "user@internal?x=y", target)
    assert target.read_bytes() == PIXELS
    assert requests[0].url.host == "q.qlogo.cn" and requests[0].url.scheme == "https"
    assert options[0]["follow_redirects"] is False
