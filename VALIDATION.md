# Validation — hermes-plugin-zalo

Recorded output of `hermes plugins validate` for the pinned release.

```text
✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — not declared
✓ config schema — not declared
✓ requires_env — all entries UPPER_SNAKE
✓ loadable — entry: __init__.py
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no tools to check
✗ security scan — dangerous: hardcoded_secret (README.md:133), 
hermes_config_mod_shell (README.md:75)

Validation failed.
```
