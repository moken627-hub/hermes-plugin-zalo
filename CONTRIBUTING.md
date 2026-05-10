# Contributing to Hermes Zalo Plugin 🎉

First off, thank you for considering contributing! We welcome contributions from everyone.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Pull Request Guidelines](#pull-request-guidelines)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Issue Reporting](#issue-reporting)

## Code of Conduct

This project follows a **be excellent to each other** policy. Be respectful, inclusive, and constructive. Harassment, trolling, and personal attacks will not be tolerated.

## How Can I Contribute?

### 🐛 Report Bugs

- **Ensure the bug wasn't already reported** by searching [GitHub Issues](https://github.com/your-username/hermes-plugin-zalo/issues)
- If you can't find it, [open a new issue](.github/ISSUE_TEMPLATE/bug_report.md) with:
  - Clear title and description
  - Steps to reproduce
  - Expected vs actual behavior
  - Hermes Agent version (`hermes --version`)
  - Python version
  - Zalo Bot account type (Marketplace / OA)

### 💡 Suggest Features

- Open a [feature request](.github/ISSUE_TEMPLATE/feature_request.md)
- Explain *why* the feature is useful (not just *what*)
- Include examples of how it would work

### 🔀 Submit Pull Requests

1. Fork the repo
2. Create a feature branch (`git checkout -b feat/amazing-feature`)
3. Commit your changes
4. Push (`git push origin feat/amazing-feature`)
5. Open a Pull Request

## Development Setup

```bash
# 1. Clone the repo
git clone https://github.com/your-username/hermes-plugin-zalo.git
cd hermes-plugin-zalo

# 2. Symlink to Hermes plugins directory
ln -sf $(pwd)/zalo ~/.hermes/plugins/zalo

# 3. Verify import works
cd /path/to/hermes-agent
python -c "
import sys
sys.path.insert(0, '$(pwd)/zalo')
from adapter import ZaloAdapter, check_requirements, register
print('✅ Plugin imports OK')
"

# 4. Enable the plugin
hermes plugins enable zalo-platform
```

## Pull Request Guidelines

- **One feature/fix per PR** — keeps reviews focused
- **Write meaningful commit messages** following [Conventional Commits](https://www.conventionalcommits.org/):
  - `feat: add webhook message signing`
  - `fix: handle empty response from getUpdates`
  - `docs: update README with webhook setup`
  - `refactor: extract HTTP client from adapter`
- **Keep it small** — PRs under 300 lines are much easier to review
- **Update documentation** — if you add a feature, update the README
- **Add examples** — if you add a new capability, add an example

## Coding Standards

- **Python 3.11+** — use modern Python features
- **Type hints** — all functions must have type annotations
- **Async-first** — all I/O operations must be async
- **Error handling** — wrap external API calls in try/except with logging
- **Logging** — use `logger` from the `logging` module, not `print()`
- **Docstrings** — all public methods need docstrings explaining parameters and return values

### Style Guide

```python
# ✅ Good
async def send_message(self, chat_id: str, text: str) -> SendResult:
    """Send a text message via Zalo API."""
    ...

# ❌ Bad
async def send_message(self, chat_id, text):
    ...
```

## Testing

```bash
# Syntax check
python -m py_compile zalo/adapter.py

# Manual integration test (requires real token)
ZALO_BOT_TOKEN="your_token" python -c "
import asyncio
from adapter import ZaloAdapter, _ZaloClient

async def test():
    client = _ZaloClient('your_token')
    me = await client.get_me()
    print('Bot info:', me)

asyncio.run(test())
"
```

## Issue Reporting

When opening an issue, include:

1. **Environment**: Hermes version, Python version, OS
2. **Configuration**: How you configured the plugin (env vars / config.yaml)
3. **Logs**: Relevant logs from `~/.hermes/logs/errors.log` or `gateway.log`
4. **Steps to reproduce**: Clear, numbered steps

---

> **Questions?** Feel free to open a [Discussion](https://github.com/your-username/hermes-plugin-zalo/discussions)!
