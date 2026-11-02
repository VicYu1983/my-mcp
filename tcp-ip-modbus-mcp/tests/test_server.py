"""Tool-level tests: whitelist gating, write safety, batch check."""

from __future__ import annotations

import json

import pytest
from fake_device import FakeModbusDevice

from modbus_mcp import server
from modbus_mcp.config import get_store

CONSENT = "好，這個端口可以查"


def _write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def allow_ports(ports: list[int], protocol=None):
    store = get_store()
    _write_json(store.whitelist_path, {"entries": [
        {"target": "127.0.0.1", "ports": ports, "protocol": protocol, "note": "test"}
    ]})


def set_devices(devices: list[dict]):
    _write_json(get_store().devices_path, {"devices": devices})


@pytest.fixture
async def device():
    dev = FakeModbusDevice(registers=[100, 200, 300], coils=[True, False])
    await dev.start()
    allow_ports([dev.port])
    yield dev
    await dev.stop()


async def test_tcp_check_requires_whitelist(device):
    allow_ports([1])  # device port intentionally not allowed
    out = await server.tcp_check("127.0.0.1", port=device.port)
    assert out["ok"] is False
    assert out["error"]["kind"] == "not_whitelisted"
    assert out["needs_authorization"]["host"] == "127.0.0.1"
    assert device.requests == 0  # nothing was contacted


async def test_temporary_authorization_unblocks(device):
    allow_ports([1])
    refusal = await server.tcp_check("127.0.0.1", port=device.port)
    request_id = refusal["needs_authorization"]["request_id"]
    granted = server.authorize_temporary(request_id, ttl_seconds=60)
    assert granted["ok"] is True
    out = await server.tcp_check("127.0.0.1", port=device.port)
    assert out["ok"] is True


async def test_whitelist_add_requires_consent():
    out = server.whitelist_add("10.1.2.3", note="no consent")
    assert out["ok"] is False
    assert out["error"]["kind"] == "consent_required"
    out = server.whitelist_add("10.1.2.3", note="with consent", user_consent="可以加入")
    assert out["ok"] is True
    listed = server.whitelist_list()
    assert any(entry["target"] == "10.1.2.3" for entry in listed["whitelist"])
    assert server.whitelist_remove("10.1.2.3", user_consent="移除")["ok"] is True


async def test_read_tool(device):
    out = await server.modbus_read("127.0.0.1", "holding", 0, 3, port=device.port)
    assert out["ok"] is True and out["values"] == [100, 200, 300]


async def test_write_defaults_to_dry_run(device):
    out = await server.modbus_write("127.0.0.1", "holding", 10, [999], port=device.port)
    assert out["ok"] is True and out["dry_run"] is True
    assert out["preview"]["function"].startswith("FC06")
    assert device.requests == 0        # dry run never touches the device
    assert 10 not in device.registers


async def test_write_needs_confirm(device):
    out = await server.modbus_write("127.0.0.1", "holding", 10, [999], port=device.port,
                                    dry_run=False)
    assert out["ok"] is False and out["error"]["kind"] == "confirmation_required"
    assert device.requests == 0


async def test_write_needs_consent_text(device):
    out = await server.modbus_write("127.0.0.1", "holding", 10, [999], port=device.port,
                                    dry_run=False, confirm=True)
    assert out["ok"] is False and out["error"]["kind"] == "consent_required"
    assert device.requests == 0


async def test_write_executes_and_verifies(device):
    out = await server.modbus_write("127.0.0.1", "holding", 10, [999], port=device.port,
                                    dry_run=False, confirm=True, user_consent=CONSENT)
    assert out["ok"] is True
    assert out["verified"] is True
    assert out["readback"]["values"] == [999]
    assert device.registers[10] == 999

    tail = server.audit_tail(10)
    assert any(rec.get("event") == "modbus_write" and rec.get("ok") is True
               for rec in tail["records"])
    assert any(CONSENT in str(rec.get("user_consent", "")) for rec in tail["records"])


async def test_write_refuses_readonly_area(device):
    out = await server.modbus_write("127.0.0.1", "input", 0, [1], port=device.port,
                                    dry_run=False, confirm=True, user_consent=CONSENT)
    assert out["ok"] is False and out["error"]["kind"] == "invalid_argument"


async def test_diagnose_reports_stable(device):
    out = await server.modbus_diagnose("127.0.0.1", attempts=3, port=device.port,
                                       timeout_ms=800, interval_ms=0)
    assert out["ok"] is True and out["verdict"] == "stable"
    assert out["stats"]["success"] == 3


async def test_check_all_devices_scope(device):
    set_devices([
        {"alias": "好的設備", "host": "127.0.0.1", "port": device.port, "unit": 1},
        {"alias": "壞的設備", "host": "127.0.0.1", "port": 1, "unit": 1},
    ])
    out = await server.check_all(scope="devices", level="probe", timeout_ms=500, concurrency=2)
    assert out["ok"] is False
    assert out["counts"]["total"] == 2
    assert out["counts"]["ok"] == 1
    assert out["counts"]["tcp_fail"] == 1
    assert "1" in [str(item["port"]) for item in out["results"]]


async def test_check_all_respects_max_targets(device):
    set_devices([{"alias": f"d{i}", "host": "127.0.0.1", "port": device.port + i}
                 for i in range(5)])
    out = await server.check_all(scope="devices", max_targets=2)
    assert out["ok"] is False
    assert out["error"]["kind"] == "too_many_targets"


async def test_device_list(device):
    set_devices([{"alias": "一號機", "host": "127.0.0.1", "port": device.port, "unit": 2}])
    out = server.device_list()
    assert out["ok"] is True and out["devices"][0]["alias"] == "一號機"


async def test_write_disabled_by_env(device, monkeypatch):
    monkeypatch.setenv("MODBUS_WRITE_ENABLED", "false")
    out = await server.modbus_write("127.0.0.1", "holding", 10, [1], port=device.port,
                                    dry_run=False, confirm=True, user_consent=CONSENT)
    assert out["ok"] is False and out["error"]["kind"] == "write_disabled"
    assert device.requests == 0
