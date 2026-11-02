"""Test-wide isolation: point config/log dirs at a temp folder before any import."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="modbus-mcp-tests-"))
os.environ.setdefault("MODBUS_MCP_CONFIG_DIR", str(_ROOT / "config"))
os.environ.setdefault("MODBUS_MCP_LOG_DIR", str(_ROOT / "logs"))
os.environ.setdefault("MODBUS_WRITE_ENABLED", "true")
