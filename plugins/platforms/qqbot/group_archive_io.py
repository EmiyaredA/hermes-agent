"""Private, atomic QQ archive files and bounded streaming downloads."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from gateway.platforms.base import get_inbound_media_max_bytes, validate_inbound_media_size
from tools.url_safety import async_is_safe_url


def private_directory(path: Path) -> None:
    if not path.parent.exists():
        private_directory(path.parent)
    path.mkdir(exist_ok=True, mode=0o700)
    path.chmod(0o700)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None


def write_json(path: Path, value: Any) -> None:
    private_directory(path.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def append_index(path: Path, record: dict[str, Any], record_path: Path) -> None:
    entry = {k: record[k] for k in ("id", "sender", "name", "timestamp", "text", "received_at")}
    entry["files"] = public_files(record)
    entry["member"] = record.get("member", {})
    entry["record_path"] = str(record_path)
    private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def public_files(record: dict[str, Any]) -> list[dict[str, Any]]:
    keys = ("filename", "content_type", "status", "local_path", "bytes", "error")
    return [{k: item[k] for k in keys if k in item} for item in record.get("files", [])]


async def download_file(client, headers: dict[str, str], url: str, destination: Path) -> int:
    """Save original bytes (including audio) without analysis, conversion or transcription."""
    if not await async_is_safe_url(url):
        raise ValueError("unsafe_attachment_url")
    return await _stream_file(client, headers, url, destination)


async def _stream_file(client, headers: dict[str, str], url: str, destination: Path) -> int:
    private_directory(destination.parent)
    limit = get_inbound_media_max_bytes()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            total = 0
            async with client.stream("GET", url, headers=headers, timeout=60.0) as response:
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length and length.isdecimal():
                    validate_inbound_media_size(int(length), max_bytes=limit)
                async for chunk in response.aiter_bytes(chunk_size=262144):
                    total += len(chunk)
                    validate_inbound_media_size(total, max_bytes=limit)
                    await asyncio.to_thread(stream.write, chunk)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
        return total
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def copy_private_file(source: Path, destination: Path) -> int:
    import shutil
    private_directory(destination.parent)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
        shutil.copyfile(source, temporary)
        temporary.chmod(0o600)
        size = temporary.stat().st_size
        temporary.replace(destination)
        return size
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def download_avatar(app_id: str, sender_id: str, destination: Path) -> int:
    """Fetch only QQ's fixed HTTPS avatar service; never follow caller-controlled URLs.

    Local TUN proxies can resolve this CDN to benchmark fake IPs. The fixed host, TLS hostname
    verification and disabled redirects preserve the destination boundary without changing global
    URL safety or allowing private attachment URLs.
    """
    import httpx
    from urllib.parse import quote
    from gateway.platforms.base import gateway_trust_env

    url = "https://q.qlogo.cn/qqapp/" + quote(app_id, safe="") + "/" + quote(sender_id, safe="") + "/640"
    async with httpx.AsyncClient(trust_env=gateway_trust_env(), follow_redirects=False) as client:
        return await _stream_file(client, {}, url, destination)
