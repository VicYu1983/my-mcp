"""Whitelist matching, temporary authorisation and config hot reload."""

from __future__ import annotations

import json

from modbus_mcp.config import ConfigStore
from modbus_mcp.safety import Gate, resolve_target


def _store(tmp_path, entries) -> ConfigStore:
    store = ConfigStore(tmp_path / "config", tmp_path / "logs")
    store.whitelist_path.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    return store


def _entry(target, ports=None, protocol=None):
    return {"target": target, "ports": ports, "protocol": protocol, "note": ""}


def test_exact_ip_match(tmp_path):
    store = _store(tmp_path, [_entry("192.168.1.10", [502])])
    gate = Gate(store, resolve_hostnames=False)
    assert gate.match_whitelist("192.168.1.10", 502) is not None
    assert gate.match_whitelist("192.168.1.10", 1502) is None
    assert gate.match_whitelist("192.168.1.11", 502) is None


def test_cidr_match(tmp_path):
    store = _store(tmp_path, [_entry("192.168.1.0/24")])
    gate = Gate(store, resolve_hostnames=False)
    assert gate.match_whitelist("192.168.1.77", 502) is not None
    assert gate.match_whitelist("192.168.2.77", 502) is None


def test_protocol_restriction(tmp_path):
    store = _store(tmp_path, [_entry("10.0.0.5", protocol="rtu")])
    gate = Gate(store, resolve_hostnames=False)
    assert gate.match_whitelist("10.0.0.5", 502, "rtu") is not None
    assert gate.match_whitelist("10.0.0.5", 502, "tcp") is None


def test_refusal_carries_authorization_request(tmp_path):
    store = _store(tmp_path, [_entry("10.0.0.1")])
    gate = Gate(store, resolve_hostnames=False, temp_ttl_default=120)
    target, refusal = resolve_target(gate, "10.0.0.9", port=502)
    assert target is None
    assert refusal["ok"] is False
    assert refusal["error"]["kind"] == "not_whitelisted"
    info = refusal["needs_authorization"]
    assert info["host"] == "10.0.0.9"
    assert info["default_ttl_seconds"] == 120
    assert info["request_id"].startswith("auth-")


def test_temporary_authorization_grants_access(tmp_path):
    store = _store(tmp_path, [])
    gate = Gate(store, resolve_hostnames=False)
    _, refusal = resolve_target(gate, "10.0.0.9", port=502)
    request_id = refusal["needs_authorization"]["request_id"]
    gate.authorize_temporary(request_id, ttl_seconds=60)

    target, refusal2 = resolve_target(gate, "10.0.0.9", port=502)
    assert refusal2 is None
    assert target is not None and target.port == 502
    assert target.source.startswith("temporary:")
    # temporary auth is per port
    _, refusal3 = resolve_target(gate, "10.0.0.9", port=1502)
    assert refusal3 is not None


def test_temporary_authorization_expires(tmp_path):
    store = _store(tmp_path, [])
    gate = Gate(store, resolve_hostnames=False)
    _, refusal = resolve_target(gate, "10.0.0.9", port=502)
    gate.authorize_temporary(refusal["needs_authorization"]["request_id"], 60)
    for auth in gate._temp.values():  # simulate clock passing
        auth.expires_at = 0
    _, refusal2 = resolve_target(gate, "10.0.0.9", port=502)
    assert refusal2 is not None


def test_device_alias_is_trusted(tmp_path):
    store = _store(tmp_path, [])
    (tmp_path / "config" / "devices.json").write_text(json.dumps({
        "devices": [{"alias": "一號機", "host": "172.16.0.10", "port": 502,
                     "unit": 3, "framer": "rtu"}]
    }, ensure_ascii=False), encoding="utf-8")
    gate = Gate(store, resolve_hostnames=False)
    target, refusal = resolve_target(gate, "一號機")
    assert refusal is None
    assert (target.host, target.port, target.unit, target.framer) == ("172.16.0.10", 502, 3, "rtu")
    assert target.alias == "一號機"


def test_invalid_unit_and_port_rejected(tmp_path):
    store = _store(tmp_path, [_entry("10.0.0.1")])
    gate = Gate(store, resolve_hostnames=False)
    _, refusal = resolve_target(gate, "10.0.0.1", unit=999)
    assert refusal["error"]["kind"] == "invalid_argument"
    _, refusal = resolve_target(gate, "10.0.0.1", port=70000)
    assert refusal["error"]["kind"] == "invalid_argument"


def test_whitelist_add_remove_and_hot_reload(tmp_path):
    store = _store(tmp_path, [])
    gate = Gate(store, resolve_hostnames=False)
    entry, created = store.add_whitelist("10.0.0.20", ports=[502], note="test")
    assert created is True and entry.note == "test"

    entry2, created2 = store.add_whitelist("10.0.0.20", note="updated")
    assert created2 is False and entry2.note == "updated"

    # hot reload: hand edit the file, no restart
    store.whitelist_path.write_text(json.dumps(
        {"entries": [_entry("10.0.0.30", [502])]}), encoding="utf-8")
    assert gate.match_whitelist("10.0.0.30", 502) is not None
    assert gate.match_whitelist("10.0.0.20", 502) is None

    assert store.remove_whitelist("10.0.0.30") is True
    assert store.remove_whitelist("10.0.0.30") is False


def test_audit_log_records_events(tmp_path):
    store = _store(tmp_path, [])
    store.audit({"event": "unit_test", "detail": "中文也要能寫"})
    tail = store.audit_tail(5)
    assert tail[-1]["event"] == "unit_test"
    assert tail[-1]["detail"] == "中文也要能寫"
