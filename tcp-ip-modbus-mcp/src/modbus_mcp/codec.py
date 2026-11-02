"""Modbus frame codec: MBAP (Modbus TCP) and RTU (RTU-over-TCP) framing.

Self-contained on purpose: no third-party Modbus dependency, so the wire
format stays exactly as specified and every byte can be reported in
diagnostics output.
"""

from __future__ import annotations

import struct

# --------------------------------------------------------------------------
# Function codes
# --------------------------------------------------------------------------
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_COILS = 0x0F
FC_WRITE_MULTIPLE_REGISTERS = 0x10

READ_FUNCTIONS = (FC_READ_COILS, FC_READ_DISCRETE_INPUTS,
                  FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS)
WRITE_FUNCTIONS = (FC_WRITE_SINGLE_COIL, FC_WRITE_SINGLE_REGISTER,
                   FC_WRITE_MULTIPLE_COILS, FC_WRITE_MULTIPLE_REGISTERS)

# area name -> function codes
READ_AREAS = {
    "coil": FC_READ_COILS,
    "coils": FC_READ_COILS,
    "discrete_input": FC_READ_DISCRETE_INPUTS,
    "discrete_inputs": FC_READ_DISCRETE_INPUTS,
    "holding": FC_READ_HOLDING_REGISTERS,
    "holding_register": FC_READ_HOLDING_REGISTERS,
    "holding_registers": FC_READ_HOLDING_REGISTERS,
    "input": FC_READ_INPUT_REGISTERS,
    "input_register": FC_READ_INPUT_REGISTERS,
    "input_registers": FC_READ_INPUT_REGISTERS,
}

BIT_AREAS = (FC_READ_COILS, FC_READ_DISCRETE_INPUTS)

# Modbus limits (spec 4.x)
MAX_READ_BITS = 2000
MAX_READ_REGISTERS = 125
MAX_WRITE_BITS = 1968
MAX_WRITE_REGISTERS = 123

# --------------------------------------------------------------------------
# Exception codes
# --------------------------------------------------------------------------
EXCEPTION_CODES: dict[int, tuple[str, str]] = {
    0x01: ("ILLEGAL_FUNCTION", "設備不支援這個功能碼（FC 不對）"),
    0x02: ("ILLEGAL_DATA_ADDRESS", "位址不存在或超出設備可用範圍"),
    0x03: ("ILLEGAL_DATA_VALUE", "數量或數值超出設備允許範圍"),
    0x04: ("SERVER_DEVICE_FAILURE", "設備內部錯誤（PLC 端故障或未就緒）"),
    0x05: ("ACKNOWLEDGE", "設備已受理但仍在處理，需稍後再問"),
    0x06: ("SERVER_DEVICE_BUSY", "設備忙碌中，稍後重試"),
    0x08: ("MEMORY_PARITY_ERROR", "設備記憶體同位錯誤"),
    0x0A: ("GATEWAY_PATH_UNAVAILABLE", "gateway 找不到通往目標設備的路徑"),
    0x0B: ("GATEWAY_TARGET_NO_RESPONSE", "gateway 有回應，但目標 slave 沒有回應"),
}

# Exception codes that mean "the slave itself is not there"
ABSENT_SLAVE_EXCEPTIONS = (0x0A, 0x0B)


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16 (poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


# --------------------------------------------------------------------------
# PDU builders
# --------------------------------------------------------------------------
def build_read_pdu(fc: int, address: int, count: int) -> bytes:
    return struct.pack(">BHH", fc, address, count)


def build_write_single_pdu(fc: int, address: int, value: int) -> bytes:
    return struct.pack(">BHH", fc, address, value)


def build_write_multiple_pdu(fc: int, address: int, values: list[int]) -> bytes:
    if fc == FC_WRITE_MULTIPLE_COILS:
        count = len(values)
        nbytes = (count + 7) // 8
        packed = bytearray(nbytes)
        for i, v in enumerate(values):
            if v:
                packed[i // 8] |= 1 << (i % 8)
        return struct.pack(">BHHB", fc, address, count, nbytes) + bytes(packed)
    if fc == FC_WRITE_MULTIPLE_REGISTERS:
        count = len(values)
        payload = b"".join(struct.pack(">H", v & 0xFFFF) for v in values)
        return struct.pack(">BHHB", fc, address, count, 2 * count) + payload
    raise ValueError(f"not a multiple-write function code: {fc}")


def build_pdu(fc: int, address: int, values: list[int] | None = None,
              count: int | None = None, single_value: int | None = None) -> bytes:
    """Build a PDU for any supported function code."""
    if fc in READ_FUNCTIONS:
        if count is None:
            raise ValueError("count required for read function codes")
        return build_read_pdu(fc, address, count)
    if fc in (FC_WRITE_SINGLE_COIL, FC_WRITE_SINGLE_REGISTER):
        if single_value is None:
            raise ValueError("value required for single-write function codes")
        return build_write_single_pdu(fc, address, single_value)
    if fc in (FC_WRITE_MULTIPLE_COILS, FC_WRITE_MULTIPLE_REGISTERS):
        if not values:
            raise ValueError("values required for multiple-write function codes")
        return build_write_multiple_pdu(fc, address, values)
    raise ValueError(f"unsupported function code: {fc}")


# --------------------------------------------------------------------------
# ADU framing
# --------------------------------------------------------------------------
def build_tcp_adu(unit: int, pdu: bytes, transaction_id: int) -> bytes:
    header = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, unit & 0xFF)
    return header + pdu


def build_rtu_adu(unit: int, pdu: bytes) -> bytes:
    frame = struct.pack(">B", unit & 0xFF) + pdu
    crc = crc16(frame)
    return frame + struct.pack("<H", crc)


def build_adu(framer: str, unit: int, pdu: bytes, transaction_id: int = 1) -> bytes:
    if framer == "rtu":
        return build_rtu_adu(unit, pdu)
    return build_tcp_adu(unit, pdu, transaction_id)


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------
def parse_exception_pdu(pdu: bytes) -> tuple[int, int]:
    """Return (function_code, exception_code) from an exception PDU."""
    return pdu[0] & 0x7F, pdu[1]


def parse_read_response(pdu: bytes, fc: int, count: int) -> list[int]:
    """Decode a read response PDU into raw ints (bits as 0/1, registers as 0..65535)."""
    if len(pdu) < 2:
        raise ValueError("response PDU too short")
    byte_count = pdu[1]
    payload = pdu[2:]
    if len(payload) != byte_count:
        raise ValueError(
            f"byte count mismatch: header says {byte_count}, got {len(payload)}")
    if fc in BIT_AREAS:
        expected = (count + 7) // 8
        if byte_count != expected:
            raise ValueError(f"expected {expected} bytes of bits, got {byte_count}")
        bits: list[int] = []
        for i in range(count):
            bits.append((payload[i // 8] >> (i % 8)) & 1)
        return bits
    expected = count * 2
    if byte_count != expected:
        raise ValueError(f"expected {expected} bytes of registers, got {byte_count}")
    return list(struct.unpack(f">{count}H", payload))
