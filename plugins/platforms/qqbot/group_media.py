"""Bounded QQ attachment references; passive observation never fetches their bytes."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

MAX_GROUP_ATTACHMENTS = 3
_MEDIA_QUERIES = (
    ("image", r"图片|图像|照片|截图|这张|那张|这幅|那幅|这个图|那个图|上图|表情包"),
    ("video", r"视频|录像|短片|mp4|mov"),
    ("audio", r"语音|音频|录音|声音"),
    ("document", r"文档|pdf|docx|xlsx|pptx|表格|幻灯片"),
    ("any", r"文件|附件|看看这个|看一下这个|看下这个|分析这个|处理这个|打开这个"),
)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def attachment_references(data: dict[str, Any], *, limit: int | None = MAX_GROUP_ATTACHMENTS) -> list[dict[str, str]]:
    """Retain only delivered attachment fields, including quoted media, with fixed bounds."""
    candidates = data["attachments"][:] if isinstance(data.get("attachments"), list) else []
    elements = data.get("msg_elements")
    for element in (elements if isinstance(elements, list) else []):
        if isinstance(element, dict) and isinstance(element.get("attachments"), list):
            candidates.extend(element["attachments"])
    result = []
    seen = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url.startswith("//"):
            url = "https:" + url
        try:
            scheme = urlparse(url).scheme
        except ValueError:
            continue
        if len(url) > 8192 or scheme not in {"https", "http"} or url in seen:
            continue
        reference = {"url": url, "content_type": _text(item.get("content_type"), 128),
                     "filename": _text(item.get("filename"), 200)}
        for key in ("asr_refer_text", "voice_wav_url"):
            if isinstance(item.get(key), str):
                reference[key] = item[key][:2000 if key == "asr_refer_text" else 8192]
        result.append(reference)
        seen.add(url)
        if limit is not None and len(result) == limit:
            break
    return result


def _kind(item: dict[str, str]) -> str:
    mime = item["content_type"].lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/") or mime == "voice":
        return "audio"
    return "document"


def select_group_media(messages: list[dict[str, Any]], query: str, current_id: str) -> dict[str, Any] | None:
    """A named file wins; otherwise select the latest message matching an explicit media request."""
    if query.lstrip().startswith("/"):
        return None
    query = query.casefold()
    candidates = []
    for message in reversed(messages):
        if message["id"] == current_id:
            continue
        attachments = attachment_references({"attachments": message.get("attachments")})
        if not attachments:
            continue
        named = [a for a in attachments if a["filename"] and a["filename"].casefold() in query]
        if named:
            return {**message, "attachments": named}
        candidates.append((message, attachments))
    kind = next((kind for kind, pattern in _MEDIA_QUERIES if re.search(pattern, query)), None)
    if kind is None:
        return None
    for message, attachments in candidates:
        matches = [a for a in attachments if kind == "any" or _kind(a) == kind]
        if matches:
            return {**message, "attachments": matches}
    return None
