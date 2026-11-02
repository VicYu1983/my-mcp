"""Async Modbus client: one TCP connection per transaction, full frame diagnostics."""

from __future__ import annotations

import asyncio
import random
import struct

from . import codec
from .models import (
    decode_registers,
    encode_registers,
    failure,
    registers_per_value,
    result,
)
from .safety import Target
from .transport import ERROR_HINTS, classify_error, close_connection, open_connection

DEFAULT_TIMEOUT = 3.0


class ModbusFailure(Exception):
    """A Modbus-level failure with a machine-readable kind."""

    def __init__(self, kind: str, detail: str = "", *, hint: str | None = None,
                 stage: str = "modbus", exception_code: int | None = None,
                 raw: bytes | None = None) -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail
        self.hint = hint
        self.stage = stage
        self.exception_code = exception_code
        self.raw = raw

    def to_json(self) -> dict:
        err = {"kind": self.kind}
        if self.detail:
            err["detail"] = self.detail
        if self.hint:
            err["hint"] = self.hint
        if self.exception_code is not None:
            err["exception_code"] = f"0x{self.exception_code:02X}"
            name, meaning = codec.EXCEPTION_CODES.get(
                self.exception_code, ("UNKNOWN", "未定義的例外碼"))
            err["exception_name"] = name
            err["exception_meaning"] = meaning
        return err


def _hex(data: bytes) -> str:
    return data.hex(" ").upper()


async def _recv_exact(reader: asyncio.StreamReader, size: int, deadline: float) -> bytes:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise ModbusFailure("modbus_timeout", "等待回應逾時", stage="modbus")
    try:
        return await asyncio.wait_for(reader.readexactly(size), timeout=remaining)
    except asyncio.IncompleteReadError as exc:
        raise ModbusFailure(
            "connection_closed",
            f"對方在回應中途關閉連線（已收到 {len(exc.partial)} bytes：{_hex(exc.partial)}）",
            hint="設備可能不支援這個請求，或連線數已滿而主動斷線。",
        ) from exc
    except TimeoutError as exc:
        raise ModbusFailure(
            "modbus_timeout", "TCP 已連上，但等不到 Modbus 回應",
            hint="常見於 unit id 錯誤、設備忙碌、或 RTU 參數（baud/parity）不符。",
        ) from exc


async def transact(target: Target, pdu: bytes, *, timeout: float = DEFAULT_TIMEOUT,
                   transaction_id: int | None = None) -> dict:
    """Send one PDU, return {'pdu', 'rtt_ms', 'frame_tx', 'frame_rx'}."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    txn = transaction_id if transaction_id is not None else random.randint(1, 0xFFFF)

    try:
        reader, writer, connect_rtt = await open_connection(target.host, target.port, timeout)
    except Exception as exc:
        kind, detail = classify_error(exc)
        raise ModbusFailure(
            f"tcp_{kind}", detail, stage="tcp",
            hint=ERROR_HINTS.get(kind, "")) from exc

    try:
        start = loop.time()
        if target.framer == "rtu":
            frame = codec.build_rtu_adu(target.unit, pdu)
        else:
            frame = codec.build_tcp_adu(target.unit, pdu, txn)
        writer.write(frame)
        await writer.drain()

        if target.framer == "rtu":
            response_pdu, raw_rx, resp_unit = await _read_rtu(reader, deadline)
        else:
            response_pdu, raw_rx, resp_unit = await _read_tcp(
                reader, deadline, txn, target.unit)

        rtt_ms = round((loop.time() - start) * 1000, 2)
        return {
            "pdu": response_pdu,
            "rtt_ms": rtt_ms,
            "connect_rtt_ms": connect_rtt,
            "frame_tx": _hex(frame),
            "frame_rx": _hex(raw_rx),
            "response_unit": resp_unit,
        }
    finally:
        await close_connection(writer)


async def _read_tcp(reader, deadline: float, txn: int,
                   unit: int) -> tuple[bytes, bytes, int]:
    header = await _recv_exact(reader, 7, deadline)
    r_txn, proto, length, r_unit = struct.unpack(">HHHB", header)
    if proto != 0:
        raise ModbusFailure("protocol_error",
                            f"MBAP protocol id 應為 0，收到 {proto}",
                            hint="對方可能不是 Modbus TCP（也許是 RTU over TCP，試 framer='rtu'）。")
    if length < 2:
        raise ModbusFailure("protocol_error", f"MBAP length 欄位不合法：{length}")
    if r_txn != txn:
        raise ModbusFailure("protocol_error",
                            f"transaction id 不符（送出 {txn}，收到 {r_txn}）",
                            hint="可能有多個 client 共用同一條連線。")
    body = await _recv_exact(reader, length - 1, deadline)
    return body, header + body, r_unit


async def _read_rtu(reader, deadline: float) -> tuple[bytes, bytes, int]:
    head = await _recv_exact(reader, 2, deadline)
    fc = head[1]
    if fc & 0x80:
        rest = await _recv_exact(reader, 3, deadline)
    elif fc in codec.READ_FUNCTIONS:
        byte_count = await _recv_exact(reader, 1, deadline)
        rest = byte_count + await _recv_exact(reader, byte_count[0] + 2, deadline)
    elif fc in (codec.FC_WRITE_SINGLE_COIL, codec.FC_WRITE_SINGLE_REGISTER,
                codec.FC_WRITE_MULTIPLE_COILS, codec.FC_WRITE_MULTIPLE_REGISTERS):
        rest = await _recv_exact(reader, 6, deadline)
    else:
        raise ModbusFailure(
            "protocol_error", f"回應的功能碼無法辨識：0x{fc:02X}",
            hint="若不是 Modbus 設備，改用 tcp_check 只測連通性。")
    frame = head + rest
    expected_crc = struct.unpack("<H", frame[-2:])[0]
    actual_crc = codec.crc16(frame[:-2])
    if expected_crc != actual_crc:
        raise ModbusFailure(
            "crc_error",
            f"RTU CRC 不符（收到 0x{expected_crc:04X}，計算得 0x{actual_crc:04X}）",
            hint="訊框可能被截斷或錯位；確認 framer 是否該用 'tcp'。")
    return frame[1:-2], frame, frame[0]


def _raise_if_exception(pdu: bytes) -> None:
    if pdu and (pdu[0] & 0x80):
        code = pdu[1] if len(pdu) > 1 else 0
        name, meaning = codec.EXCEPTION_CODES.get(code, ("UNKNOWN", "未定義的例外碼"))
        raise ModbusFailure(
            "modbus_exception",
            f"設備回報例外 0x{code:02X} ({name})：{meaning}",
            hint=meaning, exception_code=code)


# --------------------------------------------------------------------------
# High level operations
# --------------------------------------------------------------------------
def _parse_area(area: str) -> int:
    key = (area or "").strip().lower()
    if key not in codec.READ_AREAS:
        raise ValueError(f"area 必須是以下之一：{sorted(set(codec.READ_AREAS))}")
    return codec.READ_AREAS[key]


async def read(target: Target, area: str, address: int, count: int = 1, *,
               timeout: float = DEFAULT_TIMEOUT, data_type: str = "uint16",
               word_order: str = "big", byte_order: str = "big") -> dict:
    """Read coils/discrete inputs/holding/input registers."""
    fc = _parse_area(area)
    is_bits = fc in codec.BIT_AREAS
    limit = codec.MAX_READ_BITS if is_bits else codec.MAX_READ_REGISTERS
    if count < 1 or count > limit:
        return failure("invalid_argument",
                       f"{area} 的 count 必須介於 1..{limit}，收到 {count}。",
                       target=target.label)
    if address < 0 or address > 0xFFFF:
        return failure("invalid_argument", f"address 必須介於 0..65535，收到 {address}。",
                       target=target.label)

    pdu = codec.build_read_pdu(fc, address, count)
    try:
        tx = await transact(target, pdu, timeout=timeout)
        _raise_if_exception(tx["pdu"])
        raw = codec.parse_read_response(tx["pdu"], fc, count)
    except ModbusFailure as exc:
        return failure(exc.kind, f"讀取失敗：{exc.detail}", target=target.label,
                       stage=exc.stage, latency_ms=None, error=exc.to_json())
    except ValueError as exc:
        return failure("protocol_error", f"回應解析失敗：{exc}", target=target.label)

    payload: dict = {
        "target": target.label,
        "area": area.lower(),
        "function": f"FC{fc:02d}",
        "address": address,
        "count": count,
        "raw": raw,
        "latency_ms": tx["rtt_ms"],
        "frame_tx": tx["frame_tx"],
        "frame_rx": tx["frame_rx"],
    }
    if is_bits:
        payload["values"] = [bool(v) for v in raw]
        summary = (f"{target.label} 讀取 {count} 個 {area}（起始 {address}）成功，"
                   f"共 {sum(raw)} 個 ON，耗時 {tx['rtt_ms']} ms。")
    else:
        try:
            need = registers_per_value(data_type)
            usable = len(raw) - (len(raw) % need)
            decoded = decode_registers(raw[:usable], data_type, word_order, byte_order)
        except ValueError as exc:
            decoded = []
            payload["decode_error"] = str(exc)
        payload["values"] = decoded
        payload["data_type"] = data_type
        if data_type not in ("uint16", "hex"):
            payload["word_order"] = word_order
            payload["byte_order"] = byte_order
        shown = ", ".join(str(v) for v in (decoded or raw)[:8])
        more = " …" if len(raw) > 8 else ""
        summary = (f"{target.label} 讀取 {count} 個 {area}（起始 {address}）成功："
                   f"[{shown}{more}]，耗時 {tx['rtt_ms']} ms。")
    return result(True, summary, **payload)


async def write(target: Target, area: str, address: int, values: list, *,
                timeout: float = DEFAULT_TIMEOUT, data_type: str = "uint16",
                word_order: str = "big", byte_order: str = "big") -> dict:
    """Write coils or holding registers. Returns echo + latency."""
    fc_read = _parse_area(area)
    key = area.strip().lower()
    if fc_read in codec.BIT_AREAS:
        if key not in ("coil", "coils"):
            return failure("invalid_argument",
                           f"{area} 是唯讀區域，無法寫入（只有 coil 可寫）。",
                           target=target.label)
        bits = [1 if v else 0 for v in values]
        if len(bits) == 1:
            fc = codec.FC_WRITE_SINGLE_COIL
            pdu = codec.build_write_single_pdu(fc, address, 0xFF00 if bits[0] else 0x0000)
        else:
            fc = codec.FC_WRITE_MULTIPLE_COILS
            pdu = codec.build_write_multiple_pdu(fc, address, bits)
        limit = 1 if fc == codec.FC_WRITE_SINGLE_COIL else codec.MAX_WRITE_BITS
        total = len(bits)
        echo_value = bits[0]
        registers: list[int] = []
    else:
        if key not in ("holding", "holding_register", "holding_registers"):
            return failure("invalid_argument",
                           f"{area} 是唯讀區域，無法寫入（只有 holding register 可寫）。",
                           target=target.label)
        try:
            registers = _to_registers(values, data_type, word_order, byte_order)
        except (TypeError, ValueError, struct.error) as exc:
            return failure("invalid_argument", f"數值無法轉成 register：{exc}",
                           target=target.label)
        if len(registers) == 1:
            fc = codec.FC_WRITE_SINGLE_REGISTER
            pdu = codec.build_write_single_pdu(fc, address, registers[0])
        else:
            fc = codec.FC_WRITE_MULTIPLE_REGISTERS
            pdu = codec.build_write_multiple_pdu(fc, address, registers)
        limit = 1 if fc == codec.FC_WRITE_SINGLE_REGISTER else codec.MAX_WRITE_REGISTERS
        total = len(registers)
        echo_value = registers[0]

    if total < 1 or total > limit:
        return failure("invalid_argument",
                       f"一次寫入數量必須介於 1..{limit}（register/coil 數），收到 {total}。",
                       target=target.label)

    try:
        tx = await transact(target, pdu, timeout=timeout)
        _raise_if_exception(tx["pdu"])
        _verify_write_echo(tx["pdu"], fc, address, echo_value)
    except ModbusFailure as exc:
        return failure(exc.kind, f"寫入失敗：{exc.detail}", target=target.label,
                       stage=exc.stage, error=exc.to_json())
    payload = {
        "target": target.label,
        "function": f"FC{fc:02d}",
        "address": address,
        "written_values": values,
        "written_count": total,
        "latency_ms": tx["rtt_ms"],
        "frame_tx": tx["frame_tx"],
        "frame_rx": tx["frame_rx"],
    }
    if registers:
        payload["written_registers"] = registers
    return result(
        True,
        f"{target.label} 寫入成功（FC{fc:02d}，位址 {address}，{total} 個值），"
        f"耗時 {tx['rtt_ms']} ms。",
        **payload,
    )


def _verify_write_echo(pdu: bytes, fc: int, address: int,
                       expected_single: int) -> None:
    """Check the device echoed our write (spec requires echo for FC05/06/15/16)."""
    if len(pdu) < 5:
        raise ModbusFailure("protocol_error", f"寫入回應過短：{_hex(pdu)}")
    r_fc = pdu[0]
    if r_fc != fc:
        raise ModbusFailure("protocol_error",
                            f"回應功能碼不符（送出 FC{fc:02d}，收到 FC{r_fc:02d}）")
    r_addr, r_value = struct.unpack(">HH", pdu[1:5])
    if r_addr != address:
        raise ModbusFailure("protocol_error",
                            f"回應位址不符（送出 {address}，收到 {r_addr}）")
    if fc == codec.FC_WRITE_SINGLE_COIL:
        expected = 0xFF00 if expected_single else 0x0000
    elif fc == codec.FC_WRITE_SINGLE_REGISTER:
        expected = int(expected_single) & 0xFFFF
    else:
        return
    if r_value != expected:
        raise ModbusFailure("protocol_error",
                            f"回應值不符（送出 0x{expected:04X}，收到 0x{r_value:04X}）")


def _to_registers(values: list, data_type: str, word_order: str,
                  byte_order: str) -> list[int]:
    """Convert numeric values into raw 16-bit registers for a write."""
    sixteen_bit = {"uint16", "int16", "short", "word", "hex"}
    if data_type in sixteen_bit:
        out: list[int] = []
        for value in values:
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{value} 有小數，但 data_type={data_type} 是整數型別")
            raw = int(value)
            if data_type == "uint16" and not (0 <= raw <= 0xFFFF):
                raise ValueError(f"{raw} 超出 uint16 範圍（0..65535）")
            if data_type == "int16" and not (-32768 <= raw <= 32767):
                raise ValueError(f"{raw} 超出 int16 範圍（-32768..32767）")
            out.append(raw & 0xFFFF)
        return out
    return encode_registers(values, data_type, word_order, byte_order)


async def probe(target: Target, *, probe_address: int = 0, probe_count: int = 1,
                timeout: float = DEFAULT_TIMEOUT) -> dict:
    """Minimal read to prove the Modbus layer answers (not just TCP).

    Tries FC03 first; if the device rejects the function code (0x01) it retries
    with FC04, which covers devices that only expose input registers.
    """
    attempts: list[dict] = []
    for index, fc in enumerate((codec.FC_READ_HOLDING_REGISTERS,
                                codec.FC_READ_INPUT_REGISTERS)):
        pdu = codec.build_read_pdu(fc, probe_address, probe_count)
        try:
            tx = await transact(target, pdu, timeout=timeout)
        except ModbusFailure as exc:
            attempts.append({"function": f"FC{fc:02d}", "ok": False, "error": exc.to_json()})
            if exc.stage == "tcp":
                return failure(exc.kind, f"連線失敗：{exc.detail}", target=target.label,
                               stage="tcp", error=exc.to_json(), attempts=attempts)
            return failure(
                "modbus_timeout",
                f"{target.label} TCP 通了，但 Modbus 沒有回應。",
                hint=("最常見：unit id 錯誤、設備忙碌、或 RTU-over-TCP 的序列參數不符。"),
                target=target.label, stage="modbus", responded=False,
                error=exc.to_json(), attempts=attempts)

        pdu_rx = tx["pdu"]
        if pdu_rx and (pdu_rx[0] & 0x80):
            code = pdu_rx[1] if len(pdu_rx) > 1 else 0
            name, meaning = codec.EXCEPTION_CODES.get(code, ("UNKNOWN", "未定義的例外碼"))
            attempts.append({"function": f"FC{fc:02d}", "ok": True, "responded": True,
                             "exception_code": f"0x{code:02X}", "exception_name": name})
            if code in codec.ABSENT_SLAVE_EXCEPTIONS:
                return failure(
                    "modbus_gateway_no_target",
                    f"{target.label} 的 gateway 有回應，但回報找不到目標 slave"
                    f"（例外 0x{code:02X} {name}）。",
                    hint="多半是 unit id 不對，或該 slave 未上線 / 匯流排接線問題。",
                    target=target.label, stage="modbus", responded=True,
                    error={"kind": "modbus_gateway_no_target",
                           "exception_code": f"0x{code:02X}",
                           "exception_name": name, "hint": meaning},
                    attempts=attempts)
            if code == 0x01 and index == 0:
                continue  # this device has no holding registers, try input registers
            return result(
                True,
                f"{target.label} 有 Modbus 應答（FC{fc:02d} 回例外 0x{code:02X} {name}：{meaning}），"
                f"代表設備與 unit id 正確，但該功能/位址不支援。耗時 {tx['rtt_ms']} ms。",
                target=target.label, stage="modbus", responded=True,
                exception_code=f"0x{code:02X}", exception_name=name,
                latency_ms=tx["rtt_ms"], attempts=attempts,
                frame_tx=tx["frame_tx"], frame_rx=tx["frame_rx"])
        try:
            raw = codec.parse_read_response(pdu_rx, fc, probe_count)
        except ValueError as exc:
            return failure("protocol_error",
                           f"{target.label} 有回應但格式不符：{exc}",
                           target=target.label, stage="modbus",
                           frame_rx=tx["frame_rx"])
        return result(
            True,
            f"{target.label} Modbus 應答正常（FC{fc:02d} 位址 {probe_address} 讀到 {raw}），"
            f"耗時 {tx['rtt_ms']} ms。",
            target=target.label, stage="modbus", responded=True, function=f"FC{fc:02d}",
            address=probe_address, raw=raw, latency_ms=tx["rtt_ms"],
            attempts=attempts, frame_tx=tx["frame_tx"], frame_rx=tx["frame_rx"])

    return failure(
        "protocol_error", f"{target.label} 兩次探測都沒有得到有效回應。",
        target=target.label, stage="modbus", attempts=attempts)


async def scan_units(target: Target, unit_start: int = 1, unit_end: int = 247, *,
                     timeout: float = 1.0, concurrency: int = 4,
                     probe_address: int = 0) -> dict:
    """Poll a unit-id range to find live slaves."""
    unit_start = max(0, unit_start)
    unit_end = min(247, unit_end)
    if unit_start > unit_end:
        return failure("invalid_argument",
                       f"unit 範圍不合法：{unit_start}..{unit_end}", target=target.label)
    total_units = unit_end - unit_start + 1
    if total_units > 247:
        return failure("invalid_argument", "unit 範圍最多 247 個。", target=target.label)

    semaphore = asyncio.Semaphore(max(1, min(concurrency, 16)))
    found: list[dict] = []

    async def check(unit: int) -> None:
        async with semaphore:
            probe_target = Target(host=target.host, port=target.port, unit=unit,
                                  framer=target.framer, alias=target.alias)
            outcome = await probe(probe_target, probe_address=probe_address, timeout=timeout)
            if outcome.get("responded"):
                found.append({
                    "unit": unit,
                    "status": "responded",
                    "exception": outcome.get("exception_name"),
                    "latency_ms": outcome.get("latency_ms"),
                })

    budget = min(120.0, total_units * timeout / max(1, concurrency) + 5.0)
    try:
        await asyncio.wait_for(
            asyncio.gather(*(check(u) for u in range(unit_start, unit_end + 1))),
            timeout=budget)
    except TimeoutError:
        pass

    found.sort(key=lambda item: item["unit"])
    if not found:
        return result(
            False,
            f"{target.host}:{target.port} 在 unit {unit_start}..{unit_end} 之間沒有設備回應。",
            target=f"{target.host}:{target.port}", units_found=[],
            scanned=f"{unit_start}..{unit_end}",
            error={"kind": "no_slave_responded",
                   "hint": "確認設備有上電、unit id 範圍，或先跑 tcp_check 確認連通性。"})
    return result(
        True,
        f"{target.host}:{target.port} 找到 {len(found)} 個有回應的 unit："
        f"{', '.join(str(item['unit']) for item in found)}。",
        target=f"{target.host}:{target.port}", units_found=found,
        scanned=f"{unit_start}..{unit_end}")
