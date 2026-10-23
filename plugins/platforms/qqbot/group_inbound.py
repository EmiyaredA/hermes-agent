"""QQ full-group intake: observe all permitted messages, dispatch addressed requests only."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any

from hermes_constants import get_hermes_home

from .group_context import GroupContextSettings, GroupContextStore, describe_group_message
from .group_media import attachment_references

logger = logging.getLogger(__name__)
GROUP_EVENTS = frozenset({"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"})


def _ids(user: dict[str, Any]) -> set[str]:
    return {str(user[k]) for k in ("id", "member_openid", "user_openid") if user.get(k)}


class QQGroupContextMixin:
    def _init_group_context(self, extra: dict[str, Any]) -> None:
        settings = GroupContextSettings.parse(extra.get("group_context"))
        app_key = hashlib.sha256(self._app_id.encode()).hexdigest()[:24]
        self._group_context = GroupContextStore(get_hermes_home() / "cache" / "qqbot" / app_key / "group-context", settings)
        self._qq_self_ids: set[str] = set()
        self._qq_group_self_ids: dict[str, set[str]] = {}
        self._observed_group_counts: dict[str, int] = {}
        logger.info("[%s] QQ group context %s (messages=%d, hours=%d, chars=%d; mention-only replies)",
                    self._log_tag, "enabled" if settings.enabled else "disabled", settings.max_messages,
                    settings.max_age_hours, settings.max_context_chars)

    def _remember_qq_identity(self, ready: dict[str, Any]) -> None:
        user = ready.get("user")
        if isinstance(user, dict):
            self._qq_self_ids.update(_ids(user))

    def _group_addressed(self, event_type: str, data: dict[str, Any], group_id: str) -> bool:
        raw_mentions = data.get("mentions")
        mentions = [m for m in (raw_mentions if isinstance(raw_mentions, list) else []) if isinstance(m, dict)]
        # In full-message mode QQ can omit the separate AT event and use a group-scoped
        # mention ID, unrelated to READY.user.id. QQ's is_you flag identifies this bot.
        self_mentions = [m for m in mentions if m.get("is_you") is True]
        for mention in self_mentions:
            self._qq_group_self_ids.setdefault(group_id, set()).update(_ids(mention))
        if event_type == "GROUP_AT_MESSAGE_CREATE":
            bots = [m for m in mentions if m.get("bot") is True and m.get("is_you") is not False]
            # Older AT events may lack is_you; a single bot mention is unambiguous here.
            if not self_mentions and len(bots) == 1:
                self._qq_group_self_ids.setdefault(group_id, set()).update(_ids(bots[0]))
            return True
        if self_mentions:
            return True
        own_ids = self._qq_self_ids | self._qq_group_self_ids.get(group_id, set())
        if any(_ids(mention) & own_ids for mention in mentions):
            return True
        targets = set(re.findall(r"<@!?([^>]+)>", str(data.get("content") or "")))
        return bool(targets & own_ids)

    async def _receive_group_message(self, event_type: str, data: dict[str, Any]) -> None:
        group_id = str(data.get("group_openid") or "")
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        sender = str(author.get("member_openid") or author.get("id") or "")
        message_id = str(data.get("id") or "")
        if not group_id or not sender or not message_id or not self._is_group_allowed(group_id, sender):
            return
        own_ids = self._qq_self_ids | self._qq_group_self_ids.get(group_id, set())
        if _ids(author) & own_ids:
            return
        addressed = author.get("bot") is not True and self._group_addressed(event_type, data, group_id)
        if event_type == "GROUP_MESSAGE_CREATE" and not self._group_context.settings.enabled and not addressed:
            return
        name = str(author.get("username") or sender)
        timestamp = str(data.get("timestamp") or "")
        text = describe_group_message(data, self._group_context.settings.max_message_chars)
        context = await self._group_context.observe(
            group_id, message_id, sender, name, timestamp, text or "[空消息]", include_context=addressed,
            attachments=attachment_references(data))
        if event_type == "GROUP_MESSAGE_CREATE":
            count = self._observed_group_counts.get(group_id, 0) + 1
            self._observed_group_counts[group_id] = count
            if count == 1 or count % 50 == 0:
                logger.info("[%s] Full group intake active: group=%s events=%d", self._log_tag, group_id, count)
        if not addressed or self._dedup.is_duplicate(f"group-request:{group_id}:{message_id}"):
            return
        content = str(data.get("content") or "").strip()
        own_ids = self._qq_self_ids | self._qq_group_self_ids.get(group_id, set())
        for identity in own_ids:
            content = re.sub(r"<@!?" + re.escape(identity) + r">\s*", "", content)
        content = self._strip_at_mention(content)
        if not content and not data.get("attachments") and not data.get("msg_elements"):
            content = "有人在群里叫你，接着当前话题自然回一句；没有明确话题就简短应一声。"
        attachments = data.get("attachments")
        if not attachments and not data.get("msg_elements"):
            reference = await self._group_context.media_for_request(group_id, content, message_id)
            if reference:
                attachments = reference["attachments"]
                content += ("\n\n[本群已选中的历史附件，来自 " + reference["name"] + "；" +
                            reference["timestamp"] + "，下方为本次附件处理结果；这不是新的群友发言。]")
                logger.info("[%s] Group attachment selected: group=%s count=%d",
                            self._log_tag, group_id, len(attachments))
        logger.info("[%s] Group request admitted: group=%s event=%s context_chars=%d",
                    self._log_tag, group_id, event_type, len(context or ""))
        await self._ingest(
            data, message_id, content, attachments, timestamp,
            chat_id=group_id, qq_chat_type="group", channel_context=context,
            user_id=sender, user_name=name, chat_type="group")

    async def _remember_group_reply(self, group_id: str, content: str, result: Any) -> None:
        if result.success and result.message_id and self._is_group_allowed(group_id):
            await self._group_context.observe(group_id, str(result.message_id), "assistant", "机器人",
                                              datetime.now(UTC).isoformat(), content)
