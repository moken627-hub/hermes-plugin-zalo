"""
Moderation and CRM tools for Hermes Zalo Plugin.

Provides chat history sync, CRM contact management, and
zero-token moderation features for enterprise Zalo groups.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CRM_DATA_DIR = os.environ.get(
    "ZALO_CRM_DIR",
    str(Path.home() / ".hermes" / "zalo-crm"),
)
HISTORY_DIR = os.environ.get(
    "ZALO_HISTORY_DIR",
    str(Path.home() / ".hermes" / "zalo-history"),
)
MAX_HISTORY_MESSAGES = 10000
CRM_SYNC_INTERVAL = 3600  # 1 hour


class ChatHistorySync:
    """Synchronizes Zalo chat history for agent access."""

    def __init__(self) -> None:
        self._history_dir = Path(HISTORY_DIR)
        self._history_dir.mkdir(parents=True, exist_ok=True)
        self._message_buffer: Dict[str, List[Dict[str, Any]]] = {}

    def save_message(
        self,
        group_id: str,
        message: Dict[str, Any],
    ) -> None:
        """Save a message to the history buffer."""
        if group_id not in self._message_buffer:
            self._message_buffer[group_id] = []

        entry = {
            "message_id": message.get("message_id", ""),
            "from_id": message.get("from_id", ""),
            "from_name": message.get("from_name", ""),
            "text": message.get("text", ""),
            "timestamp": message.get("timestamp", time.time()),
            "type": message.get("type", "text"),
            "synced": False,
        }
        self._message_buffer[group_id].append(entry)

        # Flush if buffer too large
        if len(self._message_buffer[group_id]) > 500:
            self.flush(group_id)

    def flush(self, group_id: str) -> int:
        """Flush buffered messages to disk for a group."""
        if group_id not in self._message_buffer:
            return 0

        filepath = self._history_dir / f"{group_id}.jsonl"
        messages = self._message_buffer.pop(group_id, [])

        count = 0
        with open(filepath, "a", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
                count += 1

        logger.info("Zalo: flushed %d messages for group %s", count, group_id)
        return count

    def get_recent(
        self, group_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get recent messages from a group's history."""
        filepath = self._history_dir / f"{group_id}.jsonl"
        if not filepath.exists():
            return []

        messages = []
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    messages.append(msg)
                except json.JSONDecodeError:
                    continue

        # Return most recent first
        return messages[-limit:][::-1]

    def search(
        self, group_id: str, query: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Search messages in a group's history."""
        messages = self.get_recent(group_id, MAX_HISTORY_MESSAGES)
        query_lower = query.lower()
        results = [
            m for m in messages
            if query_lower in m.get("text", "").lower()
        ]
        return results[:limit]


class CRMContacts:
    """Manages Zalo contacts for CRM integration."""

    def __init__(self) -> None:
        self._crm_dir = Path(CRM_DATA_DIR)
        self._crm_dir.mkdir(parents=True, exist_ok=True)

    def _contacts_file(self, group_id: str) -> Path:
        return self._crm_dir / f"{group_id}_contacts.json"

    def add_contact(
        self,
        group_id: str,
        user_id: str,
        phone: str = "",
        name: str = "",
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add or update a contact."""
        filepath = self._contacts_file(group_id)
        contacts = self._load_contacts(filepath)

        contact = contacts.get(user_id, {})
        contact.update({
            "user_id": user_id,
            "phone": phone,
            "name": name,
            "labels": labels or [],
            "updated_at": time.time(),
        })
        contacts[user_id] = contact

        self._save_contacts(filepath, contacts)
        return {"ok": True, "contact": contact}

    def get_contact(
        self, group_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a contact by user ID."""
        filepath = self._contacts_file(group_id)
        contacts = self._load_contacts(filepath)
        return contacts.get(user_id)

    def list_contacts(
        self, group_id: str, label: str = ""
    ) -> List[Dict[str, Any]]:
        """List all contacts, optionally filtered by label."""
        filepath = self._contacts_file(group_id)
        contacts = self._load_contacts(filepath)

        result = list(contacts.values())
        if label:
            result = [c for c in result if label in c.get("labels", [])]

        return sorted(result, key=lambda c: c.get("updated_at", 0), reverse=True)

    def search_by_phone(
        self, group_id: str, phone: str
    ) -> Optional[Dict[str, Any]]:
        """Search contacts by phone number."""
        filepath = self._contacts_file(group_id)
        contacts = self._load_contacts(filepath)

        for c in contacts.values():
            if c.get("phone") == phone:
                return c
        return None

    def import_csv(
        self, group_id: str, csv_path: str
    ) -> Dict[str, Any]:
        """Import contacts from CSV (phone,name,label)."""
        import csv as csv_mod  # noqa: F811

        filepath = self._contacts_file(group_id)
        contacts = self._load_contacts(filepath)
        imported = 0

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                user_id = row.get("user_id", "")
                if not user_id:
                    continue
                contact = contacts.get(user_id, {})
                contact.update({
                    "user_id": user_id,
                    "phone": row.get("phone", ""),
                    "name": row.get("name", ""),
                    "labels": [
                        l.strip()
                        for l in row.get("labels", "").split(",")
                        if l.strip()
                    ],
                    "updated_at": time.time(),
                })
                contacts[user_id] = contact
                imported += 1

        self._save_contacts(filepath, contacts)
        return {"ok": True, "imported": imported}

    def export_csv(
        self, group_id: str, csv_path: str
    ) -> Dict[str, Any]:
        """Export contacts to CSV."""
        import csv as csv_mod  # noqa: F811

        filepath = self._contacts_file(group_id)
        contacts = self._load_contacts(filepath)
        exported = 0

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv_mod.DictWriter(
                f, fieldnames=["user_id", "phone", "name", "labels"]
            )
            writer.writeheader()
            for c in contacts.values():
                writer.writerow({
                    "user_id": c.get("user_id", ""),
                    "phone": c.get("phone", ""),
                    "name": c.get("name", ""),
                    "labels": ",".join(c.get("labels", [])),
                })
                exported += 1

        return {"ok": True, "exported": exported}

    def _load_contacts(self, filepath: Path) -> Dict[str, Any]:
        """Load contacts from JSON file."""
        if not filepath.exists():
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_contacts(
        self, filepath: Path, contacts: Dict[str, Any]
    ) -> None:
        """Save contacts to JSON file."""
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(contacts, f, ensure_ascii=False, indent=2)


class ModerationEngine:
    """Zero-token moderation engine combining anti-spam and warn system."""

    def __init__(self) -> None:
        from .group_manager import AntiSpam, WarnSystem
        self.anti_spam = AntiSpam()
        self.warn_system = WarnSystem()

    def process_moderation(
        self, group_id: str, user_id: str, text: str
    ) -> Dict[str, Any]:
        """Process a message through moderation pipeline."""
        # Step 1: Anti-spam check
        spam_result = self.anti_spam.check_spam(group_id, user_id, text)
        if spam_result["spam"]:
            return {
                "action": "block",
                "reason": spam_result["reason"],
                "message": "⚠️ Tin nhắn bị chặn do vi phạm quy tắc anti-spam.",
            }

        # Step 2: Check warnings
        warnings = self.warn_system.get_warnings(group_id, user_id)
        if len(warnings) >= 3:
            return {
                "action": "flag",
                "reason": "max_warnings",
                "message": f"⚠️ User đã có {len(warnings)} cảnh báo.",
            }

        return {"action": "allow", "reason": ""}