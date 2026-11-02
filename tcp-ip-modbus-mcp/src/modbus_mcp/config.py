"""Config store: sites, whitelist, device aliases, audit log, with hot reload.

Layout::

    config/
      whitelist.json          root scope (no site selected)
      devices.json
      sites.json              site index + which site is active
      sites/<site>/whitelist.json
      sites/<site>/devices.json
      sites/_trash/<site>-<ts>/   deleted sites (recoverable)

Files are re-read on every access, so you can edit them by hand while the MCP
server is running. Selecting a site swaps the whitelist/device table wholesale,
which keeps one customer's targets from leaking into another's.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_PACKAGE_DIR = Path(__file__).resolve().parent          # .../src/modbus_mcp
_PROJECT_DIR = _PACKAGE_DIR.parents[1]                  # project root


def _default_config_dir() -> Path:
    env = os.environ.get("MODBUS_MCP_CONFIG_DIR")
    return Path(env).expanduser().resolve() if env else _PROJECT_DIR / "config"


def _default_log_dir() -> Path:
    env = os.environ.get("MODBUS_MCP_LOG_DIR")
    return Path(env).expanduser().resolve() if env else _PROJECT_DIR / "logs"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


ROOT_SITE_ID = "default"
"""Site id that means "the root config/ folder" (no site selected)."""

_SITE_BAD_CHARS = set('\\/:*?"<>|')


def normalize_site_id(raw: str) -> str:
    """Validate a site name so it is always safe to use as a directory name."""
    site_id = (raw or "").strip()
    if not site_id:
        raise ValueError("案場名稱不可為空。")
    if len(site_id) > 64:
        raise ValueError("案場名稱過長（上限 64 字元）。")
    if any(ch in _SITE_BAD_CHARS for ch in site_id) or any(ord(ch) < 32 for ch in site_id):
        raise ValueError('案場名稱不可包含 \\ / : * ? " < > | 或控制字元。')
    if site_id in (".", "..") or site_id.startswith("_"):
        raise ValueError("案場名稱不可為 . / .. 或以 _ 開頭（保留字）。")
    if site_id.endswith("."):
        raise ValueError("案場名稱結尾不可為半形句點。")
    return site_id


@dataclass(frozen=True)
class SiteInfo:
    id: str
    name: str = ""
    note: str = ""
    created_at: str = ""
    active: bool = False
    whitelist_count: int = 0
    device_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "note": self.note,
            "created_at": self.created_at,
            "active": self.active,
            "whitelist_count": self.whitelist_count,
            "device_count": self.device_count,
        }


@dataclass(frozen=True)
class WhitelistEntry:
    target: str
    ports: list[int] | None = None
    protocol: str | None = None
    note: str = ""
    added_at: str = ""
    added_by: str = "manual"

    def to_json(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "ports": self.ports,
            "protocol": self.protocol,
            "note": self.note,
            "added_at": self.added_at or now_iso(),
            "added_by": self.added_by,
        }


@dataclass(frozen=True)
class Device:
    alias: str
    host: str
    port: int = 502
    unit: int = 1
    framer: str = "tcp"
    note: str = ""
    data_type: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "alias": self.alias,
            "host": self.host,
            "port": self.port,
            "unit": self.unit,
            "framer": self.framer,
            "note": self.note,
            "data_type": self.data_type,
        }


_SITE_WHITELIST_README = [
    "此案場的白名單：只有符合以下項目的目標才能被探測 / 讀寫。",
    "target 可為單一 IP、CIDR 網段或 hostname。",
    "ports 省略 = 不限埠號；protocol 省略 = 不分協議。",
]

_SITE_DEVICES_README = [
    "此案場的設備別名表：讓模型用「一號機」這種名稱指定目標。",
    "framer: tcp = Modbus TCP (MBAP)，rtu = Modbus RTU over TCP。",
]

_WHITELIST_TEMPLATE = {
    "_readme": [
        "白名單：只有符合以下項目的目標才能被探測 / 讀寫。",
        "target 可為單一 IP、CIDR 網段或 hostname。",
        "ports 省略 = 不限埠號；protocol 省略 = 不分協議。",
    ],
    "entries": [
        {
            "target": "127.0.0.1",
            "ports": [502],
            "protocol": None,
            "note": "本機佔位 / 自我測試用",
            "added_at": now_iso(),
            "added_by": "bootstrap",
        }
    ],
}

_DEVICES_TEMPLATE = {
    "_readme": [
        "設備別名表：讓模型用「一號機」這種名稱指定目標。",
        "framer: tcp = Modbus TCP (MBAP)，rtu = Modbus RTU over TCP。",
    ],
    "devices": [
        {
            "alias": "本機測試",
            "host": "127.0.0.1",
            "port": 502,
            "unit": 1,
            "framer": "tcp",
            "note": "佔位設備",
        }
    ],
}


_SITES_TEMPLATE = {
    "_readme": [
        "案場索引：每個案場有自己獨立的白名單與設備表（config/sites/<案場>/）。",
        "active = 目前作用中的案場；空字串代表用根目錄的 whitelist.json / devices.json。",
        "本檔由 site_create / site_use / site_delete 維護，也可手動編輯。",
    ],
    "active": "",
    "sites": [],
}


class ConfigStore:
    """Thread-safe config/audit accessor with mtime-based hot reload."""

    def __init__(self, config_dir: Path | str | None = None,
                 log_dir: Path | str | None = None) -> None:
        self.config_dir = Path(config_dir).expanduser().resolve() if config_dir \
            else _default_config_dir()
        self.log_dir = Path(log_dir).expanduser().resolve() if log_dir else _default_log_dir()
        self._root_whitelist_path = self.config_dir / "whitelist.json"
        self._root_devices_path = self.config_dir / "devices.json"
        self.sites_path = self.config_dir / "sites.json"
        self.sites_dir = self.config_dir / "sites"
        self.trash_dir = self.sites_dir / "_trash"
        self.audit_path = self.log_dir / "audit.jsonl"
        self._lock = threading.RLock()
        self._ensure_files()

    # -- files -------------------------------------------------------------
    def _ensure_files(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        if not self._root_whitelist_path.exists():
            self._write_json(self._root_whitelist_path, _WHITELIST_TEMPLATE)
        if not self._root_devices_path.exists():
            self._write_json(self._root_devices_path, _DEVICES_TEMPLATE)
        if not self.sites_path.exists():
            self._write_json(self.sites_path, _SITES_TEMPLATE)

    # -- sites -------------------------------------------------------------
    @staticmethod
    def _is_root_site(site: str | None) -> bool:
        return not (site or "").strip() or (site or "").strip().lower() == ROOT_SITE_ID

    @property
    def whitelist_path(self) -> Path:
        """Whitelist file of the active site (root file when none is active)."""
        return self.paths_for(None)[0]

    @property
    def devices_path(self) -> Path:
        """Device table of the active site (root file when none is active)."""
        return self.paths_for(None)[1]

    def paths_for(self, site: str | None = None) -> tuple[Path, Path]:
        """(whitelist_path, devices_path) for a site; "" / "default" = root scope."""
        resolved = self.active_site() if site is None else site
        if self._is_root_site(resolved):
            return self._root_whitelist_path, self._root_devices_path
        site_id = self.resolve_site(resolved)
        base = self.sites_dir / site_id
        return base / "whitelist.json", base / "devices.json"

    def active_site(self) -> str:
        """Id of the active site, or "" when the root config is in use.

        `MODBUS_MCP_SITE` overrides the on-disk selection for one process, which
        is handy for running a separate server instance per customer site.
        """
        env = (os.environ.get("MODBUS_MCP_SITE") or "").strip()
        with self._lock:
            active = env or str(self._read_json(self.sites_path, {"active": ""}).get("active") or "").strip()
            if self._is_root_site(active):
                return ""
            if not (self.sites_dir / active).is_dir():
                return ""          # folder removed by hand -> fall back to root scope
            return active

    def site_ids(self) -> list[str]:
        ids: list[str] = []
        seen: set[str] = set()
        with self._lock:
            data = self._read_json(self.sites_path, {"sites": []})
            for item in data.get("sites") or []:
                if not isinstance(item, dict):
                    continue
                site_id = str(item.get("id") or "").strip()
                if site_id and site_id.lower() not in seen:
                    seen.add(site_id.lower())
                    ids.append(site_id)
            if self.sites_dir.is_dir():
                for child in sorted(self.sites_dir.iterdir()):
                    name = child.name
                    if not child.is_dir() or name.startswith("_") or name.lower() in seen:
                        continue
                    seen.add(name.lower())
                    ids.append(name)
        return ids

    def resolve_site(self, site: str) -> str:
        """Canonical site id (case-insensitive), or ROOT_SITE_ID for root scope."""
        wanted = (site or "").strip()
        if self._is_root_site(wanted):
            return ROOT_SITE_ID
        known = self.site_ids()
        for site_id in known:
            if site_id.lower() == wanted.lower():
                return site_id
        raise ValueError(
            f"找不到案場 {wanted!r}（現有：{'、'.join(known) if known else '無'}）。")

    def sites(self) -> list[SiteInfo]:
        with self._lock:
            data = self._read_json(self.sites_path, {"sites": []})
            meta: dict[str, dict[str, Any]] = {}
            for item in data.get("sites") or []:
                if isinstance(item, dict) and str(item.get("id") or "").strip():
                    meta[str(item["id"]).strip().lower()] = item
            active = self.active_site().lower()
            out: list[SiteInfo] = []
            for site_id in self.site_ids():
                item = meta.get(site_id.lower()) or {}
                wl_path, dv_path = self.paths_for(site_id)
                out.append(SiteInfo(
                    id=site_id,
                    name=str(item.get("name") or site_id),
                    note=str(item.get("note") or ""),
                    created_at=str(item.get("created_at") or ""),
                    active=site_id.lower() == active,
                    whitelist_count=len(self._entries_of(wl_path)),
                    device_count=len(self._items_of(dv_path, "devices")),
                ))
            return out

    def _entries_of(self, path: Path) -> list[dict[str, Any]]:
        return [i for i in (self._read_json(path, {"entries": []}).get("entries") or [])
                if isinstance(i, dict)]

    def _items_of(self, path: Path, key: str) -> list[dict[str, Any]]:
        return [i for i in (self._read_json(path, {key: []}).get(key) or [])
                if isinstance(i, dict)]

    def create_site(self, site: str, *, name: str = "", note: str = "",
                    copy_from: str | None = None,
                    activate: bool = True) -> tuple[SiteInfo, bool]:
        """Create (or update) a site. Returns (info, created).

        copy_from=None      start with an empty whitelist / device table
        copy_from="default" copy the root (unsited) whitelist / device table
        copy_from="<site>"  deep-copy that site as a starting point
        """
        site_id = normalize_site_id(site)
        with self._lock:
            data = self._read_json(self.sites_path, {"sites": [], "active": ""})
            items = [i for i in (data.get("sites") or []) if isinstance(i, dict)]
            existing = next((i for i in items
                             if str(i.get("id") or "").strip().lower() == site_id.lower()),
                            None)
            created = existing is None
            canonical = str(existing["id"]) if existing is not None else site_id
            base = self.sites_dir / canonical
            base.mkdir(parents=True, exist_ok=True)
            wl_path, dv_path = self.paths_for(canonical)

            if copy_from is None:
                if created or not wl_path.exists():
                    self._write_json(wl_path, {"_readme": _SITE_WHITELIST_README, "entries": []})
                if created or not dv_path.exists():
                    self._write_json(dv_path, {"_readme": _SITE_DEVICES_README, "devices": []})
            else:
                src_wl, src_dv = self.paths_for(copy_from)
                if not src_wl.exists() and not self._is_root_site(copy_from):
                    raise ValueError(f"copy_from 的案場 {copy_from!r} 沒有白名單檔。")
                if created or not wl_path.exists():
                    self._write_json(wl_path, {"_readme": _SITE_WHITELIST_README,
                                               "entries": self._entries_of(src_wl)})
                if created or not dv_path.exists():
                    self._write_json(dv_path, {"_readme": _SITE_DEVICES_README,
                                               "devices": self._items_of(src_dv, "devices")})

            if created:
                stamp = now_iso()
                items.append({"id": canonical, "name": (name or site_id).strip(),
                              "note": note.strip(), "created_at": stamp})
            else:
                stamp = ""
                for item in items:
                    if str(item.get("id") or "").strip().lower() == canonical.lower():
                        item["name"] = (name or item.get("name") or canonical).strip()
                        if note:
                            item["note"] = note.strip()
                        stamp = str(item.get("created_at") or "")
                        break
            data["sites"] = items
            if activate:
                data["active"] = canonical
            self._write_json(self.sites_path, data)
            info = SiteInfo(id=canonical, name=(name or site_id), note=note,
                            created_at=stamp, active=bool(activate),
                            whitelist_count=len(self._entries_of(wl_path)),
                            device_count=len(self._items_of(dv_path, "devices")))
            return info, created

    def set_active_site(self, site: str) -> str:
        """Switch the active site. "" / "default" detaches to the root config."""
        wanted = (site or "").strip()
        with self._lock:
            if self._is_root_site(wanted):
                target = ""
            else:
                target = self.resolve_site(wanted)
                if not (self.sites_dir / target).is_dir():
                    raise ValueError(f"案場 {target!r} 的資料夾不存在：{self.sites_dir / target}")
            data = self._read_json(self.sites_path, {"sites": [], "active": ""})
            data["active"] = target
            self._write_json(self.sites_path, data)
            return target

    def delete_site(self, site: str) -> Path | None:
        """Move a site to sites/_trash/ (never hard-deletes). Returns new path."""
        with self._lock:
            wanted = (site or "").strip()
            if self._is_root_site(wanted):
                raise ValueError("不能刪除根範圍（未分案場），請指定案場名稱。")
            site_id = self.resolve_site(wanted)
            active = self.active_site()
            if active and active.lower() == site_id.lower():
                raise ValueError(f"案場 {site_id!r} 目前使用中，請先切換到別的案場再刪除。")
            base = self.sites_dir / site_id
            moved: Path | None = None
            if base.is_dir():
                self.trash_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                dest = self.trash_dir / f"{site_id}-{stamp}"
                shutil.move(str(base), str(dest))
                moved = dest
            data = self._read_json(self.sites_path, {"sites": [], "active": ""})
            items = [i for i in (data.get("sites") or []) if isinstance(i, dict)]
            data["sites"] = [i for i in items
                             if str(i.get("id") or "").strip().lower() != site_id.lower()]
            self._write_json(self.sites_path, data)
            return moved

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(path)

    def _read_json(self, path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} 格式錯誤（JSON 解析失敗）：{exc}") from exc
        if not isinstance(data, dict):
            raise TypeError(f"{path.name} 必須是 JSON 物件")
        return data

    # -- whitelist ---------------------------------------------------------
    def whitelist(self, site: str | None = None) -> list[WhitelistEntry]:
        """Always re-reads the file, so hand edits are picked up immediately."""
        wl_path, _ = self.paths_for(site)
        with self._lock:
            data = self._read_json(wl_path, {"entries": []})
            entries: list[WhitelistEntry] = []
            for item in data.get("entries") or []:
                if not isinstance(item, dict) or not item.get("target"):
                    continue
                ports = item.get("ports")
                entries.append(WhitelistEntry(
                    target=str(item["target"]).strip(),
                    ports=[int(p) for p in ports] if ports else None,
                    protocol=(item.get("protocol") or None),
                    note=str(item.get("note") or ""),
                    added_at=str(item.get("added_at") or ""),
                    added_by=str(item.get("added_by") or "manual"),
                ))
            return entries

    def add_whitelist(self, target: str, *, ports: list[int] | None = None,
                      protocol: str | None = None, note: str = "",
                      added_by: str = "whitelist_add",
                      site: str | None = None) -> tuple[WhitelistEntry, bool]:
        """Add (or update) an entry. Returns (entry, created)."""
        wl_path, _ = self.paths_for(site)
        with self._lock:
            target = target.strip()
            if not target:
                raise ValueError("target 不可為空")
            data = self._read_json(wl_path, {"entries": []})
            items = [i for i in (data.get("entries") or []) if isinstance(i, dict)]
            entry = WhitelistEntry(target=target, ports=ports, protocol=protocol,
                                   note=note, added_at=now_iso(), added_by=added_by)
            created = True
            for idx, item in enumerate(items):
                if str(item.get("target", "")).strip().lower() == target.lower():
                    items[idx] = entry.to_json()
                    created = False
                    break
            else:
                items.append(entry.to_json())
            data["entries"] = items
            self._write_json(wl_path, data)
            return entry, created

    def remove_whitelist(self, target: str, site: str | None = None) -> bool:
        wl_path, _ = self.paths_for(site)
        with self._lock:
            data = self._read_json(wl_path, {"entries": []})
            items = [i for i in (data.get("entries") or []) if isinstance(i, dict)]
            kept = [i for i in items
                    if str(i.get("target", "")).strip().lower() != target.strip().lower()]
            if len(kept) == len(items):
                return False
            data["entries"] = kept
            self._write_json(wl_path, data)
            return True

    # -- devices -----------------------------------------------------------
    def devices(self, site: str | None = None) -> list[Device]:
        _, dv_path = self.paths_for(site)
        with self._lock:
            data = self._read_json(dv_path, {"devices": []})
            devices: list[Device] = []
            for item in data.get("devices") or []:
                if not isinstance(item, dict) or not item.get("alias") or not item.get("host"):
                    continue
                framer = str(item.get("framer") or "tcp").lower()
                devices.append(Device(
                    alias=str(item["alias"]),
                    host=str(item["host"]),
                    port=int(item.get("port") or 502),
                    unit=int(item.get("unit") or 1),
                    framer="rtu" if framer in ("rtu", "rtu_over_tcp", "rtuovertcp") else "tcp",
                    note=str(item.get("note") or ""),
                    data_type=(item.get("data_type") or None),
                ))
            return devices

    def find_device(self, alias: str, site: str | None = None) -> Device | None:
        key = alias.strip().lower()
        for device in self.devices(site):
            if device.alias.lower() == key:
                return device
        return None

    def upsert_device(self, device: Device, *, site: str | None = None,
                      ) -> tuple[Device, bool]:
        """Add a device alias, or update it when the alias already exists."""
        alias = device.alias.strip()
        if not alias:
            raise ValueError("alias 不可為空")
        _, dv_path = self.paths_for(site)
        with self._lock:
            data = self._read_json(dv_path, {"devices": []})
            items = [i for i in (data.get("devices") or []) if isinstance(i, dict)]
            stored = Device(alias=alias, host=device.host.strip(), port=device.port,
                            unit=device.unit, framer=device.framer, note=device.note,
                            data_type=device.data_type)
            created = True
            for idx, item in enumerate(items):
                if str(item.get("alias", "")).strip().lower() == alias.lower():
                    items[idx] = stored.to_json()
                    created = False
                    break
            else:
                items.append(stored.to_json())
            data["devices"] = items
            data.setdefault("_readme", _SITE_DEVICES_README)
            self._write_json(dv_path, data)
            return stored, created

    def remove_device(self, alias: str, site: str | None = None) -> bool:
        _, dv_path = self.paths_for(site)
        with self._lock:
            data = self._read_json(dv_path, {"devices": []})
            items = [i for i in (data.get("devices") or []) if isinstance(i, dict)]
            kept = [i for i in items
                    if str(i.get("alias", "")).strip().lower() != alias.strip().lower()]
            if len(kept) == len(items):
                return False
            data["devices"] = kept
            self._write_json(dv_path, data)
            return True

    # -- audit -------------------------------------------------------------
    def audit(self, record: dict[str, Any]) -> None:
        site = record.pop("site", None) or self.active_site()
        head: dict[str, Any] = {"ts": now_iso()}
        if site:
            head["site"] = site
        head.update(record)
        line = json.dumps(head, ensure_ascii=False)
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()

    def audit_tail(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            if not self.audit_path.exists():
                return []
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        out: list[dict[str, Any]] = []
        for line in lines[-max(1, limit):]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


_store: ConfigStore | None = None
_store_lock = threading.Lock()


def get_store() -> ConfigStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ConfigStore()
        return _store
