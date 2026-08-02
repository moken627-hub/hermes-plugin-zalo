"""
Hermes Zalo Plugin — root package wrapper.

Hermes scans `<plugins>/<plugin-name>/plugin.yaml` at depth 1. The actual
implementation lives in the ``zalo`` subpackage (adapter, group manager,
moderation, CRM, history sync). This root ``__init__.py`` re-exports the
plugin entry point so the gateway can discover and load it.
"""

__version__ = "2.1.0"

from .zalo.adapter import register

__all__ = ["register"]
