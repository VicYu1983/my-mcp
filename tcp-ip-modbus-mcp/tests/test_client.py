"""Client tests against an in-process fake Modbus device."""

from __future__ import annotations

import pytest
from fake_device import FakeModbusDevice

from modbus_mcp import client
from modbus_mcp.safety import Target

FAST = 0.6


def tgt(port: int, unit: int = 1, framer: str = "tcp") -> Target:
    return Target(host="127.0.0.1", port=port, unit=unit, framer=framer)


@pytest.fixture
async def device():
    dev = FakeModbusDevice(registers=[11, 22, 33, 44], coils=[True, False, True])
    await dev.start()
    yield dev
    await dev.stop()


@pytest.fixture
async def rtu_device():
    dev = FakeModbusDevice(framer="rtu", registers=[7, 8, 9])
    await dev.start()
    yield dev
    await dev.stop()


async def test_read_holding_registers(device):
    out = await client.read(tgt(device.port), "holding", 0, 3, timeout=FAST)
    assert out["ok"] is True
    assert out["raw"] == [11, 22, 33]
    assert out["values"] == [11, 22, 33]
    assert out["function"] == "FC03"


async def test_read_input_registers(device):
    out = await client.read(tgt(device.port), "input", 1, 2, timeout=FAST)
    assert out["ok"] is True and out["raw"] == [22, 33]


async def test_read_coils_returns_booleans(device):
    out = await client.read(tgt(device.port), "coil", 0, 3, timeout=FAST)
    assert out["ok"] is True
    assert out["values"] == [True, False, True]


async def test_read_out_of_range_reports_exception(device):
    out = await client.read(tgt(device.port), "holding", 900, 2, timeout=FAST)
    assert out["ok"] is False
    assert out["error"]["kind"] == "modbus_exception"
    assert out["error"]["exception_code"] == "0x02"


async def test_read_rejects_bad_count(device):
    out = await client.read(tgt(device.port), "holding", 0, 5000, timeout=FAST)
    assert out["ok"] is False and out["error"]["kind"] == "invalid_argument"


async def test_write_single_register(device):
    out = await client.write(tgt(device.port), "holding", 10, [1234], timeout=FAST)
    assert out["ok"] is True
    assert out["function"] == "FC06"
    assert device.registers[10] == 1234


async def test_write_multiple_registers(device):
    out = await client.write(tgt(device.port), "holding", 20, [111, 222], timeout=FAST)
    assert out["ok"] is True and out["function"] == "FC16"
    assert device.registers[20] == 111 and device.registers[21] == 222


async def test_write_single_coil(device):
    out = await client.write(tgt(device.port), "coil", 7, [True], timeout=FAST)
    assert out["ok"] is True and out["function"] == "FC05"
    assert device.coils[7] is True


async def test_write_multiple_coils(device):
    out = await client.write(tgt(device.port), "coil", 10, [1, 0, 1], timeout=FAST)
    assert out["ok"] is True and out["function"] == "FC15"
    assert [device.coils[i] for i in (10, 11, 12)] == [True, False, True]


async def test_write_rejects_readonly_area(device):
    out = await client.write(tgt(device.port), "input", 0, [1], timeout=FAST)
    assert out["ok"] is False and out["error"]["kind"] == "invalid_argument"


async def test_write_rejects_out_of_range_value(device):
    out = await client.write(tgt(device.port), "holding", 0, [70000], timeout=FAST)
    assert out["ok"] is False and out["error"]["kind"] == "invalid_argument"


async def test_write_float32_occupies_two_registers(device):
    out = await client.write(tgt(device.port), "holding", 30, [1.5], timeout=FAST,
                             data_type="float32")
    assert out["ok"] is True
    assert out["written_count"] == 2
    read = await client.read(tgt(device.port), "holding", 30, 2, timeout=FAST,
                             data_type="float32")
    assert read["values"] == [1.5]


async def test_probe_ok(device):
    out = await client.probe(tgt(device.port), timeout=FAST)
    assert out["ok"] is True and out["responded"] is True


async def test_probe_wrong_unit_times_out(device):
    out = await client.probe(tgt(device.port, unit=9), timeout=FAST)
    assert out["ok"] is False
    assert out["error"]["kind"] == "modbus_timeout"
    assert out["stage"] == "modbus"


async def test_probe_connection_refused():
    out = await client.probe(tgt(1), timeout=0.3)  # nothing listens on port 1
    assert out["ok"] is False
    assert out["error"]["kind"].startswith("tcp_")


async def test_rtu_over_tcp(rtu_device):
    read = await client.read(tgt(rtu_device.port, framer="rtu"), "holding", 0, 3, timeout=FAST)
    assert read["ok"] is True and read["raw"] == [7, 8, 9]
    write = await client.write(tgt(rtu_device.port, framer="rtu"), "holding", 5, [42], timeout=FAST)
    assert write["ok"] is True and rtu_device.registers[5] == 42


async def test_rtu_multi_write(rtu_device):
    out = await client.write(tgt(rtu_device.port, framer="rtu"), "holding", 10,
                             [1, 2, 3], timeout=FAST)
    assert out["ok"] is True
    assert [rtu_device.registers[i] for i in (10, 11, 12)] == [1, 2, 3]


async def test_silent_device_yields_modbus_timeout():
    dev = FakeModbusDevice(registers=[1], respond=False)
    await dev.start()
    try:
        out = await client.probe(tgt(dev.port), timeout=0.4)
        assert out["ok"] is False
        assert out["error"]["kind"] == "modbus_timeout"
    finally:
        await dev.stop()


async def test_scan_units_finds_slave(device):
    out = await client.scan_units(tgt(device.port, unit=1), 1, 4, timeout=0.4, concurrency=2)
    assert out["ok"] is True
    assert [entry["unit"] for entry in out["units_found"]] == [1]
