"""Frame-level tests against known-good Modbus byte sequences."""

from __future__ import annotations

import pytest

from modbus_mcp import codec
from modbus_mcp.models import decode_registers, encode_registers


def test_crc16_known_frame():
    # classic RTU request: unit 1, FC03, addr 0, qty 10 -> 01 03 00 00 00 0A C5 CD
    frame = codec.build_rtu_adu(1, codec.build_read_pdu(codec.FC_READ_HOLDING_REGISTERS, 0, 10))
    assert frame == bytes.fromhex("01030000000AC5CD")


def test_rtu_crc_roundtrip():
    for payload in (b"\x01\x03\x00\x00\x00\x01", b"\x11\x06\x00\x0a\x00\x64"):
        frame = codec.build_rtu_adu(payload[0], payload[1:])
        assert codec.crc16(frame[:-2]) == int.from_bytes(frame[-2:], "little")


def test_tcp_adu_header():
    adu = codec.build_tcp_adu(0x11, codec.build_read_pdu(codec.FC_READ_HOLDING_REGISTERS, 0x13, 0x25), 7)
    assert adu == bytes.fromhex("000700000006110300130025")


def test_read_pdu_layout():
    assert codec.build_read_pdu(codec.FC_READ_COILS, 0x0013, 0x0013) == bytes.fromhex("0100130013")


def test_write_single_register_pdu():
    pdu = codec.build_write_single_pdu(codec.FC_WRITE_SINGLE_REGISTER, 1, 3)
    assert pdu == bytes.fromhex("0600010003")


def test_write_multiple_registers_pdu():
    pdu = codec.build_write_multiple_pdu(codec.FC_WRITE_MULTIPLE_REGISTERS, 1, [10, 258])
    # fc, addr(2), qty(2), bytecount(1), data
    assert pdu == bytes.fromhex("100001000204000A0102")


def test_write_multiple_coils_pdu():
    pdu = codec.build_write_multiple_pdu(codec.FC_WRITE_MULTIPLE_COILS, 0x13, [1, 0, 1, 1, 0, 0, 1, 1, 1, 0])
    assert pdu == bytes.fromhex("0F0013000A02CD01")


def test_parse_read_bits():
    # 3 bits requested, device sends 1 byte 0b00000101
    pdu = bytes([0x01, 0x01, 0x05])
    assert codec.parse_read_response(pdu, codec.FC_READ_COILS, 3) == [1, 0, 1]


def test_parse_read_registers():
    pdu = bytes([0x03, 0x04, 0x00, 0x0A, 0x01, 0x02])
    assert codec.parse_read_response(pdu, codec.FC_READ_HOLDING_REGISTERS, 2) == [10, 258]


def test_parse_read_response_rejects_bytecount_mismatch():
    pdu = bytes([0x03, 0x04, 0x00, 0x0A])
    with pytest.raises(ValueError, match="byte count mismatch"):
        codec.parse_read_response(pdu, codec.FC_READ_HOLDING_REGISTERS, 2)


def test_exception_pdu():
    assert codec.parse_exception_pdu(bytes([0x83, 0x02])) == (3, 2)


def test_decode_uint32_big():
    assert decode_registers([0x1234, 0x5678], "uint32")[0] == 0x12345678


def test_decode_word_orders():
    assert decode_registers([0x1234, 0x5678], "uint32", "big")[0] == 0x12345678
    assert decode_registers([0x1234, 0x5678], "uint32", "little")[0] == 0x56781234
    assert decode_registers([0x1234], "uint16", byte_order="little")[0] == 0x3412


def test_decode_float32_roundtrip():
    regs = encode_registers([1.5], "float32")
    assert decode_registers(regs, "float32")[0] == 1.5


def test_decode_int16_negative():
    assert decode_registers([0xFFFF], "int16")[0] == -1


def test_decode_rejects_partial_values():
    with pytest.raises(ValueError):
        decode_registers([0x1234], "uint32")
