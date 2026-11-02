"""A tiny in-process Modbus device used by the tests (TCP and RTU-over-TCP)."""

from __future__ import annotations

import asyncio
import contextlib
import struct

from modbus_mcp import codec


class FakeModbusDevice:
    def __init__(self, *, unit: int = 1, framer: str = "tcp",
                 registers: list[int] | None = None,
                 input_registers: list[int] | None = None,
                 coils: list[bool] | None = None,
                 respond: bool = True) -> None:
        self.unit = unit
        self.framer = framer
        self.registers: dict[int, int] = dict(enumerate(registers or []))
        self.input_registers: dict[int, int] = dict(
            enumerate(input_registers if input_registers is not None else (registers or [])))
        self.coils: dict[int, bool] = dict(enumerate(coils or []))
        self.respond = respond
        self.requests = 0
        self.server: asyncio.AbstractServer | None = None
        self.port: int | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    # -- framing -----------------------------------------------------------
    async def _read_request(self, reader: asyncio.StreamReader) -> dict | None:
        try:
            if self.framer == "rtu":
                head = await reader.readexactly(2)
                fc = head[1]
                if fc in codec.READ_FUNCTIONS or fc in (codec.FC_WRITE_SINGLE_COIL,
                                                        codec.FC_WRITE_SINGLE_REGISTER):
                    rest = await reader.readexactly(6)
                else:
                    mid = await reader.readexactly(5)      # addr(2) qty(2) bytecount(1)
                    data = await reader.readexactly(mid[4] + 2)  # payload + crc
                    rest = mid + data
                frame = head + rest
                return {"framer": "rtu", "unit": frame[0], "pdu": frame[1:-2],
                        "txn": None, "raw": frame}
            header = await reader.readexactly(7)
            txn, _proto, length, unit = struct.unpack(">HHHB", header)
            body = await reader.readexactly(length - 1)
            return {"framer": "tcp", "unit": unit, "pdu": body, "txn": txn,
                    "raw": header + body}
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            return None

    def _build(self, unit: int, pdu: bytes, txn: int | None) -> bytes:
        if self.framer == "rtu":
            return codec.build_rtu_adu(unit, pdu)
        return codec.build_tcp_adu(unit, pdu, txn or 1)

    # -- server loop -------------------------------------------------------
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while True:
                request = await self._read_request(reader)
                if request is None:
                    break
                self.requests += 1
                if request["unit"] != self.unit:
                    continue  # silent, like a slave that was not addressed
                if not self.respond:
                    await reader.read(4096)  # stay connected but never answer
                    continue
                response = self._handle_request(request)
                if response:
                    writer.write(response)
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(ConnectionError, OSError, RuntimeError):
                writer.close()

    def _handle_request(self, request: dict) -> bytes | None:
        pdu = request["pdu"]
        fc = pdu[0]
        if fc in (codec.FC_READ_COILS, codec.FC_READ_DISCRETE_INPUTS):
            address, count = struct.unpack(">HH", pdu[1:5])
            store = self.coils
            if any(i not in store for i in range(address, address + count)):
                return self._exception(request, fc, 0x02)
            bits = [1 if store[i] else 0 for i in range(address, address + count)]
            payload = bytearray((count + 7) // 8)
            for i, bit in enumerate(bits):
                if bit:
                    payload[i // 8] |= 1 << (i % 8)
            return self._build(request["unit"], bytes([fc, len(payload)]) + bytes(payload),
                               request["txn"])
        if fc in (codec.FC_READ_HOLDING_REGISTERS, codec.FC_READ_INPUT_REGISTERS):
            address, count = struct.unpack(">HH", pdu[1:5])
            store = self.registers if fc == codec.FC_READ_HOLDING_REGISTERS \
                else self.input_registers
            if any(i not in store for i in range(address, address + count)):
                return self._exception(request, fc, 0x02)
            payload = b"".join(struct.pack(">H", store[i] & 0xFFFF)
                               for i in range(address, address + count))
            return self._build(request["unit"], bytes([fc, len(payload)]) + payload,
                               request["txn"])
        if fc == codec.FC_WRITE_SINGLE_REGISTER:
            address, value = struct.unpack(">HH", pdu[1:5])
            self.registers[address] = value
            return self._build(request["unit"], pdu, request["txn"])
        if fc == codec.FC_WRITE_SINGLE_COIL:
            address, value = struct.unpack(">HH", pdu[1:5])
            self.coils[address] = value == 0xFF00
            return self._build(request["unit"], pdu, request["txn"])
        if fc == codec.FC_WRITE_MULTIPLE_REGISTERS:
            address, count, bytecount = struct.unpack(">HHB", pdu[1:6])
            values = struct.unpack(f">{count}H", pdu[6:6 + bytecount])
            for offset, value in enumerate(values):
                self.registers[address + offset] = value
            return self._build(request["unit"],
                               bytes([fc]) + struct.pack(">HH", address, count),
                               request["txn"])
        if fc == codec.FC_WRITE_MULTIPLE_COILS:
            address, count, bytecount = struct.unpack(">HHB", pdu[1:6])
            data = pdu[6:6 + bytecount]
            for offset in range(count):
                self.coils[address + offset] = bool((data[offset // 8] >> (offset % 8)) & 1)
            return self._build(request["unit"],
                               bytes([fc]) + struct.pack(">HH", address, count),
                               request["txn"])
        return self._exception(request, fc, 0x01)

    def _exception(self, request: dict, fc: int, code: int) -> bytes:
        return self._build(request["unit"], bytes([fc | 0x80, code]), request["txn"])
