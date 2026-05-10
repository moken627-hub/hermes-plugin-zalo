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
import time
from datetime import datetime
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

        # Dispatch
        await self._dispatch_message(
            text=text,
            chat_id=chat_id,
            user_id=user_id,
            user_name=user_name,
            message_type=message_type,
            media_url=media_url,
            chat_type="dm",
        )

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
