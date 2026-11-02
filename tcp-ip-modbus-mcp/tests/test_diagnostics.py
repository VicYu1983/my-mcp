"""Target expansion rules for batch checks (no network I/O)."""

from __future__ import annotations

import json

from modbus_mcp.config import ConfigStore
from modbus_mcp.diagnostics import _expand_targets


def _store(tmp_path, entries, devices=None) -> ConfigStore:
    store = ConfigStore(tmp_path / "config", tmp_path / "logs")
    store.whitelist_path.write_text(
        json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")
    store.devices_path.write_text(
        json.dumps({"devices": devices or []}, ensure_ascii=False), encoding="utf-8")
    return store


def _entry(target, ports=None, protocol=None):
    return {"target": target, "ports": ports, "protocol": protocol}


def test_exact_ip_and_default_port(tmp_path):
    store = _store(tmp_path, [_entry("10.0.0.1")])
    targets, notes = _expand_targets(store, "whitelist", default_port=502,
                                     protocol_filter=None)
    assert [(t.host, t.port) for t in targets] == [("10.0.0.1", 502)]
    assert notes == []


def test_cidr_is_expanded_to_usable_hosts(tmp_path):
    store = _store(tmp_path, [_entry("192.168.5.0/30")])
    targets, _ = _expand_targets(store, "whitelist", default_port=502, protocol_filter=None)
    assert sorted(t.host for t in targets) == ["192.168.5.1", "192.168.5.2"]


def test_oversized_cidr_is_skipped_with_note(tmp_path):
    store = _store(tmp_path, [_entry("10.0.0.0/16")])
    targets, notes = _expand_targets(store, "whitelist", default_port=502,
                                     protocol_filter=None)
    assert targets == []
    assert any("過大" in note for note in notes)


def test_multiple_ports_and_protocol_filter(tmp_path):
    store = _store(tmp_path, [
        _entry("10.0.0.7", [502, 5020]),
        _entry("10.0.0.8", [502], protocol="rtu"),
    ])
    targets, _ = _expand_targets(store, "whitelist", default_port=502, protocol_filter=None)
    assert sorted((t.host, t.port, t.framer) for t in targets) == [
        ("10.0.0.7", 502, "tcp"), ("10.0.0.7", 5020, "tcp"), ("10.0.0.8", 502, "rtu")]

    rtu_only, _ = _expand_targets(store, "whitelist", default_port=502, protocol_filter="rtu")
    assert [(t.host, t.framer) for t in rtu_only] == [("10.0.0.8", "rtu")]


def test_devices_scope_and_dedup(tmp_path):
    store = _store(
        tmp_path,
        [_entry("10.0.0.1", [502])],
        devices=[
            {"alias": "A", "host": "10.0.0.1", "port": 502, "unit": 1},
            {"alias": "B", "host": "10.0.0.2", "port": 502, "unit": 3},
        ])
    both, _ = _expand_targets(store, "both", default_port=502, protocol_filter=None)
    hosts = sorted(t.host for t in both)
    assert hosts == ["10.0.0.1", "10.0.0.2"]  # 10.0.0.1 not duplicated
