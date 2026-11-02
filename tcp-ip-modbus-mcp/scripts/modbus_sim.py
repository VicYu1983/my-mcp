"""Modbus 設備模擬器（測試 / 展示用）。

支援 Modbus TCP（MBAP）與 Modbus RTU over TCP，FC01/02/03/04/05/06/15/16。
用途：在還沒有實機 PLC 時，先驗證 MCP server 的讀寫、診斷與批次檢查流程。

範例：
    uv run python scripts/modbus_sim.py --port 5020 --unit 1 --registers 100,200,300
    uv run python scripts/modbus_sim.py --port 5021 --unit 1 --framer rtu
    uv run python scripts/modbus_sim.py --port 5022 --silent   # 只接 TCP、永不回應
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def log(message: str) -> None:
    """輸出失敗（例如 console 編碼不支援）絕不能影響 Modbus 服務。"""
    try:
        print(message, flush=True)
    except Exception:  # noqa: BLE001,S110 - 輸出失敗不能影響 Modbus 服務
        pass

from modbus_mcp import codec


class Simulator:
    def __init__(self, *, unit: int, framer: str, registers: list[int],
                 coils: list[bool], silent: bool) -> None:
        self.unit = unit
        self.framer = framer
        self.registers = dict(enumerate(registers))
        self.input_registers = dict(enumerate(registers))
        self.coils = dict(enumerate(coils))
        self.silent = silent
        self.requests = 0

    # -- 讀取請求 ---------------------------------------------------------
    async def read_request(self, reader: asyncio.StreamReader) -> dict | None:
        try:
            if self.framer == "rtu":
                head = await reader.readexactly(2)
                fc = head[1]
                if fc in codec.READ_FUNCTIONS or fc in (
                        codec.FC_WRITE_SINGLE_COIL, codec.FC_WRITE_SINGLE_REGISTER):
                    rest = await reader.readexactly(6)
                else:
                    mid = await reader.readexactly(5)
                    rest = mid + await reader.readexactly(mid[4] + 2)
                frame = head + rest
                return {"unit": frame[0], "pdu": frame[1:-2], "txn": None}
            header = await reader.readexactly(7)
            txn, _proto, length, unit = struct.unpack(">HHHB", header)
            return {"unit": unit, "pdu": await reader.readexactly(length - 1), "txn": txn}
        except (asyncio.IncompleteReadError, ConnectionResetError, TimeoutError):
            return None

    def build(self, unit: int, pdu: bytes, txn: int | None) -> bytes:
        if self.framer == "rtu":
            return codec.build_rtu_adu(unit, pdu)
        return codec.build_tcp_adu(unit, pdu, txn or 1)

    # -- 服務迴圈 ---------------------------------------------------------
    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        with contextlib.suppress(Exception):
            while True:
                request = await self.read_request(reader)
                if request is None:
                    break
                self.requests += 1
                if request["unit"] != self.unit:
                    continue  # 不是叫我，保持沉默（跟真實匯流排一樣）
                if self.silent:
                    await reader.read(4096)  # 連線保持著但不回應
                    continue
                try:
                    response = self.respond(request)
                except Exception as exc:  # noqa: BLE001 - 模擬器自身錯誤
                    log(f"  !! 模擬器內部錯誤：{exc!r}")
                    response = self.exception(request["unit"], request["pdu"][0],
                                              request["txn"], 0x04)
                if response:
                    writer.write(response)
                    await writer.drain()
                log(f"  <- {peer} unit={request['unit']} "
                    f"{request['pdu'].hex(' ').upper()}")
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            writer.close()

    def respond(self, request: dict) -> bytes | None:
        pdu = request["pdu"]
        fc = pdu[0]
        unit, txn = request["unit"], request["txn"]
        if fc in (codec.FC_READ_COILS, codec.FC_READ_DISCRETE_INPUTS):
            address, count = struct.unpack(">HH", pdu[1:5])
            if any(i not in self.coils for i in range(address, address + count)):
                return self.exception(unit, fc, txn, 0x02)
            payload = bytearray((count + 7) // 8)
            for i in range(count):
                if self.coils[address + i]:
                    payload[i // 8] |= 1 << (i % 8)
            return self.build(unit, bytes([fc, len(payload)]) + bytes(payload), txn)
        if fc in (codec.FC_READ_HOLDING_REGISTERS, codec.FC_READ_INPUT_REGISTERS):
            address, count = struct.unpack(">HH", pdu[1:5])
            store = self.registers if fc == codec.FC_READ_HOLDING_REGISTERS \
                else self.input_registers
            if any(i not in store for i in range(address, address + count)):
                return self.exception(unit, fc, txn, 0x02)
            payload = b"".join(struct.pack(">H", store[i] & 0xFFFF)
                               for i in range(address, address + count))
            return self.build(unit, bytes([fc, len(payload)]) + payload, txn)
        if fc == codec.FC_WRITE_SINGLE_REGISTER:
            address, value = struct.unpack(">HH", pdu[1:5])
            self.registers[address] = value
            log(f"  OK 寫入 register[{address}] = {value}")
            return self.build(unit, pdu, txn)
        if fc == codec.FC_WRITE_SINGLE_COIL:
            address, value = struct.unpack(">HH", pdu[1:5])
            self.coils[address] = value == 0xFF00
            log(f"  OK 寫入 coil[{address}] = {self.coils[address]}")
            return self.build(unit, pdu, txn)
        if fc == codec.FC_WRITE_MULTIPLE_REGISTERS:
            address, count, bytecount = struct.unpack(">HHB", pdu[1:6])
            for offset, value in enumerate(struct.unpack(f">{count}H", pdu[6:6 + bytecount])):
                self.registers[address + offset] = value
            log(f"  OK 批次寫入 register[{address}..{address + count - 1}]")
            return self.build(unit, bytes([fc]) + struct.pack(">HH", address, count), txn)
        if fc == codec.FC_WRITE_MULTIPLE_COILS:
            address, count, bytecount = struct.unpack(">HHB", pdu[1:6])
            data = pdu[6:6 + bytecount]
            for offset in range(count):
                self.coils[address + offset] = bool((data[offset // 8] >> (offset % 8)) & 1)
            return self.build(unit, bytes([fc]) + struct.pack(">HH", address, count), txn)
        return self.exception(unit, fc, txn, 0x01)

    def exception(self, unit: int, fc: int, txn: int | None, code: int) -> bytes:
        log(f"  ! 回例外碼 0x{code:02X}")
        return self.build(unit, bytes([fc | 0x80, code]), txn)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Modbus 設備模擬器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--unit", type=int, default=1)
    parser.add_argument("--framer", choices=["tcp", "rtu"], default="tcp")
    parser.add_argument("--registers", default="100,200,300,400,500,600,700,800")
    parser.add_argument("--coils", default="1,0,1,0")
    parser.add_argument("--silent", action="store_true",
                        help="只接受 TCP 連線但永不回應（模擬 Modbus 不通）")
    args = parser.parse_args()

    registers = [int(x) & 0xFFFF for x in args.registers.split(",") if x.strip()]
    coils = [x.strip() not in ("0", "false", "") for x in args.coils.split(",")]

    sim = Simulator(unit=args.unit, framer=args.framer, registers=registers,
                    coils=coils, silent=args.silent)
    server = await asyncio.start_server(sim.handle, args.host, args.port)
    log(f"模擬器啟動：{args.host}:{args.port} unit={args.unit} framer={args.framer}"
        f"{' (silent)' if args.silent else ''}")
    log(f"registers={registers} coils={coils}")
    log("按 Ctrl+C 結束")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
