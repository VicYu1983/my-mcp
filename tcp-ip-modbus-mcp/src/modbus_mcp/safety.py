"""Safety gate: whitelist matching, temporary authorization, write guard, audit.

Design notes
------------
MCP cannot hard-stop a model from calling a tool, so the gate is layered:

1. Nothing outside the whitelist (or `devices.json`) is ever contacted.
2. An unauthorised target produces a *request for authorization* instead of a
   connection attempt. The model has to relay that to you.
3. Approval is temporary by default (in-memory, TTL, never written to disk).
   Permanent approval must carry the user's own consent text and is audited.
4. Every write is audited with the consent text and read back for verification.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass

from .config import ConfigStore, Device, get_store

DEFAULT_PORT = 502
DEFAULT_TEMP_TTL = 600
MAX_TEMP_TTL = 24 * 3600
FRAMERS = ("tcp", "rtu")


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    unit: int
    framer: str
    alias: str | None = None
    source: str = "argument"
    data_type: str | None = None

    @property
    def label(self) -> str:
        base = f"{self.alias} {self.host}:{self.port}" if self.alias else f"{self.host}:{self.port}"
        return f"{base} unit={self.unit} ({self.framer})"

    def to_json(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "unit": self.unit,
            "framer": self.framer,
            "alias": self.alias,
        }


@dataclass
class _TempAuth:
    request_id: str
    host: str
    port: int
    expires_at: float
    granted_at: float


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


class Gate:
    """Whitelist + temporary authorisation bookkeeping."""

    def __init__(self, store: ConfigStore | None = None, *,
                 resolve_hostnames: bool = True,
                 temp_ttl_default: int = DEFAULT_TEMP_TTL) -> None:
        self.store = store or get_store()
        self.resolve_hostnames = resolve_hostnames
        self.temp_ttl_default = temp_ttl_default
        self._lock = threading.RLock()
        self._temp: dict[str, _TempAuth] = {}
        self._pending: dict[str, dict] = {}
        self._pending_by_key: dict[tuple[str, int], str] = {}
        self._dns_cache: dict[str, tuple[float, list[str]]] = {}
        self._dns_ttl = 60.0

    # -- DNS ---------------------------------------------------------------
    def resolve_ips(self, host: str) -> list[str]:
        if _is_ip(host) or not self.resolve_hostnames:
            return []
        now = time.time()
        cached = self._dns_cache.get(host)
        if cached and cached[0] > now:
            return cached[1]
        ips: list[str] = []
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
            ips = sorted({info[4][0] for info in infos})
        except OSError:
            ips = []
        self._dns_cache[host] = (now + self._dns_ttl, ips)
        return ips

    # -- whitelist matching ------------------------------------------------
    @staticmethod
    def _host_matches(entry_target: str, host: str, resolved: list[str]) -> bool:
        target = entry_target.strip()
        if "/" in target:
            try:
                network = ipaddress.ip_network(target, strict=False)
            except ValueError:
                return False
            candidates = ([host] if _is_ip(host) else []) + resolved
            for candidate in candidates:
                try:
                    if ipaddress.ip_address(candidate) in network:
                        return True
                except ValueError:
                    continue
            return False
        if _is_ip(target):
            return target == host or target in resolved
        return target.lower() == host.lower()

    def match_whitelist(self, host: str, port: int, framer: str | None = None):
        """Return the matching whitelist entry, or None."""
        resolved = self.resolve_ips(host)
        for entry in self.store.whitelist():
            if not self._host_matches(entry.target, host, resolved):
                continue
            if entry.ports and port not in entry.ports:
                continue
            if entry.protocol and framer and entry.protocol != framer:
                continue
            return entry
        return None

    # -- temporary authorisations -----------------------------------------
    def temp_auth_for(self, host: str, port: int) -> _TempAuth | None:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._temp.items() if v.expires_at <= now]
            for key in expired:
                self._temp.pop(key, None)
            for auth in self._temp.values():
                if auth.host == host and auth.port == port:
                    return auth
        return None

    def list_temp_auths(self) -> list[dict]:
        now = time.time()
        with self._lock:
            return [
                {
                    "request_id": a.request_id,
                    "host": a.host,
                    "port": a.port,
                    "expires_in_seconds": max(0, int(a.expires_at - now)),
                }
                for a in self._temp.values() if a.expires_at > now
            ]

    def _pending_payload(self, host: str, port: int, framer: str, unit: int,
                         purpose: str, reason: str) -> dict:
        key = (host, port)
        with self._lock:
            existing = self._pending_by_key.get(key)
            if existing and existing in self._pending:
                request_id = existing
            else:
                request_id = f"auth-{uuid.uuid4().hex[:8]}"
                self._pending[request_id] = {
                    "request_id": request_id,
                    "host": host,
                    "port": port,
                    "framer": framer,
                    "unit": unit,
                    "purpose": purpose,
                    "requested_at": time.time(),
                }
                self._pending_by_key[key] = request_id
            info = dict(self._pending[request_id])
        info["options"] = ["temporary", "permanent"]
        info["default_ttl_seconds"] = self.temp_ttl_default
        info["reason"] = reason
        return info

    def authorize_temporary(self, request_id: str, ttl_seconds: int | None = None,
                            purpose: str = "probe") -> dict:
        ttl = int(ttl_seconds or self.temp_ttl_default)
        ttl = max(30, min(ttl, MAX_TEMP_TTL))
        with self._lock:
            info = self._pending.pop(request_id, None)
            if info is None:
                # allow a direct authorisation by host:port form
                raise KeyError(request_id)
            self._pending_by_key.pop((info["host"], info["port"]), None)
            auth = _TempAuth(request_id=request_id, host=info["host"], port=info["port"],
                             expires_at=time.time() + ttl, granted_at=time.time())
            self._temp[request_id] = auth
        self.store.audit({
            "event": "authorize_temporary",
            "request_id": request_id,
            "host": info["host"],
            "port": info["port"],
            "framer": info["framer"],
            "unit": info["unit"],
            "purpose": purpose,
            "ttl_seconds": ttl,
        })
        return {**info, "expires_in_seconds": ttl}

    # -- write guard -------------------------------------------------------
    def writes_enabled(self) -> bool:
        raw = os.environ.get("MODBUS_WRITE_ENABLED", "true").strip().lower()
        return raw not in ("0", "false", "no", "off")


# -- target resolution ------------------------------------------------------
def resolve_target(gate: Gate, target: str, *, port: int | None = None,
                   unit: int | None = None, framer: str | None = None,
                   data_type: str | None = None, purpose: str = "probe",
                   ) -> tuple[Target | None, dict | None]:
    """Resolve an alias/host into a vetted Target.

    Returns (target, None) when allowed, or (None, refusal) when the target is
    unknown/unauthorised — the refusal payload is meant to be shown to the user.
    """
    from .models import failure

    raw = (target or "").strip()
    if not raw:
        return None, failure("invalid_argument", "沒有指定目標（target 不可為空）。",
                             hint="可用 host（例如 192.168.1.10）或 devices.json 的別名。")

    want_framer = framer.lower() if framer else None
    if want_framer is not None and want_framer not in FRAMERS:
        return None, failure(
            "invalid_argument", f"framer 只能是 'tcp' 或 'rtu'，收到 {framer!r}。",
            hint="Modbus TCP 用 'tcp'，Modbus RTU over TCP 用 'rtu'。")

    device: Device | None = gate.store.find_device(raw)
    if device is not None:
        resolved_framer = want_framer or device.framer
        resolved_unit = unit if unit is not None else device.unit
        resolved_port = port or device.port
        return Target(host=device.host, port=resolved_port, unit=resolved_unit,
                      framer=resolved_framer, alias=device.alias,
                      source="devices.json",
                      data_type=data_type or device.data_type), None

    host = raw
    resolved_port = port or DEFAULT_PORT
    resolved_unit = unit if unit is not None else 1
    resolved_framer = want_framer or "tcp"

    if not (0 < resolved_port < 65536):
        return None, failure("invalid_argument", f"port 不合法：{resolved_port}",
                             hint="TCP port 需介於 1..65535。")
    if not (0 <= resolved_unit <= 247):
        return None, failure("invalid_argument", f"unit id 不合法：{resolved_unit}",
                             hint="Modbus unit id 需介於 0..247（常用 1）。")

    entry = gate.match_whitelist(host, resolved_port, resolved_framer)
    if entry is not None:
        return Target(host=host, port=resolved_port, unit=resolved_unit,
                      framer=resolved_framer, source=f"whitelist:{entry.target}",
                      data_type=data_type), None

    temp = gate.temp_auth_for(host, resolved_port)
    if temp is not None:
        return Target(host=host, port=resolved_port, unit=resolved_unit,
                      framer=resolved_framer, source=f"temporary:{temp.request_id}",
                      data_type=data_type), None

    info = gate._pending_payload(
        host, resolved_port, resolved_framer, resolved_unit, purpose,
        reason=f"{host}:{resolved_port} 不在白名單內")

    refusal = failure(
        "not_whitelisted",
        f"{host}:{resolved_port} 不在白名單內，已停止操作、未建立任何連線。"
        f"要授權我連線嗎？一次性（{info['default_ttl_seconds']} 秒）或永久加入白名單？",
        hint=("使用者同意後，呼叫 authorize_temporary 套用一次性授權，"
              "或用 whitelist_add 永久加入（需帶使用者同意文字）。"),
        needs_authorization=info,
        target=f"{host}:{resolved_port}",
    )
    return None, refusal
