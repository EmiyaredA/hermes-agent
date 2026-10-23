"""Profile/bot isolation and first-mention command handling for QQ group background."""

from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from plugins.platforms.qqbot.adapter import QQAdapter


def make_adapter(app_id="app"):
    adapter = QQAdapter(PlatformConfig(enabled=True, extra={
        "app_id": app_id, "client_secret": "secret", "group_policy": "allowlist",
        "group_allow_from": ["g1", "g2"], "group_context": {"enabled": True},
    }))
    adapter._remember_qq_identity({"user": {"id": "ready-self"}})
    adapter.handle_message = AsyncMock()
    return adapter


def payload(mid, text, group="g1"):
    return {"id": mid, "group_openid": group, "content": text,
            "author": {"member_openid": "user", "username": "User"}}


@pytest.mark.asyncio
async def test_first_group_specific_identity_is_removed_before_command_detection():
    adapter = make_adapter()
    data = payload("m", "<@group-self> /new")
    data["mentions"] = [{"member_openid": "group-self", "bot": True}]
    await adapter._on_message("GROUP_AT_MESSAGE_CREATE", data)
    event = adapter.handle_message.await_args.args[0]
    assert event.text == "/new" and event.get_command() == "new"


@pytest.mark.asyncio
async def test_background_is_separate_per_profile_and_bot_after_scope_changes(tmp_path):
    token = set_hermes_home_override(tmp_path / "profile-a")
    try:
        first = make_adapter()
    finally:
        reset_hermes_home_override(token)
    token = set_hermes_home_override(tmp_path / "profile-b")
    try:
        other_profile = make_adapter()
        other_bot = make_adapter("other-app")
    finally:
        reset_hermes_home_override(token)
    await first._on_message("GROUP_MESSAGE_CREATE", payload("m", "Only profile A"))
    await other_profile._on_message("GROUP_AT_MESSAGE_CREATE", payload("m", "Question"))
    await other_bot._on_message("GROUP_AT_MESSAGE_CREATE", payload("m", "Question"))
    assert first._group_context.root.is_relative_to(tmp_path / "profile-a")
    assert other_profile.handle_message.await_args.args[0].channel_context is None
    assert other_bot.handle_message.await_args.args[0].channel_context is None
    assert other_profile._group_context.root != other_bot._group_context.root
    await first._on_message("GROUP_AT_MESSAGE_CREATE", payload("m2", "Question"))
    assert "Only profile A" in first.handle_message.await_args.args[0].channel_context


@pytest.mark.asyncio
async def test_identical_message_ids_in_two_groups_each_receive_one_answer():
    adapter = make_adapter()
    await adapter._on_message("GROUP_AT_MESSAGE_CREATE", payload("same", "Question", "g1"))
    await adapter._on_message("GROUP_AT_MESSAGE_CREATE", payload("same", "Question", "g2"))
    assert adapter.handle_message.await_count == 2
    assert [call.args[0].source.chat_id for call in adapter.handle_message.await_args_list] == ["g1", "g2"]
