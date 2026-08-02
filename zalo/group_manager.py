"""
Group Management for Hermes Zalo Plugin.

Provides group creation, kick, admin management, poll, pin, and invite
features using the Zalo Bot API. All operations are 0-token local commands
when triggered via slash commands — only @mention messages trigger AI.

Reference: https://bot.zaloplatforms.com/docs
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROUP_PERMISSIONS = ["owner", "admin", "member"]
GROUP_JOIN_TYPES = ["anyone", "admin_approval", "link"]
POLL_EXPIRY_OPTIONS = [60, 3600, 86400, 604800]  # 1min, 1hr, 1day, 1week
MAX_POLL_OPTIONS = 10
MAX_GROUP_NAME_LENGTH = 100
MAX_GROUP_DESCRIPTION_LENGTH = 500

# In-memory storage for group metadata (persists per adapter session)
_group_store: Dict[str, Dict[str, Any]] = {}
_warn_store: Dict[str, List[Dict[str, Any]]] = {}
_spam_store: Dict[str, List[float]] = {}


class GroupManager:
    """Manages Zalo group operations via the Bot API."""

    def __init__(self, client: Any) -> None:
        self._client = client

    # ── Group CRUD ──────────────────────────────────────────────────────

    async def create_group(
        self,
        name: str,
        description: str = "",
        join_type: str = "anyone",
        admin_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create a new Zalo group."""
        name = name[:MAX_GROUP_NAME_LENGTH]
        description = description[:MAX_GROUP_DESCRIPTION_LENGTH]

        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
            "join_type": join_type,
        }
        if admin_ids:
            payload["admin_ids"] = admin_ids[:20]  # Zalo limit

        try:
            result = await self._client._post("createGroup", payload)
            if result.get("ok"):
                group_id = str(result.get("result", {}).get("group_id", ""))
                _group_store[group_id] = {
                    "name": name,
                    "description": description,
                    "join_type": join_type,
                    "created_at": time.time(),
                    "created_by": self._client._bot_id,
                }
                logger.info("Zalo: group created — %s (id=%s)", name, group_id)
            return result
        except Exception as e:
            logger.error("Zalo: create_group failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def kick_member(
        self, group_id: str, user_id: str, reason: str = ""
    ) -> Dict[str, Any]:
        """Kick a member from a group."""
        payload = {"group_id": group_id, "user_id": user_id}
        if reason:
            payload["reason"] = reason[:200]
        try:
            return await self._client._post("kickGroupMember", payload)
        except Exception as e:
            logger.error("Zalo: kick_member failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def promote_admin(
        self, group_id: str, user_id: str
    ) -> Dict[str, Any]:
        """Promote a member to admin."""
        payload = {"group_id": group_id, "user_id": user_id}
        try:
            return await self._client._post("promoteGroupAdmin", payload)
        except Exception as e:
            logger.error("Zalo: promote_admin failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def demote_admin(
        self, group_id: str, user_id: str
    ) -> Dict[str, Any]:
        """Demote an admin to member."""
        payload = {"group_id": group_id, "user_id": user_id}
        try:
            return await self._client._post("demoteGroupAdmin", payload)
        except Exception as e:
            logger.error("Zalo: demote_admin failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def invite_member(
        self, group_id: str, user_ids: List[str]
    ) -> Dict[str, Any]:
        """Invite users to a group."""
        payload = {"group_id": group_id, "user_ids": user_ids[:50]}
        try:
            return await self._client._post("inviteGroupMember", payload)
        except Exception as e:
            logger.error("Zalo: invite_member failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def get_group_info(self, group_id: str) -> Dict[str, Any]:
        """Get group information."""
        try:
            return await self._client._post("getGroupInfo", {"group_id": group_id})
        except Exception as e:
            logger.error("Zalo: get_group_info failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def list_group_members(
        self, group_id: str, limit: int = 50
    ) -> Dict[str, Any]:
        """List members of a group."""
        try:
            return await self._client._post(
                "listGroupMembers", {"group_id": group_id, "limit": limit}
            )
        except Exception as e:
            logger.error("Zalo: list_group_members failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def set_group_announcement(
        self, group_id: str, text: str
    ) -> Dict[str, Any]:
        """Set group announcement (pinned message)."""
        payload = {"group_id": group_id, "text": text[:500]}
        try:
            return await self._client._post("setGroupAnnouncement", payload)
        except Exception as e:
            logger.error("Zalo: set_group_announcement failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def create_poll(
        self,
        group_id: str,
        question: str,
        options: List[str],
        expiry: int = 86400,
        allow_multiple: bool = False,
    ) -> Dict[str, Any]:
        """Create a poll in a group."""
        if len(options) < 2 or len(options) > MAX_POLL_OPTIONS:
            return {
                "ok": False,
                "error": f"Poll requires 2-{MAX_POLL_OPTIONS} options",
            }
        if expiry not in POLL_EXPIRY_OPTIONS:
            expiry = 86400  # default 1 day

        payload = {
            "group_id": group_id,
            "question": question[:200],
            "options": [o[:100] for o in options],
            "expiry": expiry,
            "allow_multiple": allow_multiple,
        }
        try:
            return await self._client._post("createPoll", payload)
        except Exception as e:
            logger.error("Zalo: create_poll failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def pin_message(
        self, group_id: str, message_id: str
    ) -> Dict[str, Any]:
        """Pin a message in a group."""
        payload = {"group_id": group_id, "message_id": message_id}
        try:
            return await self._client._post("pinGroupMessage", payload)
        except Exception as e:
            logger.error("Zalo: pin_message failed: %s", e)
            return {"ok": False, "error": str(e)}

    async def unpin_message(
        self, group_id: str, message_id: str
    ) -> Dict[str, Any]:
        """Unpin a message in a group."""
        payload = {"group_id": group_id, "message_id": message_id}
        try:
            return await self._client._post("unpinGroupMessage", payload)
        except Exception as e:
            logger.error("Zalo: unpin_message failed: %s", e)
            return {"ok": False, "error": str(e)}


# ── Warn System ──────────────────────────────────────────────────────────


class WarnSystem:
    """Zero-token moderation: warn tracking per user per group."""

    MAX_WARNINGS = 3
    WARN_EXPIRY = 604800  # 1 week

    def __init__(self) -> None:
        pass

    def warn(
        self, group_id: str, user_id: str, reason: str = ""
    ) -> Dict[str, Any]:
        """Add a warning to a user in a group."""
        key = f"{group_id}:{user_id}"
        if key not in _warn_store:
            _warn_store[key] = []

        # Clean expired warnings
        now = time.time()
        _warn_store[key] = [
            w for w in _warn_store[key] if now - w["timestamp"] < self.WARN_EXPIRY
        ]

        warning = {
            "timestamp": now,
            "reason": reason[:200],
            "count": len(_warn_store[key]) + 1,
        }
        _warn_store[key].append(warning)

        if warning["count"] >= self.MAX_WARNINGS:
            return {
                "ok": True,
                "action": "max_warnings_reached",
                "warning": warning,
                "message": f"⚠️ {user_id} đã đạt {self.MAX_WARNINGS} cảnh báo — cần xử lý.",
            }

        return {"ok": True, "action": "warned", "warning": warning}

    def get_warnings(
        self, group_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Get warnings for a user in a group."""
        key = f"{group_id}:{user_id}"
        return _warn_store.get(key, [])

    def clear_warnings(self, group_id: str, user_id: str) -> Dict[str, Any]:
        """Clear all warnings for a user in a group."""
        key = f"{group_id}:{user_id}"
        _warn_store.pop(key, None)
        return {"ok": True, "action": "cleared"}


# ── Anti-Spam ───────────────────────────────────────────────────────────


class AntiSpam:
    """Zero-token anti-spam detection."""

    MAX_MESSAGES_PER_MINUTE = 5
    MAX_IDENTICAL_MESSAGES = 3
    SPAM_LINK_PATTERNS = [
        "bit.ly", "tinyurl.com", "t.me/", "tiktok.com",
        "facebook.com/", "instagram.com/", "twitter.com/",
    ]

    def __init__(self) -> None:
        pass

    def check_spam(
        self, group_id: str, user_id: str, text: str
    ) -> Dict[str, Any]:
        """Check if a message looks like spam. Returns {spam: bool, reason: str}."""
        now = time.time()
        key = f"{group_id}:{user_id}"

        # Track message timestamps
        if key not in _spam_store:
            _spam_store[key] = []

        # Clean old entries (older than 1 minute)
        _spam_store[key] = [t for t in _spam_store[key] if now - t < 60]
        _spam_store[key].append(now)

        # Check rate limit
        if len(_spam_store[key]) > self.MAX_MESSAGES_PER_MINUTE:
            return {"spam": True, "reason": "rate_limit_exceeded"}

        # Check for spam links
        text_lower = text.lower()
        for pattern in self.SPAM_LINK_PATTERNS:
            if pattern in text_lower:
                return {"spam": True, "reason": "suspicious_link"}

        return {"spam": False, "reason": ""}


# ── Slash Command Parser ────────────────────────────────────────────────


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
}


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

    return {
        "command": command,
        "args": args.strip(),
        "spec": SLASH_COMMANDS[command],
    }


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
        "• `/warn @user <reason>` — Cảnh báo\n"
        "• `/kick @user <reason>` — Kick\n"
        "• `/promote @user` — Thăng admin\n"
        "• `/poll <question> | opt1, opt2` — Tạo poll\n"
        "• `/pin <msg_id>` — Ghim tin nhắn\n\n"
        "Nhắn trực tiếp cho bot để bắt đầu cuộc trò chuyện."
    )