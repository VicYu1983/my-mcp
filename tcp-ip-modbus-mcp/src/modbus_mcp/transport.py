"""TCP transport helpers: connectivity check and error classification."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import socket
import time

from .models import result

# errno values that mean "host/net not reachable at all"
_UNREACHABLE_ERRNOS = {
    getattr(errno, "EHOSTUNREACH", 113),
    getattr(errno, "ENETUNREACH", 101),
    getattr(errno, "EHOSTDOWN", 112),
    # Windows WSA*
    10051, 10064, 10065,
}
_REFUSED_ERRNOS = {getattr(errno, "ECONNREFUSED", 111), 10061}
_TIMEOUT_ERRNOS = {getattr(errno, "ETIMEDOUT", 110), 10060}
_RESET_ERRNOS = {getattr(errno, "ECONNRESET", 104), 10054}

ERROR_HINTS = {
    "refused": "目標主機活著但該埠沒有服務在聽（PLC/網關未開、或埠號不對）。",
    "timeout": "完全沒有回應，常見原因：IP 不在同一網段、防火牆擋住、設備未上電。",
    "dns_fail": "名稱解析失敗，檢查 hostname 拼字或 DNS 設定。",
    "unreachable": "路由可達性失敗（沒有到該網段的路由 / 網路未連線）。",
    "reset": "連線被對方重置（服務存在但拒絕這個連線，常見於連線數已滿）。",
    "closed": "連線建立後立刻被對方關閉。",
    "invalid": "參數不合法（host/port）。",
    "socket_error": "未分類的 socket 錯誤，詳見 detail。",
}


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an exception to (kind, detail)."""
    if isinstance(exc, socket.gaierror):
        return "dns_fail", str(exc)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "timeout", "connect timed out"
    if isinstance(exc, ConnectionRefusedError):
        return "refused", str(exc) or "connection refused"
    if isinstance(exc, ConnectionResetError):
        return "reset", str(exc) or "connection reset"
    if isinstance(exc, OSError):
        code = getattr(exc, "errno", None)
        if code in _REFUSED_ERRNOS:
            return "refused", str(exc)
        if code in _TIMEOUT_ERRNOS:
            return "timeout", str(exc)
        if code in _UNREACHABLE_ERRNOS:
            return "unreachable", str(exc)
        if code in _RESET_ERRNOS:
            return "reset", str(exc)
        return "socket_error", f"{exc.__class__.__name__}: {exc}"
    return "socket_error", f"{exc.__class__.__name__}: {exc}"


async def open_connection(host: str, port: int, timeout: float):
    """Open a TCP connection, returning (reader, writer, rtt_ms).

    Raises the original exception; callers use classify_error() on it.
    """
    start = time.perf_counter()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout)
    rtt_ms = round((time.perf_counter() - start) * 1000, 2)
    return reader, writer, rtt_ms


async def close_connection(writer) -> None:
    with contextlib.suppress(ConnectionError, OSError, RuntimeError, TimeoutError):
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)


async def tcp_check(host: str, port: int, timeout: float = 2.0) -> dict:
    """Pure TCP handshake check; no Modbus traffic."""
    if not host or not (0 < port < 65536):
        return result(False, f"參數不合法：{host}:{port}。",
                      target=f"{host}:{port}",
                      error={"kind": "invalid", "hint": ERROR_HINTS["invalid"]})
    try:
        _reader, writer, rtt_ms = await open_connection(host, port, timeout)
    except Exception as exc:  # noqa: BLE001 - classified below
        kind, detail = classify_error(exc)
        summary = {
            "timeout": f"{host}:{port} TCP 連線逾時（{int(timeout * 1000)}ms）。",
            "refused": f"{host}:{port} TCP 被拒絕：該埠沒有服務在聽。",
            "dns_fail": f"{host} 名稱解析失敗。",
            "unreachable": f"{host}:{port} 網路不可達。",
            "reset": f"{host}:{port} 連線被重置。",
        }.get(kind, f"{host}:{port} TCP 連線失敗：{kind}。")
        return result(False, summary, target=f"{host}:{port}", stage="tcp",
                      error={"kind": kind, "detail": detail,
                             "hint": ERROR_HINTS.get(kind, "")})

    peer = writer.get_extra_info("peername")
    local = writer.get_extra_info("sockname")
    await close_connection(writer)
    return result(True, f"{host}:{port} TCP 可連通，握手耗時 {rtt_ms} ms。",
                  target=f"{host}:{port}", stage="tcp", latency_ms=rtt_ms,
                  peer=_fmt_addr(peer), local_address=_fmt_addr(local))


def _fmt_addr(addr) -> str | None:
    if not addr:
        return None
    if isinstance(addr, tuple) and len(addr) >= 2:
        return f"{addr[0]}:{addr[1]}"
    return str(addr)
