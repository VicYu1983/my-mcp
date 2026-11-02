"""Result shapes shared by every tool.

Every tool returns a plain dict that always carries `ok` and `summary`, so the
model can answer from two fields alone:

    { "ok": false, "summary": "...", "error": { "kind": ..., "hint": ... } }

Extra structured fields are there for when the model (or you) wants detail.
"""

from __future__ import annotations

import struct
from typing import Any


# --------------------------------------------------------------------------
# Result builders
# --------------------------------------------------------------------------
def result(ok: bool, summary: str, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": ok, "summary": summary}
    out.update({k: v for k, v in fields.items() if v is not None})
    return out


def failure(kind: str, summary: str, *, detail: str | None = None,
            hint: str | None = None, **fields: Any) -> dict[str, Any]:
    err: dict[str, Any] = {"kind": kind}
    if detail:
        err["detail"] = detail
    if hint:
        err["hint"] = hint
    out: dict[str, Any] = {"ok": False, "summary": summary, "error": err}
    out.update({k: v for k, v in fields.items() if v is not None})
    return out


# --------------------------------------------------------------------------
# Register decoding
# --------------------------------------------------------------------------
_UINT16 = {"uint16": "H", "int16": "h", "short": "h", "word": "H", "hex": "H"}
_32BIT = {"uint32": "I", "int32": "i", "float32": "f", "float": "f", "dword": "I"}
_64BIT = {"uint64": "Q", "int64": "q", "float64": "d", "double": "d"}

DATA_TYPES = {**_UINT16, **_32BIT, **_64BIT}


def registers_per_value(data_type: str) -> int:
    if data_type in _UINT16:
        return 1
    if data_type in _32BIT:
        return 2
    if data_type in _64BIT:
        return 4
    raise ValueError(f"unknown data_type: {data_type!r}")


def decode_registers(values: list[int], data_type: str = "uint16",
                     word_order: str = "big", byte_order: str = "big") -> list[Any]:
    """Decode raw 16-bit registers into python values.

    word_order: "big" = high word first (ABCD), "little" = low word first (CDAB)
    byte_order: "big" = AB, "little" = BA (byte swap inside each register)
    """
    per_value = registers_per_value(data_type)
    if len(values) % per_value:
        raise ValueError(
            f"{len(values)} registers is not a multiple of {per_value} for {data_type}")
    if word_order not in ("big", "little"):
        raise ValueError("word_order must be 'big' or 'little'")
    if byte_order not in ("big", "little"):
        raise ValueError("byte_order must be 'big' or 'little'")

    fmt = ">" + (_UINT16.get(data_type) or _32BIT.get(data_type) or _64BIT[data_type])
    swap_bytes = byte_order == "little"

    out: list[Any] = []
    for i in range(0, len(values), per_value):
        chunk = values[i:i + per_value]
        words = [struct.pack("<H" if swap_bytes else ">H", v & 0xFFFF) for v in chunk]
        if word_order == "little":
            words.reverse()
        out.append(struct.unpack(fmt, b"".join(words))[0])
    return out


def encode_registers(values: list[Any], data_type: str = "uint16",
                     word_order: str = "big", byte_order: str = "big") -> list[int]:
    """Inverse of decode_registers: python values -> raw 16-bit registers."""
    per_value = registers_per_value(data_type)
    fmt = ">" + (_UINT16.get(data_type) or _32BIT.get(data_type) or _64BIT[data_type])
    pack_bytes = "<H" if byte_order == "little" else ">H"

    out: list[int] = []
    for value in values:
        raw = struct.pack(fmt, value)
        words = [struct.unpack(pack_bytes, raw[j:j + 2])[0]
                 for j in range(0, 2 * per_value, 2)]
        if word_order == "little":
            words.reverse()
        out.extend(words)
    return out
