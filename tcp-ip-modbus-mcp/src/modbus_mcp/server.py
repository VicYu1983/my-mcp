"""MCP server: TCP/IP + Modbus diagnostics tools.

Run as stdio MCP server:  python -m modbus_mcp   (or the `modbus-mcp` script)
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import client, diagnostics
from .config import Device, get_store
from .models import failure, result
from .safety import DEFAULT_TEMP_TTL, Gate, resolve_target
from .transport import tcp_check as _tcp_check

INSTRUCTIONS = """\
這個 server 讓你能實際探測 TCP/IP 連線與 Modbus 設備，而不是憑猜測回答。

使用原則：
1. 「連不上」先分層：先用 tcp_check 判斷 TCP 層，再用 modbus_probe 判斷 Modbus 層。
   兩者都過才輪到 modbus_read 讀值。
2. 目標可用 IP 或 devices.json 裡的別名（例如「一號機」），先用 device_list 看看有哪些。
3. 所有目標都必須在白名單內。若工具回傳 needs_authorization，把 summary 轉述給使用者，
   等使用者明確同意後才呼叫 authorize_temporary（一次性）或 whitelist_add（永久，需附同意文字）。
4. 寫入 (modbus_write) 預設 dry_run=true 只做預演。真正執行需要使用者明確同意，
   並帶 dry_run=false、confirm=true、user_consent=<使用者的原話>。
5. 要一次看全部設備健康狀況時用 check_all。
6. 回答使用者時，優先引用工具回傳的 summary 與 error.hint，不要自行編造錯誤原因。
7. 每個案場（客戶 / 廠區）都有自己的白名單與設備表。開始做事前先 site_list 看
   目前是哪個案場；切換用 site_use。白名單與設備的變更只會動到「作用中的案場」，
   除非你明確帶上 site 參數。
"""

mcp = MCPServer("tcp-ip-modbus", instructions=INSTRUCTIONS)

_store = get_store()
_gate = Gate(
    _store,
    resolve_hostnames=os.environ.get("MODBUS_RESOLVE_HOSTNAMES", "true").lower()
    not in ("0", "false", "no", "off"),
    temp_ttl_default=int(os.environ.get("MODBUS_TEMP_TTL", DEFAULT_TEMP_TTL)),
)

_DEFAULT_TIMEOUT_MS = int(os.environ.get("MODBUS_TIMEOUT_MS", "3000"))


def _seconds(timeout_ms: int | None) -> float:
    value = int(timeout_ms) if timeout_ms else _DEFAULT_TIMEOUT_MS
    return max(0.05, min(value, 60000) / 1000.0)


def _resolve(target: str, port: int | None, unit: int | None, framer: str | None,
             purpose: str, data_type: str | None = None):
    return resolve_target(_gate, target, port=port, unit=unit, framer=framer,
                          purpose=purpose, data_type=data_type)


# --------------------------------------------------------------------------
# Sites (案場)
# --------------------------------------------------------------------------
def _site_arg(site: str | None) -> str | None:
    """Resolve an optional site argument; raises ValueError with a friendly message."""
    if site is None:
        return None
    return _store.resolve_site(site)


@mcp.tool()
def site_list() -> dict:
    """列出所有案場（客戶 / 廠區），並標出目前作用中的一個。

    每個案場有自己獨立的白名單與設備表；換案場等於整組目標一起換掉。
    回答「現在在哪個案場、有哪些案場」時用這個。
    """
    sites = _store.sites()
    active = _store.active_site()
    active_label = active or "（未分案場：使用根目錄 config/whitelist.json）"
    if not sites:
        return result(True,
                      f"目前沒有建立任何案場，作用範圍是根目錄設定。{_site_hint()}",
                      active_site=active or None, sites=[],
                      sites_file=str(_store.sites_path),
                      sites_dir=str(_store.sites_dir))
    lines = "、".join(f"{s.id}(白名單 {s.whitelist_count} / 設備 {s.device_count})"
                      for s in sites[:8])
    return result(True,
                  f"共 {len(sites)} 個案場：{lines}"
                  + (" …" if len(sites) > 8 else "")
                  + f"。目前作用中：{active_label}。",
                  active_site=active or None,
                  sites=[s.to_json() for s in sites],
                  sites_file=str(_store.sites_path),
                  sites_dir=str(_store.sites_dir))


def _site_hint() -> str:
    return "（建立：site_create；切換：site_use）"


@mcp.tool()
def site_create(site: str, name: str = "", note: str = "",
                copy_from: str | None = None, activate: bool = True) -> dict:
    """建立一個新案場（或更新既有案場的名稱 / 備註），並可選擇直接切換過去。

    Args:
        site: 案場名稱 / 代號（例如「台中廠」「客戶A」）。會當成資料夾名稱，
            不可含 \\ / : * ? " < > |，不可用 _ 開頭。
        name: 顯示名稱（留空則與 site 相同）。
        note: 備註（客戶聯絡人、合約編號等）。
        copy_from: 用哪個案場當範本：留空 = 全新空白；"default" = 複製根目錄設定；
                  或填另一個案場名稱。
        activate: 建立後是否立即切換成作用中案場，預設 true。
    """
    try:
        info, created = _store.create_site(site, name=name, note=note,
                                           copy_from=copy_from, activate=activate)
    except ValueError as exc:
        return failure("invalid_argument", str(exc), hint=_site_hint())
    _store.audit({"event": "site_create", "site": info.id, "created": created,
                  "copy_from": copy_from, "activate": activate})
    return result(
        True,
        f"已{'建立' if created else '更新'}案場「{info.id}」"
        f"（白名單 {info.whitelist_count} 筆、設備 {info.device_count} 台）"
        + ("，並已切換為作用中案場。" if activate else "。")
        + (f" 範本來源：{copy_from}。" if copy_from else ""),
        site=info.to_json(), created=created, active_site=_store.active_site() or None)


@mcp.tool()
def site_use(site: str) -> dict:
    """切換作用中的案場（白名單與設備表會整組換掉）。

    填 "default" 會切回根目錄的設定（不分案場）。切換不會連線、不會改任何設備。

    Args:
        site: 案場名稱；site_list 可看現有清單。
    """
    previous = _store.active_site()
    try:
        target = _store.set_active_site(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    _store.audit({"event": "site_use", "site": target, "previous": previous})
    entries = _store.whitelist()
    devices = _store.devices()
    if not target:
        return result(True,
                      f"已切回根目錄設定（不分案場）：白名單 {len(entries)} 筆、"
                      f"設備 {len(devices)} 台。",
                      active_site=None, previous_site=previous or None,
                      whitelist_count=len(entries), devices_count=len(devices))
    return result(True,
                  f"已切換到案場「{target}」：白名單 {len(entries)} 筆、設備 {len(devices)} 台。"
                  + (f"（原：{previous}）" if previous and previous != target else ""),
                  active_site=target, previous_site=previous or None,
                  whitelist_count=len(entries), devices_count=len(devices))


@mcp.tool()
def site_show(site: str | None = None) -> dict:
    """看某個案場的內容（白名單 + 設備表），不切換、不連線。

    Args:
        site: 案場名稱；留空 = 目前作用中的案場（若無則看根目錄設定）。
    """
    try:
        wanted = _site_arg(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    label = wanted or "（根目錄 / 未分案場）"
    entries = _store.whitelist(wanted) if wanted is not None else _store.whitelist()
    devices = _store.devices(wanted) if wanted is not None else _store.devices()
    wl_path, dv_path = _store.paths_for(wanted)
    return result(True,
                  f"案場「{label}」：白名單 {len(entries)} 筆、設備 {len(devices)} 台。",
                  site=(wanted or _store.active_site()) or None,
                  whitelist=[e.to_json() for e in entries],
                  devices=[d.to_json() for d in devices],
                  whitelist_file=str(wl_path), devices_file=str(dv_path))


@mcp.tool()
def site_delete(site: str, user_consent: str = "") -> dict:
    """刪除一個案場（移到 config/sites/_trash/，不會真的銷毀，可手動救回）。

    使用中的案場不可刪除，請先用 site_use 切到別的案場。

    Args:
        site: 要刪除的案場名稱。
        user_consent: 必填。使用者同意刪除的原話，會寫入稽核紀錄。
    """
    if not user_consent.strip():
        return failure("consent_required",
                       "刪除案場需要使用者明確同意，請先取得同意再帶 user_consent。",
                       hint="刪除只是移到 sites/_trash/，但仍會讓該案場的白名單失效。")
    try:
        moved = _store.delete_site(site)
    except ValueError as exc:
        return failure("refused", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    _store.audit({"event": "site_delete", "site": site, "moved_to": str(moved),
                  "user_consent": user_consent.strip()})
    return result(True, f"已刪除案場「{site}」"
                        + (f"（已移到 {moved}，可手動救回）。" if moved else "（目錄不存在）。"),
                  site=site, moved_to=str(moved) if moved else None,
                  sites=[s.id for s in _store.sites()])


# --------------------------------------------------------------------------
# Whitelist & authorisation
# --------------------------------------------------------------------------
@mcp.tool()
def whitelist_list(site: str | None = None) -> dict:
    """列出目前可存取的目標（白名單內容 + 尚未到期的一次性授權）。

    回傳每筆的 target（IP / CIDR / hostname）、ports、protocol、note、加入時間。
    回答「現在允許查哪些設備」時用這個。

    Args:
        site: 看哪個案場；留空 = 目前作用中的案場。
    """
    try:
        wanted = _site_arg(site) if site is not None else None
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    entries = _store.whitelist() if wanted is None else _store.whitelist(wanted)
    wl_path, dv_path = _store.paths_for(wanted)
    active = _store.active_site()
    return result(
        True,
        f"白名單共 {len(entries)} 筆"
        + (f"（案場：{wanted or active}）" if (wanted or active) else "（根目錄設定）")
        + (f"，另有 {len(_gate.list_temp_auths())} 筆一次性授權未到期。"
           if _gate.list_temp_auths() else "，目前沒有一次性授權。"),
        whitelist=[entry.to_json() for entry in entries],
        temporary_authorizations=_gate.list_temp_auths(),
        active_site=active or None,
        whitelist_file=str(wl_path),
        devices_file=str(dv_path),
        write_enabled=_gate.writes_enabled(),
    )


@mcp.tool()
def whitelist_add(target: str, note: str = "", ports: list[int] | None = None,
                  protocol: str | None = None, user_consent: str = "",
                  site: str | None = None) -> dict:
    """永久把目標加入白名單（寫入設定檔，重啟後仍有效）。

    Args:
        target: 單一 IP、CIDR 網段（如 192.168.1.0/24）或 hostname。
        note: 備註，說明這是什麼設備。
        ports: 允許的埠號（預設 [502]；給空值表示不限）。
        protocol: tcp / rtu，留空表示不限協議。
        user_consent: 必填。使用者授權這次永久加入的原話，會寫入稽核紀錄。
            使用者沒有明確同意的話，不要呼叫這個工具。
        site: 加到哪個案場；留空 = 目前作用中的案場。
    """
    if not user_consent.strip():
        return failure(
            "consent_required",
            "永久加入白名單需要使用者明確同意，請先取得同意再帶 user_consent。",
            hint="若只是這一次要查，改用 authorize_temporary（一次性、會過期）風險更低。")
    raw = (target or "").strip()
    if not raw:
        return failure("invalid_argument", "target 不可為空。")
    if "/" in raw:
        import ipaddress
        try:
            ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            return failure("invalid_argument", f"CIDR 格式不合法：{raw}（{exc}）")
    if protocol and protocol.lower() not in ("tcp", "rtu"):
        return failure("invalid_argument", "protocol 只能是 tcp 或 rtu。")
    try:
        wanted = _site_arg(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])

    entry, created = _store.add_whitelist(
        raw, ports=[int(p) for p in ports] if ports else None,
        protocol=protocol.lower() if protocol else None, note=note,
        added_by=f"whitelist_add (consent: {user_consent.strip()[:200]})",
        site=wanted)
    _store.audit({"event": "whitelist_add", "target": entry.target,
                  "ports": entry.ports, "protocol": entry.protocol,
                  "created": created, "user_consent": user_consent.strip()})
    scope = wanted or _store.active_site()
    return result(
        True,
        f"已{'加入' if created else '更新'}白名單：{entry.target}"
        f"{'（埠 ' + ','.join(str(p) for p in entry.ports) + '）' if entry.ports else '（不限埠）'}"
        f"{' [案場：' + scope + ']' if scope else ' [根目錄設定]'}。",
        whitelist_entry=entry.to_json(), created=created, site=scope or None)


@mcp.tool()
def whitelist_remove(target: str, user_consent: str = "",
                     site: str | None = None) -> dict:
    """從白名單永久移除一個目標。

    Args:
        target: 要移除的項目，需與白名單中的寫法完全一致。
        user_consent: 使用者同意移除的原話，會寫入稽核紀錄。
        site: 從哪個案場移除；留空 = 目前作用中的案場。
    """
    try:
        wanted = _site_arg(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    removed = _store.remove_whitelist(target, site=wanted)
    _store.audit({"event": "whitelist_remove", "target": target,
                  "removed": removed, "user_consent": user_consent.strip()})
    if not removed:
        return failure("not_found", f"白名單裡沒有 {target} 這一筆。",
                       hint="用 whitelist_list 確認目前的寫法。",
                       whitelist=[e.target for e in _store.whitelist(wanted)])
    scope = wanted or _store.active_site()
    return result(True, f"已從白名單移除 {target}"
                        f"{'（案場：' + scope + '）' if scope else '（根目錄設定）'}。",
                  target=target, site=scope or None)


@mcp.tool()
def authorize_temporary(request_id: str, ttl_seconds: int = 600) -> dict:
    """對一次「需要授權」的請求給出一次性授權（存在記憶體，會自動過期）。

    只在使用者明確同意連線後才呼叫。授權只對該 host:port 有效，server 重啟即失效。

    Args:
        request_id: 從工具回傳的 needs_authorization.request_id 取得。
        ttl_seconds: 授權有效秒數（預設 600，最長 24 小時）。
    """
    try:
        info = _gate.authorize_temporary(request_id, ttl_seconds)
    except KeyError:
        return failure(
            "unknown_request",
            f"找不到授權請求 {request_id}（可能已使用或已過期）。",
            hint="重新執行原本的探測工具，會產生新的 request_id。",
            pending=[auth["request_id"] for auth in _gate.list_temp_auths()])
    return result(
        True,
        f"已授權 {info['host']}:{info['port']} 一次性存取 {info['expires_in_seconds']} 秒。",
        host=info["host"], port=info["port"], expires_in_seconds=info["expires_in_seconds"],
        request_id=request_id)


@mcp.tool()
def device_list(site: str | None = None) -> dict:
    """列出設備別名表（別名 → host/port/unit/framer）。

    要查某台已知設備時，先用這裡的別名當 target，比記 IP 可靠。

    Args:
        site: 看哪個案場；留空 = 目前作用中的案場。
    """
    try:
        wanted = _site_arg(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    devices = _store.devices(wanted)
    wl_path, dv_path = _store.paths_for(wanted)
    scope = wanted or _store.active_site()
    if not devices:
        return result(False, "設備表是空的。",
                      error={"kind": "no_devices",
                             "hint": f"可以編輯 {dv_path} 加入設備別名，或用 device_add。"},
                      site=scope or None, devices_file=str(dv_path),
                      whitelist_file=str(wl_path))
    lines = "、".join(f"{d.alias}({d.host}:{d.port}/unit{d.unit})" for d in devices[:8])
    return result(True, f"共 {len(devices)} 台設備：{lines}"
                        + (" …" if len(devices) > 8 else "")
                        + (f"。案場：{scope}" if scope else "。根目錄設定"),
                  devices=[d.to_json() for d in devices],
                  site=scope or None,
                  devices_file=str(dv_path), whitelist_file=str(wl_path))


@mcp.tool()
def device_add(alias: str, host: str, port: int = 502, unit: int = 1,
               framer: str = "tcp", note: str = "", site: str | None = None) -> dict:
    """把一台設備存成別名（不用每次輸入 IP / 埠 / 站號）。

    Args:
        alias: 別名，例如「一號機」「冰水主機」。同名已存在就更新。
        host: IP 或 hostname。
        port: 埠號，預設 502。
        unit: Modbus 站號（unit id），預設 1。
        framer: "tcp"（預設）或 "rtu"（RTU over TCP）。
        note: 備註。
        site: 加到哪個案場；留空 = 目前作用中的案場。
    """
    if not (alias or "").strip():
        return failure("invalid_argument", "alias 不可為空。")
    if not (host or "").strip():
        return failure("invalid_argument", "host 不可為空。")
    framer = (framer or "tcp").lower()
    if framer not in ("tcp", "rtu"):
        return failure("invalid_argument", "framer 只能是 tcp 或 rtu。")
    if not (0 < int(port) < 65536):
        return failure("invalid_argument", f"port 不合法：{port}")
    if not (0 <= int(unit) <= 247):
        return failure("invalid_argument", f"unit 不合法：{unit}（常用 1）")
    try:
        wanted = _site_arg(site)
        device, created = _store.upsert_device(
            Device(alias=alias.strip(), host=host.strip(), port=int(port),
                   unit=int(unit), framer=framer, note=note.strip()),
            site=wanted)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    _store.audit({"event": "device_add", "alias": device.alias, "host": device.host,
                  "port": device.port, "unit": device.unit, "framer": device.framer,
                  "created": created})
    scope = wanted or _store.active_site()
    return result(True,
                  f"已{'新增' if created else '更新'}設備別名「{device.alias}」→ "
                  f"{device.host}:{device.port} unit={device.unit} ({device.framer})"
                  f"{' [案場：' + scope + ']' if scope else ' [根目錄設定]'}。",
                  device=device.to_json(), created=created, site=scope or None)


@mcp.tool()
def device_remove(alias: str, user_consent: str = "",
                  site: str | None = None) -> dict:
    """移除設備別名（只掉名字，不會影響設備本身）。

    Args:
        alias: 要移除的別名。
        user_consent: 使用者同意移除的原話，會寫入稽核紀錄。
        site: 從哪個案場移除；留空 = 目前作用中的案場。
    """
    try:
        wanted = _site_arg(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    removed = _store.remove_device(alias, site=wanted)
    _store.audit({"event": "device_remove", "alias": alias, "removed": removed,
                  "user_consent": user_consent.strip()})
    if not removed:
        return failure("not_found", f"設備表裡沒有「{alias}」這個別名。",
                       hint="用 device_list 確認現有別名。",
                       devices=[d.alias for d in _store.devices(wanted)])
    scope = wanted or _store.active_site()
    return result(True, f"已移除設備別名「{alias}」"
                        f"{'（案場：' + scope + '）' if scope else '（根目錄設定）'}。",
                  alias=alias, site=scope or None)


# --------------------------------------------------------------------------
# Connectivity
# --------------------------------------------------------------------------
@mcp.tool()
async def tcp_check(target: str, port: int | None = None,
                    timeout_ms: int | None = None) -> dict:
    """只做 TCP 握手，不送任何 Modbus 訊框（判斷「網路/埠」這一層）。

    Args:
        target: IP、hostname，或 devices.json 的別名。
        port: 埠號，別名可省略（預設 502）。
        timeout_ms: 連線逾時毫秒，預設 3000。
    """
    vetted, refusal = _resolve(target, port, None, None, "tcp_check")
    if refusal:
        return refusal
    outcome = await _tcp_check(vetted.host, vetted.port, _seconds(timeout_ms))
    outcome["target"] = vetted.label
    return outcome


# --------------------------------------------------------------------------
# Modbus
# --------------------------------------------------------------------------
@mcp.tool()
async def modbus_probe(target: str, unit: int | None = None, port: int | None = None,
                       framer: str | None = None, probe_address: int = 0,
                       timeout_ms: int | None = None) -> dict:
    """發一個最小合法讀取請求，判斷「TCP 通了但 Modbus 有沒有回應」。

    會先試 FC03（holding register），若設備回報不支援功能碼再試 FC04（input register）。
    設備回「例外碼」也算有回應（代表 unit id 與連線都對）。

    Args:
        target: IP、hostname 或別名。
        unit: Modbus unit/slave id（預設 1）。
        port: 埠號（預設 502）。
        framer: "tcp"（預設）或 "rtu"（Modbus RTU over TCP）。
        probe_address: 用來探測的位址，預設 0。
        timeout_ms: 逾時毫秒，預設 3000。
    """
    vetted, refusal = _resolve(target, port, unit, framer, "modbus_probe")
    if refusal:
        return refusal
    return await client.probe(vetted, probe_address=probe_address,
                              timeout=_seconds(timeout_ms))


@mcp.tool()
async def modbus_read(target: str, area: str, address: int, count: int = 1,
                      unit: int | None = None, port: int | None = None,
                      framer: str | None = None, data_type: str = "uint16",
                      word_order: str = "big", byte_order: str = "big",
                      timeout_ms: int | None = None) -> dict:
    """讀取 Modbus 資料（FC01/02/03/04）。

    Args:
        target: IP、hostname 或別名。
        area: coil / discrete_input / holding / input。
        address: 起始位址（0-based，依設備手冊）。
        count: 數量（bit 區最多 2000，register 區最多 125）。
        unit: unit/slave id（預設 1）。
        port: 埠號（預設 502）。
        framer: "tcp"（預設）或 "rtu"（RTU over TCP）。
        data_type: uint16 / int16 / uint32 / int32 / float32 / float64 / hex。
        word_order: 多 register 時的高低位順序 big（ABCD）或 little（CDAB）。
        byte_order: 單一 register 內位元組順序 big（AB）或 little（BA）。
        timeout_ms: 逾時毫秒，預設 3000。
    """
    vetted, refusal = _resolve(target, port, unit, framer, "modbus_read", data_type)
    if refusal:
        return refusal
    return await client.read(vetted, area, address, count,
                             timeout=_seconds(timeout_ms), data_type=data_type,
                             word_order=word_order, byte_order=byte_order)


@mcp.tool()
async def modbus_write(target: str, area: str, address: int, values: list,
                       unit: int | None = None, port: int | None = None,
                       framer: str | None = None, data_type: str = "uint16",
                       word_order: str = "big", byte_order: str = "big",
                       dry_run: bool = True, confirm: bool = False,
                       user_consent: str = "",
                       timeout_ms: int | None = None) -> dict:
    """寫入 Modbus coil 或 holding register（FC05/06/15/16）。危險操作，請謹慎。

    三段式保護：預設 dry_run=true 只回預演內容、不建立連線；要真的寫入必須同時給
    dry_run=false、confirm=true、user_consent=<使用者同意的原話>。寫入後會自動讀回驗證。

    Args:
        target: IP、hostname 或別名。
        area: coil（可寫）或 holding（可寫）。discrete_input / input 唯讀。
        address: 起始位址。
        values: 要寫的值。coil 用 true/false；register 用整數或浮點（依 data_type）。
        unit: unit/slave id（預設 1）。
        port: 埠號（預設 502）。
        framer: "tcp"（預設）或 "rtu"。
        data_type: register 的資料型別（決定佔幾個 register）。
        word_order / byte_order: 多位元組資料的排列方式。
        dry_run: true（預設）= 只預演，不送出任何東西。
        confirm: 真正執行時必須為 true。
        user_consent: 使用者同意寫入的原話，會寫入稽核紀錄。
        timeout_ms: 逾時毫秒，預設 3000。
    """
    vetted, refusal = _resolve(target, port, unit, framer, "modbus_write", data_type)
    if refusal:
        return refusal

    key = (area or "").strip().lower()
    if key in ("coil", "coils"):
        fc_name = "FC05 (write single coil)" if len(values) == 1 else "FC15 (write multiple coils)"
    elif key in ("holding", "holding_register", "holding_registers"):
        fc_name = "FC06 (write single register)" if len(values) == 1 \
            else "FC16 (write multiple registers)"
    else:
        return failure("invalid_argument",
                       f"{area} 不可寫入 — 只有 coil 與 holding register 可寫。",
                       hint="input register / discrete input 是唯讀的。")

    preview = {
        "target": vetted.label,
        "function": fc_name,
        "area": key,
        "address": address,
        "values": values,
        "data_type": data_type,
        "would_send": "no bytes sent (dry run)",
    }

    if not _gate.writes_enabled():
        _store.audit({"event": "write_blocked", "reason": "write_disabled",
                      **{k: v for k, v in preview.items() if k != "would_send"}})
        return failure("write_disabled",
                       "寫入功能已被 MODBUS_WRITE_ENABLED 關閉，未執行任何寫入。",
                       hint="需要寫入請把環境變數 MODBUS_WRITE_ENABLED 設為 true。",
                       preview=preview)

    if dry_run:
        return result(True,
                      f"DRY-RUN：未送出任何資料。若執行，將對 {vetted.label} 用 {fc_name} "
                      f"把位址 {address} 設為 {values}。",
                      dry_run=True, preview=preview,
                      next_step="要向使用者確認；同意後帶 dry_run=false、confirm=true、user_consent=… 再呼叫一次。")

    if not confirm:
        return failure("confirmation_required",
                       "已指定 dry_run=false，但沒有 confirm=true，未執行寫入。",
                       hint="請先向使用者確認，再帶 confirm=true 與 user_consent 重試。",
                       preview=preview)
    if not user_consent.strip():
        return failure("consent_required",
                       "真正寫入前需要 user_consent（使用者同意的原話），未執行寫入。",
                       hint="先問使用者「我要把 X 改成 Y，可以嗎？」，把他的答覆放進 user_consent。",
                       preview=preview)

    outcome = await client.write(vetted, area, address, values,
                                 timeout=_seconds(timeout_ms), data_type=data_type,
                                 word_order=word_order, byte_order=byte_order)
    record = {
        "event": "modbus_write",
        "target": vetted.label,
        "host": vetted.host,
        "port": vetted.port,
        "unit": vetted.unit,
        "area": key,
        "address": address,
        "values": values,
        "ok": outcome.get("ok"),
        "error": outcome.get("error"),
        "user_consent": user_consent.strip(),
    }
    if not outcome.get("ok"):
        _store.audit({**record, "event": "modbus_write_failed"})
        return outcome

    read_area = "coil" if key in ("coil", "coils") else "holding"
    read_count = outcome.get("written_count") or 1
    readback = await client.read(vetted, read_area, address, int(read_count),
                                 timeout=_seconds(timeout_ms), data_type=data_type,
                                 word_order=word_order, byte_order=byte_order)
    verified = bool(readback.get("ok"))
    outcome["readback"] = {
        "ok": verified,
        "values": readback.get("values") or readback.get("raw"),
        "error": readback.get("error"),
    }
    outcome["verified"] = verified
    outcome["summary"] = outcome["summary"].rstrip("。") + (
        "；讀回驗證一致。" if verified else "；但寫入後讀回失敗（設備可能拒絕或位址對不上）。")
    _store.audit({**record, "verified": verified,
                  "readback": outcome["readback"]})
    return outcome


@mcp.tool()
async def modbus_scan_units(target: str, unit_start: int = 1, unit_end: int = 247,
                           port: int | None = None, framer: str | None = None,
                           probe_address: int = 0, timeout_ms: int = 1000,
                           concurrency: int = 4) -> dict:
    """掃描 unit id 範圍，找出匯流排上實際存在的 slave（很慢，先用小範圍試）。

    Args:
        target: IP、hostname 或別名（這裡的 unit 會被逐一套用 1..247）。
        unit_start / unit_end: 掃描範圍，預設 1..247。
        port: 埠號（預設 502）。
        framer: "tcp"（預設）或 "rtu"。
        probe_address: 探測位址，預設 0。
        timeout_ms: 每個 unit 的逾時毫秒，預設 1000。
        concurrency: 併發數，預設 4（設備脆弱時不要調高）。
    """
    vetted, refusal = _resolve(target, port, None, framer, "modbus_scan_units")
    if refusal:
        return refusal
    return await client.scan_units(vetted, unit_start, unit_end,
                                   timeout=_seconds(timeout_ms),
                                   concurrency=concurrency,
                                   probe_address=probe_address)


@mcp.tool()
async def modbus_diagnose(target: str, attempts: int = 5, unit: int | None = None,
                          port: int | None = None, framer: str | None = None,
                          probe_address: int = 0, timeout_ms: int | None = None,
                          interval_ms: int = 200) -> dict:
    """對單一設備重複探測 N 次，算出成功率 / 延遲 / 抖動，判斷是否間歇性故障。

    Args:
        target: IP、hostname 或別名。
        attempts: 重試次數（1..20，預設 5）。
        unit: unit/slave id（預設 1）。
        port / framer: 同其他工具。
        probe_address: 探測位址，預設 0。
        timeout_ms: 每次逾時毫秒，預設 3000。
        interval_ms: 每次間隔毫秒，預設 200。
    """
    vetted, refusal = _resolve(target, port, unit, framer, "modbus_diagnose")
    if refusal:
        return refusal
    return await diagnostics.diagnose(vetted, attempts=attempts,
                                      timeout=_seconds(timeout_ms),
                                      probe_address=probe_address,
                                      interval_ms=interval_ms)


@mcp.tool()
async def check_all(scope: str = "whitelist", level: str = "probe",
                    concurrency: int = 8, timeout_ms: int = 1500,
                    max_targets: int = 32,
                    protocol_filter: str | None = None,
                    site: str | None = None) -> dict:
    """一次檢查多個目標（白名單 / 設備表）的健康狀態，回傳總表與彙總。

    Args:
        scope: whitelist（預設）/ devices / both。devices 只看設備表的別名設備。
        level: tcp（只測連通）/ probe（預設，TCP+Modbus 應答）/ read（再讀一筆值驗證）。
        concurrency: 併發數上限，預設 8（環境變數 MODBUS_MAX_CONCURRENCY 可再壓低）。
        timeout_ms: 單一目標逾時毫秒，預設 1500。
        max_targets: 最多檢查幾個目標，預設 32，硬上限 256。
        protocol_filter: 只檢查 tcp 或 rtu 的目標。
        site: 檢查哪個案場；留空 = 目前作用中的案場。
    """
    try:
        wanted = _site_arg(site)
    except ValueError as exc:
        return failure("not_found", str(exc), hint=_site_hint(),
                       sites=[s.id for s in _store.sites()])
    outcome = await diagnostics.check_all(
        _store, scope=scope, level=level, concurrency=concurrency,
        timeout=_seconds(timeout_ms), max_targets=max_targets,
        protocol_filter=protocol_filter, site=wanted)
    scope_label = wanted or _store.active_site()
    outcome["site"] = scope_label or None
    if scope_label:
        outcome["summary"] = f"[案場：{scope_label}] " + str(outcome.get("summary") or "")
    return outcome


@mcp.tool()
def audit_tail(limit: int = 20, site: str | None = None) -> dict:
    """看最近的寫入 / 授權稽核紀錄（logs/audit.jsonl 最後 N 筆）。

    Args:
        limit: 筆數，預設 20。
        site: 只看某個案場的紀錄；留空 = 全部案場。
    """
    records = _store.audit_tail(limit * 5 if site else limit)
    if site:
        wanted = site.strip().lower()
        records = [r for r in records
                   if str(r.get("site") or "").lower() == wanted][-max(1, limit):]
    if not records:
        return result(True, "目前沒有稽核紀錄。", records=[],
                      audit_file=str(_store.audit_path))
    writes = [r for r in records if str(r.get("event", "")).startswith("modbus_write")]
    return result(True, f"最近 {len(records)} 筆紀錄，其中 {len(writes)} 筆與寫入有關。",
                  records=records, audit_file=str(_store.audit_path))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
