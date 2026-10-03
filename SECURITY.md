# Security Policy 🔒

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | ✅ Active support  |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, please **do not** open a public issue.

Instead, **report privately** by emailing: **jarvis.hermes.ai@gmail.com**

### What to include:

- **Type** of vulnerability (e.g., token exposure, command injection, authentication bypass)
- **Affected version(s)**
- **Steps to reproduce**
- **Potential impact**
- **Suggested fix** (if you have one)

### Disclosure Timeline

- We will acknowledge receipt within **48 hours**
- We aim to triage within **5 business days**
- A fix will be coordinated with you before public disclosure
- Public disclosure happens after a fix is released (typically within 30 days)

## Security Considerations for This Plugin

### 🔐 Bot Token Security

- Your `ZALO_BOT_TOKEN` is a **secret credential** — treat it like a password
- Never commit it to version control
- Use environment variables or a secrets manager in production
- The raw token is replaced with `***` when an API error is surfaced (the
  token embedded in the request URL is scrubbed the same way)

### 🚫 Injection Prevention

- Incoming chat IDs are passed through to the Zalo Bot API as-is; they are
  restricted to `[A-Za-z0-9_-]` before being used to build local file paths
- Webhook mode validates the `X-Bot-Api-Secret-Token` header

### 📋 Access Control

- Default policy (`pairing`) is gated by Hermes core pairing: unknown DM users
  must be approved with `hermes pairing approve zalo <code>`; the adapter-side
  code sent on first contact is only a self-served captcha, not an approval
- Allowlist mode restricts access to known user IDs
- Unsupported message types are silently ignored (not forwarded to the agent)

### 🛡️ Hermes Agent Security

The plugin inherits Hermes Agent's security model:
- Messages are processed in isolated agent sessions
- No shell access is exposed via messaging
- All outbound calls use HTTPS with TLS

## Dependencies

| Dependency | Risk | Mitigation |
|------------|------|------------|
| httpx (already a Hermes dep) | Low | Actively maintained, HTTPS enforced |
| aiohttp (optional, webhook only) | Low | Actively maintained |
| Zalo Bot API | Medium | Token + TLS + webhook secret validation |
