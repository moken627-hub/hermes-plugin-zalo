"""
Zalo Bot Platform Adapter for Hermes Agent.

A plugin-based gateway adapter that connects to Zalo via the Bot API
(bot.zaloplatforms.com) and relays messages between Zalo DMs and the
Hermes agent. Supports long-polling (default) and webhook mode.

Configuration in config.yaml:

    gateway:
      platforms:
        zalo:
          enabled: true
          extra:
            bot_token: "12345678:abc-def-xyz"
            dm_policy: "pairing"    # or "open", "allowlist"
            allowed_users: []       # empty = no restrictions (with dm_policy)
            webhook_url: ""         # optional, overrides long-polling
            webhook_secret: ""      # required if webhook_url is set
            webhook_port: 8443      # port for webhook listener

Or via environment variables (overrides config.yaml):
    ZALO_BOT_TOKEN, ZALO_ALLOWED_USERS, ZALO_ALLOW_ALL_USERS,
    ZALO_HOME_CHANNEL, ZALO_HOME_CHANNEL_NAME
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — Hermes gateway dependencies
# ---------------------------------------------------------------------------

from gateway.platforms.base import (
    BasePlatformAdapter,
    SendResult,
    MessageEvent,
    MessageType,
    cache_image_from_url,
)
from gateway.session import SessionSource
from gateway.config import PlatformConfig, Platform

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZALO_API_BASE = "https://bot-api.zaloplatforms.com/bot{}"
MAX_MESSAGE_LENGTH = 2000
POLLING_TIMEOUT = 30  # seconds for long-poll
POLLING_INTERVAL = 2  # seconds between polls when no updates
RECONNECT_DELAY = 5  # seconds before reconnecting after error
WEBHOOK_SECRET_HEADER = "X-Bot-Api-Secret-Token"

# ── Moderation constants ──────────────────────────────────────────
ANTI_SPAM_MAX_PER_MINUTE = 5
WARN_MAX_WARNINGS = 3
WARN_EXPIRY_SECONDS = 604800  # 1 week
CRM_DATA_DIR = os.environ.get(
    "ZALO_CRM_DIR",
    str(Path.home() / ".hermes" / "zalo-crm"),
)
HISTORY_DIR = os.environ.get(
    "ZALO_HISTORY_DIR",
    str(Path.home() / ".hermes" / "zalo-history"),
)

# ── Slash commands registry ───────────────────────────────────────
SLASH_COMMANDS = {
    "/noi-quy": {
        "description": "Quy định nhóm",
        "usage": "/noi-quy <text>",
        "admin_only": True,
    },
    "/menu": {
        "description": "Hiển thị menu lệnh",
        "usage": "/menu",
        "admin_only": False,
    },
    "/huong-dan": {
        "description": "Hướng dẫn sử dụng bot",
        "usage": "/huong-dan",
        "admin_only": False,
    },
    "/warn": {
        "description": "Cảnh báo thành viên",
        "usage": "/warn @user <reason>",
        "admin_only": True,
    },
    "/unwarn": {
        "description": "Xóa cảnh báo thành viên",
        "usage": "/unwarn @user",
        "admin_only": True,
    },
    "/report": {
        "description": "Báo cáo vi phạm",
        "usage": "/report @user <reason>",
        "admin_only": True,
    },
    "/rules": {
        "description": "Quy tắc nhóm",
        "usage": "/rules",
        "admin_only": False,
    },
    "/poll": {
        "description": "Tạo poll",
        "usage": "/poll <question> | option1, option2, ...",
        "admin_only": True,
    },
    "/pin": {
        "description": "Ghim tin nhắn",
        "usage": "/pin <message_id>",
        "admin_only": True,
    },
    "/unpin": {
        "description": "Bỏ ghim tin nhắn",
        "usage": "/unpin <message_id>",
        "admin_only": True,
    },
    "/invite": {
        "description": "Mời thành viên",
        "usage": "/invite @user1, @user2",
        "admin_only": True,
    },
    "/kick": {
        "description": "Kick thành viên",
        "usage": "/kick @user <reason>",
        "admin_only": True,
    },
    "/promote": {
        "description": "Thăng admin",
        "usage": "/promote @user",
        "admin_only": True,
    },
    "/demote": {
        "description": "Xoá quyền admin",
        "usage": "/demote @user",
        "admin_only": True,
    },
    "/info": {
        "description": "Thông tin nhóm",
        "usage": "/info",
        "admin_only": False,
    },
    "/mute": {
        "description": "Mute nhóm",
        "usage": "/mute",
        "admin_only": True,
    },
    "/unmute": {
        "description": "Unmute nhóm",
        "usage": "/unmute",
        "admin_only": True,
    },
    "/silent": {
        "description": "Silent mode (chỉ reply khi @tag)",
        "usage": "/silent",
        "admin_only": True,
    },
    "/welcome": {
        "description": "Bật/tắt welcome message",
        "usage": "/welcome",
        "admin_only": True,
    },
    "/welcome-text": {
        "description": "Đặt welcome text",
        "usage": "/welcome-text <nội dung>",
        "admin_only": True,
    },
    "/follow": {
        "description": "Bật/tắt group tracking",
        "usage": "/follow",
        "admin_only": True,
    },
    "/name-trigger": {
        "description": "Thêm name trigger",
        "usage": "/name-trigger <tên> <reply>",
        "admin_only": True,
    },
    "/settings": {
        "description": "Xem cài đặt nhóm",
        "usage": "/settings",
        "admin_only": False,
    },
}

# ---------------------------------------------------------------------------
# Zalo API Client (thin HTTP wrapper)
# ---------------------------------------------------------------------------


class _ZaloClient:
    """Async HTTP client for the Zalo Bot API using httpx."""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self._base = ZALO_API_BASE.format(bot_token)
        self._client = None

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _post(self, endpoint: str, data: dict = None) -> dict:
        """POST to an API endpoint and return parsed JSON."""
        await self._ensure_client()
        url = f"{self._base}/{endpoint}"
        resp = await self._client.post(url, json=data or {})
        resp.raise_for_status()
        return resp.json()

    async def get_me(self) -> dict:
        """Verify bot token and get bot info."""
        return await self._post("getMe")

    async def get_updates(self, timeout: int = POLLING_TIMEOUT) -> list:
        """Long-poll for new updates.

        Returns a list of update objects (handles both single-object and
        array response formats from the Zalo API).
        """
        result = await self._post("getUpdates", {"timeout": timeout})
        if not result.get("ok"):
            return []
        raw = result.get("result")
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            # Single object — wrap in list for uniform handling
            return [raw]
        return []

    async def send_message(self, chat_id: str, text: str) -> dict:
        """Send a text message (max 2000 chars)."""
        # Truncate if too long
        text = text[:MAX_MESSAGE_LENGTH]
        return await self._post("sendMessage", {"chat_id": chat_id, "text": text})

    async def send_photo(self, chat_id: str, photo_url: str, caption: str = "") -> dict:
        """Send a photo from a URL."""
        payload = {"chat_id": chat_id, "photo": photo_url}
        if caption:
            payload["caption"] = caption[:MAX_MESSAGE_LENGTH]
        return await self._post("sendPhoto", payload)

    async def send_chat_action(self, chat_id: str, action: str = "typing") -> dict:
        """Send a chat action indicator."""
        return await self._post("sendChatAction", {"chat_id": chat_id, "action": action})


# ---------------------------------------------------------------------------
# Moderation & Group Management Classes
# ---------------------------------------------------------------------------


class _AntiSpam:
    """Zero-token anti-spam detection."""

    def __init__(self) -> None:
        self._store: Dict[str, List[float]] = {}

    def check(self, group_id: str, user_id: str, text: str) -> Dict[str, Any]:
        now = time.time()
        key = f"{group_id}:{user_id}"
        if key not in self._store:
            self._store[key] = []
        self._store[key] = [t for t in self._store[key] if now - t < 60]
        self._store[key].append(now)
        if len(self._store[key]) > ANTI_SPAM_MAX_PER_MINUTE:
            return {"spam": True, "reason": "rate_limit_exceeded"}
        text_lower = text.lower()
        for pattern in ["bit.ly", "tinyurl.com", "t.me/", "tiktok.com",
                        "facebook.com/", "instagram.com/", "twitter.com/"]:
            if pattern in text_lower:
                return {"spam": True, "reason": "suspicious_link"}
        return {"spam": False, "reason": ""}


class _WarnSystem:
    """Zero-token warn tracking."""

    def __init__(self) -> None:
        self._store: Dict[str, List[Dict[str, Any]]] = {}

    def warn(self, group_id: str, user_id: str, reason: str = "") -> Dict[str, Any]:
        key = f"{group_id}:{user_id}"
        if key not in self._store:
            self._store[key] = []
        now = time.time()
        self._store[key] = [
            w for w in self._store[key]
            if now - w["timestamp"] < WARN_EXPIRY_SECONDS
        ]
        entry = {"timestamp": now, "reason": reason[:200],
                 "count": len(self._store[key]) + 1}
        self._store[key].append(entry)
        if entry["count"] >= WARN_MAX_WARNINGS:
            return {"ok": True, "action": "max_warnings_reached",
                    "warning": entry,
                    "message": f"⚠️ {user_id} đã đạt {WARN_MAX_WARNINGS} cảnh báo."}
        return {"ok": True, "action": "warned", "warning": entry}

    def get_warnings(self, group_id: str, user_id: str) -> List[Dict[str, Any]]:
        return self._store.get(f"{group_id}:{user_id}", [])

    def clear_warnings(self, group_id: str, user_id: str) -> Dict[str, Any]:
        self._store.pop(f"{group_id}:{user_id}", None)
        return {"ok": True, "action": "cleared"}


class _GroupManager:
    """Group management via Zalo Bot API."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def create_group(self, name: str, description: str = "",
                           join_type: str = "anyone",
                           admin_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"name": name[:100], "description": description[:500],
                                   "join_type": join_type}
        if admin_ids:
            payload["admin_ids"] = admin_ids[:20]
        return await self._client._post("createGroup", payload)

    async def kick_member(self, group_id: str, user_id: str,
                          reason: str = "") -> Dict[str, Any]:
        payload: Dict[str, Any] = {"group_id": group_id, "user_id": user_id}
        if reason:
            payload["reason"] = reason[:200]
        return await self._client._post("kickGroupMember", payload)

    async def promote_admin(self, group_id: str, user_id: str) -> Dict[str, Any]:
        return await self._client._post("promoteGroupAdmin",
                                        {"group_id": group_id, "user_id": user_id})

    async def demote_admin(self, group_id: str, user_id: str) -> Dict[str, Any]:
        return await self._client._post("demoteGroupAdmin",
                                        {"group_id": group_id, "user_id": user_id})

    async def invite_member(self, group_id: str, user_ids: List[str]) -> Dict[str, Any]:
        return await self._client._post("inviteGroupMember",
                                        {"group_id": group_id, "user_ids": user_ids[:50]})

    async def get_group_info(self, group_id: str) -> Dict[str, Any]:
        return await self._client._post("getGroupInfo", {"group_id": group_id})

    async def list_members(self, group_id: str, limit: int = 50) -> Dict[str, Any]:
        return await self._client._post("listGroupMembers",
                                        {"group_id": group_id, "limit": limit})

    async def set_announcement(self, group_id: str, text: str) -> Dict[str, Any]:
        return await self._client._post("setGroupAnnouncement",
                                        {"group_id": group_id, "text": text[:500]})

    async def create_poll(self, group_id: str, question: str,
                          options: List[str], expiry: int = 86400,
                          allow_multiple: bool = False) -> Dict[str, Any]:
        if len(options) < 2 or len(options) > 10:
            return {"ok": False, "error": "Poll requires 2-10 options"}
        payload = {"group_id": group_id, "question": question[:200],
                   "options": [o[:100] for o in options],
                   "expiry": expiry, "allow_multiple": allow_multiple}
        return await self._client._post("createPoll", payload)

    async def pin_message(self, group_id: str, message_id: str) -> Dict[str, Any]:
        return await self._client._post("pinGroupMessage",
                                        {"group_id": group_id, "message_id": message_id})

    async def unpin_message(self, group_id: str, message_id: str) -> Dict[str, Any]:
        return await self._client._post("unpinGroupMessage",
                                        {"group_id": group_id, "message_id": message_id})

    async def mute_group(self, group_id: str) -> Dict[str, Any]:
        """Mute a group (bot stops receiving notifications)."""
        return await self._client._post("muteGroup", {"group_id": group_id})

    async def unmute_group(self, group_id: str) -> Dict[str, Any]:
        """Unmute a group."""
        return await self._client._post("unmuteGroup", {"group_id": group_id})

    async def set_group_name(self, group_id: str, name: str) -> Dict[str, Any]:
        """Rename a group."""
        return await self._client._post("setGroupName",
                                        {"group_id": group_id, "name": name[:100]})

    async def set_group_avatar(self, group_id: str, avatar_url: str) -> Dict[str, Any]:
        """Set group avatar from URL."""
        return await self._client._post("setGroupAvatar",
                                        {"group_id": group_id, "avatar_url": avatar_url})

    async def get_group_invites(self, group_id: str) -> Dict[str, Any]:
        """Get pending invites for a group."""
        return await self._client._post("getGroupInvites", {"group_id": group_id})

    async def get_group_link(self, group_id: str) -> Dict[str, Any]:
        """Get group invite link."""
        return await self._client._post("getGroupLink", {"group_id": group_id})

    async def enable_group_link(self, group_id: str) -> Dict[str, Any]:
        """Enable group invite link."""
        return await self._client._post("enableGroupLink", {"group_id": group_id})

    async def disable_group_link(self, group_id: str) -> Dict[str, Any]:
        """Disable group invite link."""
        return await self._client._post("disableGroupLink", {"group_id": group_id})


class _GroupSettings:
    """Per-group toggle settings (muted, silent, welcome, follow)."""

    SETTINGS_FILE = os.environ.get(
        "ZALO_SETTINGS_DIR",
        str(Path.home() / ".hermes" / "zalo-settings"),
    )

    def __init__(self) -> None:
        self._dir = Path(self.SETTINGS_FILE)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _filepath(self, group_id: str) -> Path:
        return self._dir / f"{group_id}.json"

    def _load(self, group_id: str) -> Dict[str, Any]:
        filepath = self._filepath(group_id)
        if not filepath.exists():
            return {
                "muted": False,
                "silent": False,
                "welcome": False,
                "follow": False,
                "welcome_text": "",
                "name_triggers": [],
            }
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"muted": False, "silent": False, "welcome": False, "follow": False}

    def _save(self, group_id: str, settings: Dict[str, Any]) -> None:
        filepath = self._filepath(group_id)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)

    def get(self, group_id: str) -> Dict[str, Any]:
        return self._load(group_id)

    def set(self, group_id: str, key: str, value: Any) -> Dict[str, Any]:
        settings = self._load(group_id)
        settings[key] = value
        self._save(group_id, settings)
        return {"ok": True, "key": key, "value": value}

    def set_multiple(self, group_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        settings = self._load(group_id)
        settings.update(updates)
        self._save(group_id, settings)
        return {"ok": True, "updated": list(updates.keys())}

    def toggle(self, group_id: str, key: str) -> Dict[str, Any]:
        settings = self._load(group_id)
        current = settings.get(key, False)
        settings[key] = not current
        self._save(group_id, settings)
        return {"ok": True, "key": key, "value": settings[key]}


class _ChatHistorySync:
    """Synchronizes Zalo chat history for agent access."""

    def __init__(self) -> None:
        self._dir = Path(HISTORY_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._buffer: Dict[str, List[Dict[str, Any]]] = {}

    def save_message(self, group_id: str, message: Dict[str, Any]) -> None:
        if group_id not in self._buffer:
            self._buffer[group_id] = []
        entry = {
            "message_id": message.get("message_id", ""),
            "from_id": message.get("from_id", ""),
            "from_name": message.get("from_name", ""),
            "text": message.get("text", ""),
            "timestamp": message.get("timestamp", time.time()),
            "type": message.get("type", "text"),
        }
        self._buffer[group_id].append(entry)
        if len(self._buffer[group_id]) > 500:
            self.flush(group_id)

    def flush(self, group_id: str) -> int:
        if group_id not in self._buffer:
            return 0
        filepath = self._dir / f"{group_id}.jsonl"
        messages = self._buffer.pop(group_id, [])
        with open(filepath, "a", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return len(messages)

    def get_recent(self, group_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        filepath = self._dir / f"{group_id}.jsonl"
        if not filepath.exists():
            return []
        messages = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return messages[-limit:][::-1]

    def search(self, group_id: str, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        messages = self.get_recent(group_id, 10000)
        q = query.lower()
        return [m for m in messages if q in m.get("text", "").lower()][:limit]


class _CRMContacts:
    """CRM contact management for Zalo groups."""

    def __init__(self) -> None:
        self._dir = Path(CRM_DATA_DIR)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _filepath(self, group_id: str) -> Path:
        return self._dir / f"{group_id}_contacts.json"

    def _load(self, filepath: Path) -> Dict[str, Any]:
        if not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, filepath: Path, contacts: Dict[str, Any]) -> None:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)

    def add_contact(self, group_id: str, user_id: str, phone: str = "",
                    name: str = "", labels: Optional[List[str]] = None) -> Dict[str, Any]:
        filepath = self._filepath(group_id)
        contacts = self._load(filepath)
        contact = contacts.get(user_id, {})
        contact.update({"user_id": user_id, "phone": phone, "name": name,
                        "labels": labels or [], "updated_at": time.time()})
        contacts[user_id] = contact
        self._save(filepath, contacts)
        return {"ok": True, "contact": contact}

    def get_contact(self, group_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        return self._load(self._filepath(group_id)).get(user_id)

    def list_contacts(self, group_id: str, label: str = "") -> List[Dict[str, Any]]:
        contacts = list(self._load(self._filepath(group_id)).values())
        if label:
            contacts = [c for c in contacts if label in c.get("labels", [])]
        return sorted(contacts, key=lambda c: c.get("updated_at", 0), reverse=True)

    def import_csv(self, group_id: str, csv_path: str) -> Dict[str, Any]:
        import csv as _csv
        filepath = self._filepath(group_id)
        contacts = self._load(filepath)
        imported = 0
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                uid = row.get("user_id", "")
                if not uid:
                    continue
                c = contacts.get(uid, {})
                c.update({"user_id": uid, "phone": row.get("phone", ""),
                          "name": row.get("name", ""),
                          "labels": [l.strip() for l in row.get("labels", "").split(",") if l.strip()],
                          "updated_at": time.time()})
                contacts[uid] = c
                imported += 1
        self._save(filepath, contacts)
        return {"ok": True, "imported": imported}

    def export_csv(self, group_id: str, csv_path: str) -> Dict[str, Any]:
        import csv as _csv
        contacts = self._load(self._filepath(group_id))
        exported = 0
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=["user_id", "phone", "name", "labels"])
            w.writeheader()
            for c in contacts.values():
                w.writerow({"user_id": c.get("user_id", ""),
                            "phone": c.get("phone", ""),
                            "name": c.get("name", ""),
                            "labels": ",".join(c.get("labels", []))})
                exported += 1
        return {"ok": True, "exported": exported}


# ---------------------------------------------------------------------------
# Zalo Adapter
# ---------------------------------------------------------------------------


class ZaloAdapter(BasePlatformAdapter):
    """Async Zalo adapter implementing the BasePlatformAdapter interface.

    Connects to the Zalo Bot API via long-polling and relays DMs
    between Zalo users and the Hermes agent.
    """

    def __init__(self, config, **kwargs):
        platform = Platform("zalo")
        super().__init__(config=config, platform=platform)

        extra = getattr(config, "extra", {}) or {}

        # Bot token (env overrides config.yaml)
        self.bot_token = os.getenv("ZALO_BOT_TOKEN") or extra.get("bot_token", "")
        self.bot_token = self.bot_token.strip()
        self._redacted_token = self._redact_token(self.bot_token)

        # DM policy (env var overrides config.yaml)
        self.dm_policy = os.getenv("ZALO_DM_POLICY") or extra.get("dm_policy", "pairing")

        # Allowed users (authorisation)
        allowed_env = os.getenv("ZALO_ALLOWED_USERS", "").strip()
        if allowed_env:
            self.allowed_users: set = {u.strip() for u in allowed_env.split(",") if u.strip()}
        else:
            self.allowed_users: set = set(extra.get("allowed_users", []))

        allow_all_env = os.getenv("ZALO_ALLOW_ALL_USERS", "").strip().lower()
        if allow_all_env:
            self.allow_all = allow_all_env in ("1", "true", "yes")
        else:
            self.allow_all = extra.get("allow_all", True)

        # Webhook mode
        self.webhook_url = extra.get("webhook_url", "")
        self.webhook_secret = extra.get("webhook_secret", "")
        self.webhook_port = int(extra.get("webhook_port", 8443))

        # Runtime state
        self._client: Optional[_ZaloClient] = None
        self._poll_task: Optional[asyncio.Task] = None
        self._webhook_runner: Optional[asyncio.Task] = None
        self._bot_info: Optional[dict] = None
        self._bot_id: str = ""
        self._bot_name: str = ""
        self._pending_approvals: Dict[str, dict] = {}  # chat_id -> approval info

        # Moderation & group management instances
        self._anti_spam = _AntiSpam()
        self._warn_system = _WarnSystem()
        self._group_manager = _GroupManager(self._client)
        self._history_sync = _ChatHistorySync()
        self._crm = _CRMContacts()
        self._group_settings = _GroupSettings()

    def _redact_token(self, token: str) -> str:
        """Redact bot token for logging (show first 4 chars + last 4)."""
        if len(token) <= 12:
            return token[:4] + "..." + token[-4:] if len(token) > 8 else "***"
        return token[:4] + "..." + token[-4:]

    @property
    def name(self) -> str:
        return "Zalo"

    # ── Connection lifecycle ──────────────────────────────────────────────

    async def connect(self) -> bool:
        """Connect to Zalo Bot API: verify token and start polling/webhook."""
        if not self.bot_token:
            logger.error("Zalo: bot_token must be configured")
            self._set_fatal_error(
                "config_missing",
                "ZALO_BOT_TOKEN must be set",
                retryable=False,
            )
            return False

        self._client = _ZaloClient(self.bot_token)

        # Verify token
        try:
            me = await self._client.get_me()
            if not me.get("ok"):
                logger.error("Zalo: token verification failed: %s", me)
                self._set_fatal_error(
                    "auth_failed",
                    f"Zalo token rejected: {me}",
                    retryable=False,
                )
                return False
            self._bot_info = me.get("result", {})
            self._bot_id = str(self._bot_info.get("id", ""))
            self._bot_name = self._bot_info.get("account_name", "")
            logger.info(
                "Zalo: verified bot '%s' (id=%s, type=%s)",
                self._bot_info.get("account_name", "?"),
                self._bot_info.get("id", "?"),
                self._bot_info.get("account_type", "?"),
            )
        except Exception as e:
            logger.error("Zalo: token verification error: %s", e)
            self._set_fatal_error("auth_error", str(e), retryable=True)
            return False

        # Start receiving messages (polling or webhook)
        if self.webhook_url and self.webhook_secret:
            await self._start_webhook()
        else:
            await self._start_polling()

        self._mark_connected()
        logger.info(
            "Zalo: connected as '%s' (%s)",
            self._bot_info.get("account_name", "?"),
            "webhook" if self.webhook_url else "long-polling",
        )
        return True

    async def disconnect(self) -> None:
        """Stop polling/webhook and close the API client."""
        self._mark_disconnected()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None

        if self._webhook_runner and not self._webhook_runner.done():
            self._webhook_runner.cancel()
            try:
                await self._webhook_runner
            except asyncio.CancelledError:
                pass
        self._webhook_runner = None

        if self._client:
            await self._client.close()
            self._client = None

    # ── Polling mode ──────────────────────────────────────────────────────

    async def _start_polling(self) -> None:
        """Start the long-polling receive loop."""
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Continuously poll for updates."""
        last_update_id = 0
        consecutive_errors = 0

        while self.is_connected:
            try:
                updates = await self._client.get_updates(timeout=POLLING_TIMEOUT)

                if not updates:
                    await asyncio.sleep(POLLING_INTERVAL)
                    continue

                consecutive_errors = 0

                for update in updates:
                    event_name = update.get("event_name", "")
                    message = update.get("message") or update.get("result", {}).get("message", {})

                    if not message:
                        continue

                    await self._handle_incoming(event_name, message)

                    # Track the last update ID to avoid duplicates
                    mid = message.get("message_id", "") or update.get("message_id", "")
                    if mid:
                        try:
                            last_update_id = int(mid, 16)  # hex string
                        except (ValueError, TypeError):
                            pass

            except asyncio.CancelledError:
                raise
            except Exception as e:
                consecutive_errors += 1
                delay = min(RECONNECT_DELAY * consecutive_errors, 60)
                logger.warning(
                    "Zalo: poll error (attempt %d): %s — retrying in %ds",
                    consecutive_errors, e, delay,
                )
                await asyncio.sleep(delay)

    # ── Webhook mode ──────────────────────────────────────────────────────

    async def _start_webhook(self) -> None:
        """Start the webhook listener using aiohttp."""
        try:
            from aiohttp import web
        except ImportError:
            logger.error(
                "Zalo: webhook mode requires aiohttp. "
                "Install it with: pip install aiohttp"
            )
            # Fall back to polling
            logger.info("Zalo: falling back to long-polling")
            await self._start_polling()
            return

        app = web.Application()

        async def webhook_handler(request):
            # Verify secret token
            secret = request.headers.get(WEBHOOK_SECRET_HEADER, "")
            if secret != self.webhook_secret:
                return web.Response(status=403, text="Unauthorized")

            try:
                body = await request.json()
                if body.get("ok"):
                    result = body.get("result", {})
                    event_name = result.get("event_name", "")
                    message = result.get("message", {})
                    if message:
                        await self._handle_incoming(event_name, message)
                return web.json_response({"ok": True})
            except Exception as e:
                logger.warning("Zalo: webhook handler error: %s", e)
                return web.json_response({"ok": False, "error": str(e)}, status=500)

        app.router.add_post("/webhook/zalo", webhook_handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.webhook_port)
        await site.start()
        logger.info("Zalo: webhook listening on port %d", self.webhook_port)

        # Keep the task alive
        try:
            while self.is_connected:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        finally:
            await runner.cleanup()

    # ── Incoming message handling ─────────────────────────────────────────

    async def _handle_incoming(self, event_name: str, message: dict) -> None:
        """Process an incoming message from Zalo."""
        from_info = message.get("from", {})
        chat_info = message.get("chat", {})

        user_id = str(from_info.get("id", ""))
        user_name = from_info.get("display_name", user_id)
        chat_id = str(chat_info.get("id", user_id))
        chat_type = chat_info.get("chat_type", "PRIVATE")
        raw_text = (message.get("text") or message.get("caption") or "").strip()

        # Ignore bot's own messages
        if from_info.get("is_bot", False):
            return

        # Determine message type and extract text/media
        message_type = MessageType.TEXT
        media_url = None
        text = raw_text

        if event_name == "message.image.received":
            message_type = MessageType.IMAGE
            media_url = message.get("photo", "")
            if not text and message.get("caption"):
                text = message["caption"]
        elif event_name == "message.sticker.received":
            message_type = MessageType.STICKER
            text = message.get("sticker", "") or message.get("url", "") or "[Sticker]"
        elif event_name == "message.unsupported.received":
            text = "[Unsupported message type]"

        # ── Group message handling ──────────────────────────────────────
        is_group = chat_type == "GROUP"
        if is_group:
            # Check if bot is mentioned: @mention or reply to bot
            mentioned = False

            # 1. Check @mention (display_name)
            if self._bot_name and self._bot_name in text:
                mentioned = True

            # 2. Check reply
            if message.get("reply_to"):
                reply_to = message["reply_to"]
                if isinstance(reply_to, dict) and reply_to.get("from", {}).get("id") == self._bot_id:
                    mentioned = True

            if not mentioned:
                logger.debug("Zalo: ignoring group message — bot not mentioned")
                return

            # Strip bot mention from text
            if self._bot_name:
                text = text.replace(f"@{self._bot_name}", "").replace(f"{self._bot_name}", "").strip()

            # Auth check for group (check the user who sent it)
            if not self._is_user_authorized(user_id):
                logger.debug("Zalo: ignoring group message from unauthorized user %s", user_id)
                return

            # Dispatch group message
            await self._dispatch_message(
                text=text,
                chat_id=chat_id,
                user_id=user_id,
                user_name=user_name,
                message_type=message_type,
                media_url=media_url,
                chat_type="group",
            )
            return

        # ── DM handling ─────────────────────────────────────────────────
        # DM pairing approval flow (only for PRIVATE chats)
        if self.dm_policy == "pairing":
            if chat_id not in self._pending_approvals:
                # First contact — generate pairing code
                code = str(int(time.time()))[-6:]
                self._pending_approvals[chat_id] = {
                    "code": code,
                    "user_id": user_id,
                    "user_name": user_name,
                    "expires_at": time.time() + 3600,  # 1 hour
                }
                # Send pairing code
                await self._send_pairing_code(chat_id, code, user_name)
                return

            # Check if approved or still pending
            approval = self._pending_approvals[chat_id]
            if approval.get("code"):
                # Still pending — check if the message is the approval code
                text = (message.get("text") or "").strip()
                if text == approval["code"]:
                    # Approved! Remove pairing code
                    approval.pop("code", None)
                    await self._send_text(chat_id, f"✅ Xác thực thành công! Bạn có thể trò chuyện với bot.")
                    # Fall through to handle this message too (after approval)
                else:
                    await self._send_text(
                        chat_id,
                        f"⚠️ Vui lòng nhập mã xác thực **{approval['code']}** để bắt đầu trò chuyện."
                    )
                    return

            # Check if expired
            if approval.get("expires_at", 0) < time.time():
                self._pending_approvals.pop(chat_id, None)
                return

        # Auth check
        if not self._is_user_authorized(user_id):
            logger.debug("Zalo: ignoring message from unauthorized user %s", user_id)
            return

        # ── Slash command handling ──────────────────────────────
        slash = parse_slash_command(text)
        if slash and chat_type == "GROUP":
            await self._handle_slash_command(
                slash, chat_id, user_id, user_name, message_type, media_url, chat_type
            )
            return

        # ── Anti-spam check for group messages ──────────────────
        if is_group:
            spam = self._anti_spam.check(chat_id, user_id, text)
            if spam["spam"]:
                logger.debug("Zalo: spam blocked for %s in group %s", user_id, chat_id)
                return

        # Dispatch
        await self._dispatch_message(
            text=text,
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            message_type=message_type,
            media_url=media_url,
            chat_type=chat_type,
        )

    async def _handle_slash_command(
        self,
        slash: Dict[str, Any],
        chat_id: str,
        user_id: str,
        user_name: str,
        message_type: MessageType,
        media_url: Optional[str],
        chat_type: str = "dm",
    ) -> None:
        """Handle a slash command in a group chat."""
        cmd = slash["command"]
        args = slash["args"]
        spec = slash["spec"]

        # Admin-only check
        # For group commands, verify user is admin/owner in the group
        if spec["admin_only"] and chat_type == "GROUP":
            group_info = await self._group_manager.get_group_info(chat_id)
            members = group_info.get("result", {}).get("members", [])
            user_role = "member"
            for m in members:
                if str(m.get("user_id")) == str(user_id):
                    user_role = m.get("role", "member")
                    break
            if user_role not in ("owner", "admin"):
                await self._send_text(chat_id, "❌ Bạn không có quyền thực hiện lệnh này.")
                return
        elif spec["admin_only"] and not self._is_user_authorized(user_id):
            await self._send_text(chat_id, "❌ Bạn không có quyền thực hiện lệnh này.")
            return

        try:
            if cmd == "/menu":
                await self._send_text(chat_id, format_menu())
            elif cmd == "/rules":
                await self._send_text(chat_id, format_rules())
            elif cmd == "/huong-dan":
                await self._send_text(chat_id, format_help())
            elif cmd == "/info":
                info = await self._group_manager.get_group_info(chat_id)
                result = info.get("result", {})
                name = result.get("name", chat_id)
                members = result.get("member_count", 0)
                await self._send_text(
                    chat_id,
                    f"ℹ️ **{name}**\n👥 Thành viên: {members}\n📝 Nhóm ID: {chat_id}",
                )
            elif cmd == "/warn":
                target = args.strip().split(None, 1)
                if not target:
                    await self._send_text(chat_id, "⚠️ Dùng: `/warn @user <lí do>`")
                    return
                reason = target[1] if len(target) > 1 else "Chưa rõ lí do"
                result = self._warn_system.warn(chat_id, target[0].lstrip("@"), reason)
                msg = result.get("message", f"⚠️ Đã cảnh báo {target[0]}")
                await self._send_text(chat_id, msg)
            elif cmd == "/unwarn":
                target = args.strip().lstrip("@")
                self._warn_system.clear_warnings(chat_id, target)
                await self._send_text(chat_id, f"✅ Đã xóa cảnh báo cho {target}")
            elif cmd == "/report":
                target = args.strip().split(None, 1)
                if not target:
                    await self._send_text(chat_id, "⚠️ Dùng: `/report @user <lí do>`")
                    return
                reason = target[1] if len(target) > 1 else "Chưa rõ lí do"
                result = self._warn_system.warn(chat_id, target[0].lstrip("@"), f"REPORT: {reason}")
                await self._send_text(
                    chat_id,
                    f"📋 Đã báo cáo {target[0]}: {reason}",
                )
            elif cmd == "/kick":
                target = args.strip().split(None, 1)
                if not target:
                    await self._send_text(chat_id, "⚠️ Dùng: `/kick @user <lí do>`")
                    return
                reason = target[1] if len(target) > 1 else ""
                result = await self._group_manager.kick_member(chat_id, target[0].lstrip("@"), reason)
                if result.get("ok"):
                    await self._send_text(chat_id, f"✅ Đã kick {target[0]}")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/promote":
                target = args.strip().lstrip("@")
                result = await self._group_manager.promote_admin(chat_id, target)
                if result.get("ok"):
                    await self._send_text(chat_id, f"✅ {target} đã được thăng admin")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/demote":
                target = args.strip().lstrip("@")
                result = await self._group_manager.demote_admin(chat_id, target)
                if result.get("ok"):
                    await self._send_text(chat_id, f"✅ {target} đã bị xoá quyền admin")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/invite":
                targets = [u.strip().lstrip("@") for u in args.split(",") if u.strip()]
                if not targets:
                    await self._send_text(chat_id, "⚠️ Dùng: `/invite @user1, @user2`")
                    return
                result = await self._group_manager.invite_member(chat_id, targets)
                if result.get("ok"):
                    await self._send_text(chat_id, f"✅ Đã mời {len(targets)} thành viên")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/poll":
                parts = args.split("|")
                if len(parts) < 2:
                    await self._send_text(chat_id, "⚠️ Dùng: `/poll <câu hỏi> | lựa_chọn_1, lựa_chọn_2, ...`")
                    return
                question = parts[0].strip()
                options = [o.strip() for o in parts[1].split(",") if o.strip()]
                result = await self._group_manager.create_poll(chat_id, question, options)
                if result.get("ok"):
                    await self._send_text(chat_id, f"📊 Poll đã tạo: {question}")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/pin":
                msg_id = args.strip()
                if not msg_id:
                    await self._send_text(chat_id, "⚠️ Dùng: `/pin <message_id>`")
                    return
                result = await self._group_manager.pin_message(chat_id, msg_id)
                if result.get("ok"):
                    await self._send_text(chat_id, "📌 Đã ghim tin nhắn")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/unpin":
                msg_id = args.strip()
                if not msg_id:
                    await self._send_text(chat_id, "⚠️ Dùng: `/unpin <message_id>`")
                    return
                result = await self._group_manager.unpin_message(chat_id, msg_id)
                if result.get("ok"):
                    await self._send_text(chat_id, "📌 Đã bỏ ghim")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/noi-quy":
                announcement = args.strip()
                if not announcement:
                    await self._send_text(chat_id, "⚠️ Dùng: `/noi-quy <nội dung>`")
                    return
                result = await self._group_manager.set_announcement(chat_id, announcement)
                if result.get("ok"):
                    await self._send_text(chat_id, "📢 Đã đăng thông báo nhóm")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/mute":
                result = await self._group_manager.mute_group(chat_id)
                if result.get("ok"):
                    self._group_settings.set(chat_id, "muted", True)
                    await self._send_text(chat_id, "🔇 Đã mute nhóm")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/unmute":
                result = await self._group_manager.unmute_group(chat_id)
                if result.get("ok"):
                    self._group_settings.set(chat_id, "muted", False)
                    await self._send_text(chat_id, "🔊 Đã unmute nhóm")
                else:
                    await self._send_text(chat_id, f"❌ Lỗi: {result.get('error', 'Unknown')}")
            elif cmd == "/silent":
                settings = self._group_settings.get(chat_id)
                new_val = not settings.get("silent", False)
                self._group_settings.set(chat_id, "silent", new_val)
                status = "BẬT" if new_val else "TẮT"
                await self._send_text(chat_id, f"🔕 Silent mode {status} — {'chỉ reply khi @tag/gọi tên' if new_val else 'reply tất cả'}")
            elif cmd == "/welcome":
                settings = self._group_settings.get(chat_id)
                new_val = not settings.get("welcome", False)
                self._group_settings.set(chat_id, "welcome", new_val)
                status = "BẬT" if new_val else "TẮT"
                await self._send_text(chat_id, f"🎉 Welcome message {status}")
            elif cmd == "/follow":
                settings = self._group_settings.get(chat_id)
                new_val = not settings.get("follow", False)
                self._group_settings.set(chat_id, "follow", new_val)
                status = "BẬT" if new_val else "TẮT"
                await self._send_text(chat_id, f"📋 Group tracking {status}")
            elif cmd == "/welcome-text":
                text = args.strip()
                if not text:
                    await self._send_text(chat_id, "⚠️ Dùng: `/welcome-text <nội dung>`")
                    return
                self._group_settings.set(chat_id, "welcome_text", text)
                await self._send_text(chat_id, "✅ Đã cập nhật welcome text")
            elif cmd == "/name-trigger":
                parts = args.strip().split(None, 1)
                if len(parts) < 2:
                    await self._send_text(chat_id, "⚠️ Dùng: `/name-trigger <tên> <reply>`")
                    return
                name = parts[0]
                reply = parts[1]
                triggers = self._group_settings.get(chat_id).get("name_triggers", [])
                triggers.append({"name": name, "reply": reply})
                self._group_settings.set(chat_id, "name_triggers", triggers)
                await self._send_text(chat_id, f"✅ Đã thêm trigger: @{name} → {reply[:50]}")
            elif cmd == "/settings":
                settings = self._group_settings.get(chat_id)
                lines = [
                    "⚙️ **Cài đặt nhóm:**",
                    f"  🔇 Mute: {'BẬT' if settings.get('muted') else 'TẮT'}",
                    f"  🔕 Silent: {'BẬT' if settings.get('silent') else 'TẮT'}",
                    f"  🎉 Welcome: {'BẬT' if settings.get('welcome') else 'TẮT'}",
                    f"  📋 Follow: {'BẬT' if settings.get('follow') else 'TẮT'}",
                ]
                await self._send_text(chat_id, "\n".join(lines))
            else:
                await self._send_text(chat_id, f"❓ Không biết lệnh `{cmd}`. Dùng `/menu` để xem danh sách.")
        except Exception as e:
            logger.error("Zalo: slash command error: %s", e)
            await self._send_text(chat_id, f"❌ Lỗi xử lý lệnh: {str(e)[:100]}")

    async def _send_pairing_code(self, chat_id: str, code: str, user_name: str) -> None:
        """Send a pairing approval code to a new user."""
        welcome = (
            f"Xin chào {user_name}! 👋\n\n"
            f"Để xác thực và bắt đầu trò chuyện với bot, "
            f"vui lòng gửi mã xác thực sau:\n\n"
            f"**{code}**\n\n"
            f"Mã có hiệu lực trong 1 giờ."
        )
        await self._send_text(chat_id, welcome)

    def _is_user_authorized(self, user_id: str) -> bool:
        """Check if a user is allowed to interact with the bot."""
        if self.allow_all:
            return True
        if not self.allowed_users:
            return True
        return user_id in self.allowed_users

    async def _dispatch_message(
        self,
        text: str,
        chat_id: str,
        user_id: str,
        user_name: str,
        message_type: MessageType = MessageType.TEXT,
        media_url: str = None,
        chat_type: str = "dm",
    ) -> None:
        """Build a MessageEvent and hand it to the base class handler."""
        if not self._message_handler:
            return

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id if chat_type == "group" else user_name,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
        )

        event = MessageEvent(
            text=text,
            message_type=message_type,
            source=source,
            message_id=str(int(time.time() * 1000)),
            timestamp=datetime.now(),
        )

        await self.handle_message(event)

    # ── Sending ───────────────────────────────────────────────────────────

    async def _send_text(self, chat_id: str, text: str) -> SendResult:
        """Send a text message via the Zalo API."""
        if not self._client:
            return SendResult(success=False, error="Not connected")
        try:
            result = await self._client.send_message(chat_id, text)
            if result.get("ok"):
                msg_id = result.get("result", {}).get("message_id", "")
                return SendResult(success=True, message_id=msg_id)
            else:
                return SendResult(success=False, error=str(result))
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Send a text message to a Zalo chat (chunked at 2000 chars)."""
        if not self._client:
            return SendResult(success=False, error="Not connected")

        # Strip markdown that Zalo doesn't support (keep bold/italic)
        content = self._strip_markdown(content)

        # Split into chunks if too long
        chunks = [content[i:i + MAX_MESSAGE_LENGTH] for i in range(0, len(content), MAX_MESSAGE_LENGTH)]

        last_result = None
        for chunk in chunks:
            try:
                result = await self._client.send_message(chat_id, chunk)
                last_result = result
                await asyncio.sleep(0.3)
            except Exception as e:
                return SendResult(success=False, error=str(e))

        if last_result and last_result.get("ok"):
            msg_id = last_result.get("result", {}).get("message_id", "")
            return SendResult(success=True, message_id=msg_id)
        return SendResult(success=False, error=str(last_result or "Send failed"))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """Send typing indicator via the Zalo API."""
        if not self._client:
            return
        try:
            await self._client.send_chat_action(chat_id, "typing")
        except Exception:
            pass

    async def send_image(self, chat_id: str, image_url: str, caption: str = "") -> SendResult:
        """Send an image message."""
        if not self._client:
            return SendResult(success=False, error="Not connected")
        try:
            result = await self._client.send_photo(chat_id, image_url, caption)
            if result.get("ok"):
                msg_id = result.get("result", {}).get("message_id", "")
                return SendResult(success=True, message_id=msg_id)
            return SendResult(success=False, error=str(result))
        except Exception as e:
            return SendResult(success=False, error=str(e))

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get chat info."""
        return {
            "name": chat_id,
            "type": "dm",
        }

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Strip unsupported markdown, keep bold/italic.

        Zalo Bot API supports some markdown-like formatting.
        We strip complex formatting and keep the essentials.
        """
        import re
        # Images: ![text](url) → url
        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\2", text)
        # Links: [text](url) → text (url)
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
        # Preserve **bold** and *italic*
        return text


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def check_requirements() -> bool:
    """Check if Zalo is configured."""
    token = os.getenv("ZALO_BOT_TOKEN", "").strip()
    return bool(token)


def validate_config(config) -> bool:
    """Validate that the platform config has enough info to connect."""
    extra = getattr(config, "extra", {}) or {}
    token = os.getenv("ZALO_BOT_TOKEN") or extra.get("bot_token", "")
    return bool(token and token.strip())


def is_connected(config) -> bool:
    """Check whether Zalo is configured (env or config.yaml)."""
    extra = getattr(config, "extra", {}) or {}
    token = os.getenv("ZALO_BOT_TOKEN") or extra.get("bot_token", "")
    return bool(token and token.strip())


def interactive_setup() -> None:
    """Interactive `hermes gateway setup` flow for the Zalo platform."""
    from hermes_cli.setup import (
        prompt,
        prompt_yes_no,
        save_env_value,
        get_env_value,
        print_header,
        print_info,
        print_warning,
        print_success,
    )

    print_header("Zalo")
    existing_token = get_env_value("ZALO_BOT_TOKEN")
    if existing_token:
        print_info("Zalo: already configured")
        if not prompt_yes_no("Reconfigure Zalo?", False):
            return

    print_info("Connect Hermes to Zalo via the Zalo Bot Platform.")
    print_info("  1. Go to https://bot.zaloplatforms.com")
    print_info("  2. Create a bot and copy its token")
    print_info("  3. Enter the token below")
    print()

    token = prompt(
        "Zalo Bot Token (format: numeric_id:secret)",
        default=existing_token or "",
        password=True,
    )
    if not token:
        print_warning("Token is required — skipping Zalo setup")
        return
    save_env_value("ZALO_BOT_TOKEN", token.strip())

    if prompt_yes_no("Restrict access to specific user IDs?", False):
        users = prompt(
            "Allowed Zalo user IDs (comma-separated)",
            default=get_env_value("ZALO_ALLOWED_USERS") or "",
        )
        if users:
            save_env_value("ZALO_ALLOWED_USERS", users.strip())
        else:
            save_env_value("ZALO_ALLOWED_USERS", "")
    else:
        save_env_value("ZALO_ALLOW_ALL_USERS", "true")

    print()
    print_success("Zalo configuration saved to ~/.hermes/.env")
    print_info("Restart the gateway for changes to take effect: hermes gateway restart")


def _env_enablement() -> dict | None:
    """Seed PlatformConfig.extra from env vars during gateway config load."""
    token = os.getenv("ZALO_BOT_TOKEN", "").strip()
    if not token:
        return None
    seed: dict = {
        "bot_token": token,
    }
    home = os.getenv("ZALO_HOME_CHANNEL") or ""
    if home:
        seed["home_channel"] = {
            "chat_id": home,
            "name": os.getenv("ZALO_HOME_CHANNEL_NAME", home),
        }
    return seed


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Send a message via an ephemeral Zalo API client.

    Used for cron delivery when the gateway is not running in-process.
    """
    extra = getattr(pconfig, "extra", {}) or {}
    token = os.getenv("ZALO_BOT_TOKEN") or extra.get("bot_token", "")
    if not token:
        return {"error": "Zalo standalone send: ZALO_BOT_TOKEN must be configured"}

    client = _ZaloClient(token.strip())
    try:
        # Send media if provided
        if media_files:
            for media_url in media_files[:1]:  # only send first media
                caption = message[:MAX_MESSAGE_LENGTH] if message else ""
                result = await client.send_photo(chat_id, media_url, caption)
                if result.get("ok"):
                    return {"success": True, "message_id": result.get("result", {}).get("message_id", "")}

        # Send text
        result = await client.send_message(chat_id, message[:MAX_MESSAGE_LENGTH])
        if result.get("ok"):
            return {"success": True, "message_id": result.get("result", {}).get("message_id", "")}
        return {"error": str(result)}
    except Exception as e:
        return {"error": f"Zalo standalone send failed: {e}"}
    finally:
        await client.close()


def parse_slash_command(text: str) -> Optional[Dict[str, Any]]:
    """Parse a slash command from message text. Returns command dict or None."""
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(None, 1)
    command = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    if command not in SLASH_COMMANDS:
        return None
    return {"command": command, "args": args.strip(), "spec": SLASH_COMMANDS[command]}


def format_menu() -> str:
    """Format the command menu for display."""
    lines = ["📋 **Menu lệnh Zalo Bot:**\n"]
    for cmd, spec in SLASH_COMMANDS.items():
        visibility = "🔒 Admin" if spec["admin_only"] else "🌐 Mọi người"
        lines.append(f"  `{spec['usage']}` — {spec['description']} ({visibility})")
    return "\n".join(lines)


def format_rules() -> str:
    """Format default group rules."""
    return (
        "📜 **Quy tắc nhóm:**\n\n"
        "1. 🗣️ Lịch sự, không spam hay xúc phạm\n"
        "2. 🚫 Không chia sẻ link đáng ngờ\n"
        "3. 📌 Tuân thủ nội quy đã được thiết lập\n"
        "4. 💬 Sử dụng `/menu` để xem danh sách lệnh\n"
        "5. 🛡️ Vi phạm sẽ bị cảnh báo, 3 lần → xử lý\n\n"
        "_Quy tắc này được bot Zalo tự động thực thi._"
    )


def format_help() -> str:
    """Format help text."""
    return (
        "🤖 **Hướng dẫn sử dụng Zalo Bot:**\n\n"
        "Bot hỗ trợ các lệnh slash để quản lý nhóm:\n"
        "• `/menu` — Xem menu lệnh\n"
        "• `/rules` — Xem quy tắc nhóm\n"
        "• `/huong-dan` — Hướng dẫn chi tiết\n\n"
        "Admin còn có thêm:\n"
        "• `/warn @user <lí do>` — Cảnh báo\n"
        "• `/kick @user <lí do>` — Kick\n"
        "• `/promote @user` — Thăng admin\n"
        "• `/poll <câu hỏi> | opt1, opt2` — Tạo poll\n"
        "• `/pin <msg_id>` — Ghim tin nhắn\n\n"
        "Nhắn trực tiếp cho bot để bắt đầu cuộc trò chuyện."
    )


def register(ctx):
    """Plugin entry point: called by the Hermes plugin system."""
    ctx.register_platform(
        name="zalo",
        label="Zalo",
        adapter_factory=lambda cfg: ZaloAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["ZALO_BOT_TOKEN"],
        install_hint="No extra packages needed (uses httpx, already a Hermes dependency)",
        setup_fn=interactive_setup,
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="ZALO_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="ZALO_ALLOWED_USERS",
        allow_all_env="ZALO_ALLOW_ALL_USERS",
        max_message_length=MAX_MESSAGE_LENGTH,
        emoji="💬",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are chatting via Zalo. Zalo supports limited formatting "
            "— **bold** and *italic* work, but complex markdown is stripped. "
            "Messages are limited to 2000 characters per message "
            "(long messages are automatically split). "
            "Keep responses concise and conversational. "
            "The user is likely Vietnamese — respond in Vietnamese when appropriate."
        ),
    )
