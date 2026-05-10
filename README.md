# Hermes Zalo Plugin 🤖💬

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Hermes Agent](https://img.shields.io/badge/Hermes%20Agent-≥0.13.0-blue)](https://github.com/NousResearch/hermes-agent)
[![Python](https://img.shields.io/badge/Python-3.11+-green)](https://www.python.org/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen)](CONTRIBUTING.md)
[![Zalo Bot Platform](https://img.shields.io/badge/Zalo-Bot%20API-blue)](https://bot.zaloplatforms.com)

> Kết nối Hermes Agent với Zalo — Vietnam's leading messaging platform.
> Connect Hermes Agent to Zalo Bot Platform via official Bot API.

---

## 📦 Overview

A **platform plugin** for [Hermes Agent](https://github.com/NousResearch/hermes-agent) that adds Zalo messaging support. Built as a drop-in plugin — zero modifications to Hermes core required.

### ✨ Features

| Feature | Status |
|---------|--------|
| 📨 Send/receive text messages (2000-char chunks) | ✅ |
| 🖼️ Send photos from URL | ✅ |
| ⌨️ Typing indicator (`sendChatAction`) | ✅ |
| 🔄 Long-polling (`getUpdates`) | ✅ |
| 🌐 Webhook mode (via aiohttp) | ✅ |
| 🔐 DM Pairing approval (code-based) | ✅ |
| 📋 Allowlist / Open access policies | ✅ |
| 📬 Cron delivery (`deliver=zalo`) | ✅ |
| ⚙️ Interactive setup wizard (`hermes gateway setup`) | ✅ |
| 📦 Zero extra dependencies (uses httpx, already a Hermes dep) | ✅ |

### 🗺️ Roadmap

- [ ] Group chat support (when Zalo Bot Platform enables it)
- [ ] Sticker sending/receiving
- [ ] Image/file upload (multipart)
- [ ] Voice message support

---

## 🚀 Quick Start

### 1. Install the plugin

```bash
# Clone to user plugins directory
git clone https://github.com/moken627-hub/hermes-plugin-zalo.git \
    ~/.hermes/plugins/zalo
```

### 2. Create a Zalo Bot

1. Go to [https://bot.zaloplatforms.com](https://bot.zaloplatforms.com)
2. Sign in with your Zalo account
3. Create a new bot and copy the **Bot Token** (format: `numeric_id:secret`)

### 3. Configure

```bash
# Option A: Environment variable (recommended)
export ZALO_BOT_TOKEN="your_bot_token_here"

# Option B: Interactive setup
hermes gateway setup zalo

# Option C: config.yaml
cat >> ~/.hermes/config.yaml << 'EOF'
gateway:
  platforms:
    zalo:
      enabled: true
      extra:
        bot_token: "your_bot_token_here"
        dm_policy: "pairing"   # "pairing" | "open" | "allowlist"
EOF
```

### 4. Enable & restart

```bash
hermes plugins enable zalo-platform
hermes gateway restart
```

### 5. Verify

```bash
hermes gateway status
# You should see Zalo listed with your bot name
```

---

## 🔧 Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ZALO_BOT_TOKEN` | ✅ | Bot token from bot.zaloplatforms.com |
| `ZALO_ALLOWED_USERS` | ❌ | Comma-separated user IDs (require `allowlist` policy) |
| `ZALO_ALLOW_ALL_USERS` | ❌ | `true` to allow all users |
| `ZALO_HOME_CHANNEL` | ❌ | Default Zalo chat ID for cron delivery |

### Access Policies

| Policy | Description |
|--------|-------------|
| `pairing` (default) | New users receive a 6-digit code to approve access. Code expires in 1 hour. |
| `open` | Any user can message the bot |
| `allowlist` | Only users in `allowed_users` can message |

### Webhook Mode (Production)

For production, use webhook instead of long-polling:

```yaml
gateway:
  platforms:
    zalo:
      enabled: true
      extra:
        bot_token: "your_token"
        webhook_url: "https://your-domain.com/webhook/zalo"
        webhook_secret: "your-secret-16-256-chars"
        webhook_port: 8443      # optional, default 8443
```

Then configure your webhook URL in the Zalo Bot Platform dashboard.

---

## 📁 Project Structure

```
hermes-plugin-zalo/
├── zalo/
│   ├── __init__.py          # Plugin entry point (exports register)
│   ├── adapter.py           # Full Zalo adapter implementation
│   └── plugin.yaml          # Plugin metadata & config schema
├── examples/
│   └── config.yaml          # Example configuration
├── .github/
│   └── ISSUE_TEMPLATE/
│       ├── bug_report.md
│       └── feature_request.md
├── CONTRIBUTING.md          # Contribution guidelines
├── SECURITY.md              # Security policy
├── LICENSE                  # MIT License
└── README.md                # This file
```

---

## 🧩 API Reference

The plugin implements the Zalo Bot Platform REST API:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `getMe` | POST | Verify bot token |
| `getUpdates` | POST | Long-poll for new messages |
| `sendMessage` | POST | Send text (max 2000 chars) |
| `sendPhoto` | POST | Send image from URL |
| `sendChatAction` | POST | Show typing indicator |
| `setWebhook` | POST | Configure webhook URL |
| `deleteWebhook` | POST | Remove webhook config |

Full API docs: [bot.zapps.me/docs](https://bot.zapps.me/docs/)

---

## 🧪 Development

```bash
# Install in editable mode for development
ln -s $(pwd)/zalo ~/.hermes/plugins/zalo

# Run syntax check
python -m py_compile zalo/adapter.py

# Test import
cd /path/to/hermes-agent
python -c "from gateway.platforms.base import BasePlatformAdapter; print('OK')"
```

---

## 📝 Changelog

### v1.0.0 (2026-05-10)

- Initial release
- Text messaging (send/receive)
- Photo sending
- Typing indicator
- Long-polling & webhook modes
- DM pairing approval
- Allowlist/Open access policies
- Cron delivery support
- Interactive setup wizard

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) first.

- 🐛 Found a bug? [Open an issue](.github/ISSUE_TEMPLATE/bug_report.md)
- 💡 Have an idea? [Submit a feature request](.github/ISSUE_TEMPLATE/feature_request.md)
- 🔀 Want to contribute? Send a PR!

---

## 📄 License

[MIT](LICENSE) © 2026 Moken

---

## 🙏 Acknowledgements

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — The open-source AI agent that grows with you
- [Zalo Bot Platform](https://bot.zaloplatforms.com) — Zalo's official bot API
- [Nous Research](https://nousresearch.com/) — Creator of Hermes Agent
