"""Diagnostics: repeat-retry statistics for one target, batch health check for many."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import statistics
import time

from . import client
from .config import ConfigStore, get_store
from .models import failure, result
from .safety import Target
from .transport import tcp_check


# --------------------------------------------------------------------------
# Single target: retry + statistics
# --------------------------------------------------------------------------
async def diagnose(target: Target, *, attempts: int = 5, timeout: float = 2.0,
                   probe_address: int = 0, interval_ms: int = 200) -> dict:
    attempts = max(1, min(int(attempts), 20))
    latencies: list[float] = []
    kinds: dict[str, int] = {}
    stages: dict[str, int] = {}
    responses: list[dict] = []
    ok_count = 0

    for index in range(attempts):
        outcome = await client.probe(target, probe_address=probe_address, timeout=timeout)
        responses.append({
            "attempt": index + 1,
            "ok": outcome.get("ok", False),
            "latency_ms": outcome.get("latency_ms"),
            "error_kind": (outcome.get("error") or {}).get("kind"),
        })
        if outcome.get("ok"):
            ok_count += 1
            if outcome.get("latency_ms") is not None:
                latencies.append(float(outcome["latency_ms"]))
        else:
            err = outcome.get("error") or {}
            kind = err.get("kind") or "unknown"
            kinds[kind] = kinds.get(kind, 0) + 1
            stage = outcome.get("stage") or "modbus"
            stages[stage] = stages.get(stage, 0) + 1
        if index + 1 < attempts and interval_ms > 0:
            await asyncio.sleep(interval_ms / 1000)

    success_rate = round(100.0 * ok_count / attempts, 1)
    stats = {
        "attempts": attempts,
        "success": ok_count,
        "failed": attempts - ok_count,
        "success_rate_pct": success_rate,
    }
    if latencies:
        stats.update({
            "latency_ms_min": round(min(latencies), 2),
            "latency_ms_avg": round(statistics.fmean(latencies), 2),
            "latency_ms_max": round(max(latencies), 2),
            "jitter_ms": round(statistics.pstdev(latencies), 2) if len(latencies) > 1 else 0.0,
        })
    if kinds:
        stats["error_kinds"] = kinds
    if stages:
        stats["failure_stages"] = stages

    if ok_count == attempts:
        verdict = "stable"
        summary = (f"{target.label} {attempts}/{attempts} 全部成功"
                   f"（平均 {stats.get('latency_ms_avg')} ms，抖動 {stats.get('jitter_ms')} ms），連線穩定。")
    elif ok_count == 0:
        verdict = "down" if stages.get("tcp") else "no_modbus_response"
        if stages.get("tcp"):
            summary = (f"{target.label} {attempts} 次全部失敗在 TCP 層"
                       f"（{', '.join(f'{k}×{v}' for k, v in kinds.items())}），連線不通。")
        else:
            summary = (f"{target.label} {attempts} 次全部失敗在 Modbus 層"
                       f"（{', '.join(f'{k}×{v}' for k, v in kinds.items())}），"
                       f"TCP 通但設備沒有正常應答。")
    else:
        verdict = "flaky"
        summary = (f"{target.label} {ok_count}/{attempts} 成功（{success_rate}%），"
                   f"屬於間歇性問題 — 可能是線路品質、設備負載或連線數上限。")

    return result(
        True,
        summary,
        target=target.label,
        verdict=verdict,
        stats=stats,
        attempts_detail=responses,
    )


# --------------------------------------------------------------------------
# Batch: check every whitelisted target
# --------------------------------------------------------------------------
def _expand_targets(store: ConfigStore, scope: str, *, default_port: int,
                    protocol_filter: str | None,
                    site: str | None = None) -> tuple[list[Target], list[str]]:
    targets: list[Target] = []
    notes: list[str] = []

    def add(host: str, port: int, framer: str, alias: str | None, source: str) -> None:
        if protocol_filter and framer != protocol_filter:
            return
        targets.append(Target(host=host, port=port, unit=1, framer=framer,
                              alias=alias, source=source))

    if scope in ("devices", "both"):
        for device in store.devices(site):
            add(device.host, device.port, device.framer, device.alias, "devices.json")

    if scope in ("whitelist", "both"):
        for entry in store.whitelist(site):
            framer = entry.protocol or "tcp"
            ports = entry.ports or [default_port]
            raw = entry.target.strip()
            if "/" in raw:
                try:
                    network = ipaddress.ip_network(raw, strict=False)
                except ValueError:
                    notes.append(f"忽略無法解析的網段：{raw}")
                    continue
                hosts = list(network.hosts()) if network.prefixlen <= 30 else list(network)
                if len(hosts) > 256:
                    notes.append(f"{raw} 過大（{len(hosts)} 台），已略過；請在 whitelist 拆成更小的網段。")
                    continue
                for ip in hosts:
                    for port in ports:
                        add(str(ip), port, framer, None, f"whitelist:{raw}")
            else:
                for port in ports:
                    add(raw, port, framer, None, f"whitelist:{raw}")

    seen: set[tuple[str, int, int, str]] = set()
    unique: list[Target] = []
    for target in targets:
        key = (target.host, target.port, target.unit, target.framer)
        if key in seen:
            continue
        seen.add(key)
        unique.append(target)
    return unique, notes


async def check_all(store: ConfigStore | None = None, *, scope: str = "whitelist",
                    level: str = "probe", concurrency: int = 8,
                    timeout: float = 1.5, max_targets: int = 32,
                    protocol_filter: str | None = None,
                    site: str | None = None) -> dict:
    store = store or get_store()
    scope = (scope or "whitelist").lower()
    level = (level or "probe").lower()
    if scope not in ("whitelist", "devices", "both"):
        return failure("invalid_argument", "scope 必須是 whitelist / devices / both。")
    if level not in ("tcp", "probe", "read"):
        return failure("invalid_argument", "level 必須是 tcp / probe / read。")

    hard_cap = min(max(1, int(max_targets)), 256)
    env_cap = int(os.environ.get("MODBUS_MAX_CONCURRENCY", "16"))
    limit = max(1, min(int(concurrency), env_cap, 32))
    if protocol_filter:
        protocol_filter = protocol_filter.lower()
        if protocol_filter not in ("tcp", "rtu"):
            return failure("invalid_argument", "protocol_filter 必須是 tcp 或 rtu。")

    targets, notes = _expand_targets(store, scope, default_port=502,
                                     protocol_filter=protocol_filter, site=site)
    if not targets:
        return result(False, "白名單 / 設備表裡沒有可檢查的目標。",
                      counts={"total": 0}, results=[], notes=notes,
                      error={"kind": "no_targets",
                             "hint": "先用 whitelist_add 加入目標，或手動編輯 config/whitelist.json。"})
    if len(targets) > hard_cap:
        return failure(
            "too_many_targets",
            f"這次要檢查 {len(targets)} 個目標，超過上限 {hard_cap} 個，已中止（未發出任何連線）。",
            hint=("請縮小範圍：改用 scope='devices'、加 protocol_filter、"
                  "或提高 max_targets（硬上限 256）。"),
            counts={"total": len(targets), "limit": hard_cap}, notes=notes)

    semaphore = asyncio.Semaphore(limit)

    async def check_one(target: Target) -> dict:
        async with semaphore:
            entry: dict = {"target": target.label, "host": target.host,
                           "port": target.port, "framer": target.framer,
                           "source": target.source, "alias": target.alias}
            if level == "tcp":
                outcome = await tcp_check(target.host, target.port, timeout)
            else:
                outcome = await client.probe(target, timeout=timeout)
                if level == "read" and outcome.get("ok"):
                    readback = await client.read(target, "holding", 0, 1, timeout=timeout)
                    if not readback.get("ok"):
                        readback = await client.read(target, "input", 0, 1, timeout=timeout)
                    entry["read_check"] = {
                        "ok": readback.get("ok"),
                        "values": readback.get("raw") or readback.get("values"),
                        "error_kind": (readback.get("error") or {}).get("kind"),
                    }
                    entry["ok"] = bool(readback.get("ok"))
                    entry["stage"] = "read"
                    entry["latency_ms"] = readback.get("latency_ms") or outcome.get("latency_ms")
                    entry["error"] = readback.get("error")
                    return entry
            entry["ok"] = bool(outcome.get("ok"))
            entry["stage"] = outcome.get("stage")
            entry["latency_ms"] = outcome.get("latency_ms")
            if outcome.get("error"):
                entry["error"] = outcome["error"]
            if outcome.get("exception_name"):
                entry["exception_name"] = outcome["exception_name"]
            return entry

    deadline = 60.0
    started = time.perf_counter()
    timed_out = False
    try:
        entries = await asyncio.wait_for(
            asyncio.gather(*(check_one(t) for t in targets)), timeout=deadline)
    except TimeoutError:
        timed_out = True
        entries = []

    elapsed = round(time.perf_counter() - started, 2)
    if timed_out:
        return failure(
            "timeout_budget_exceeded",
            f"批量檢查超過 {int(deadline)} 秒總預算，已中止（{len(targets)} 個目標未全部完成）。",
            hint="把 scope 縮小、降低 timeout，或分批檢查。",
            elapsed_seconds=elapsed, counts={"total": len(targets)}, notes=notes)

    entries.sort(key=lambda item: (item["ok"], item["host"], item["port"]))
    ok_count = sum(1 for e in entries if e["ok"])
    tcp_fail = sum(1 for e in entries if not e["ok"] and (e.get("stage") or "").startswith("tcp"))
    modbus_fail = len(entries) - ok_count - tcp_fail
    counts = {"total": len(entries), "ok": ok_count,
              "tcp_fail": tcp_fail, "modbus_fail": modbus_fail}

    if ok_count == len(entries):
        summary = f"檢查 {len(entries)} 個目標：全部 {ok_count} 個正常（耗時 {elapsed}s）。"
    else:
        parts = [f"正常 {ok_count}"]
        if tcp_fail:
            parts.append(f"TCP 不通 {tcp_fail}")
        if modbus_fail:
            parts.append(f"TCP 通但 Modbus 無回應 {modbus_fail}")
        summary = (f"檢查 {len(entries)} 個目標：" + "、".join(parts) + f"（耗時 {elapsed}s）。")

    return result(
        ok_count == len(entries),
        summary,
        counts=counts,
        elapsed_seconds=elapsed,
        level=level,
        concurrency=limit,
        results=entries,
        notes=notes or None,
    )
