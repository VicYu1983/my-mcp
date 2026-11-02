"""Site (案場) management: isolation, switching, copy, delete, safety."""

from __future__ import annotations

import json

import pytest

from modbus_mcp import diagnostics
from modbus_mcp.config import ConfigStore, Device, normalize_site_id
from modbus_mcp.safety import Gate, resolve_target


@pytest.fixture
def store(tmp_path):
    return ConfigStore(tmp_path / "config", tmp_path / "logs")


def _entries(store: ConfigStore, site=None) -> list[str]:
    return [entry.target for entry in store.whitelist(site)]


# -- defaults / backwards compatibility ------------------------------------
def test_no_site_means_root_scope(store):
    assert store.active_site() == ""
    assert store.sites() == []
    assert store.whitelist_path == store.config_dir / "whitelist.json"
    assert store.devices_path == store.config_dir / "devices.json"
    assert _entries(store) == ["127.0.0.1"]


def test_root_scope_still_hand_editable(store):
    store.whitelist_path.write_text(
        json.dumps({"entries": [{"target": "10.9.9.9", "ports": [502]}]}),
        encoding="utf-8")
    assert _entries(store) == ["10.9.9.9"]


# -- create / switch -------------------------------------------------------
def test_create_site_starts_empty_and_scopes_files(store):
    info, created = store.create_site("案場A", note="台中")
    assert created is True
    assert info.whitelist_count == 0
    assert store.active_site() == "案場A"
    assert store.whitelist_path == store.sites_dir / "案場A" / "whitelist.json"
    assert _entries(store) == []
    # root untouched
    assert _entries(store, "default") == ["127.0.0.1"]


def test_whitelist_is_per_site(store):
    store.create_site("案場A", activate=False)
    store.create_site("案場B", activate=False)
    store.add_whitelist("10.1.1.1", ports=[502], site="案場A")
    store.add_whitelist("10.2.2.2", ports=[502], site="案場B")

    assert _entries(store, "案場A") == ["10.1.1.1"]
    assert _entries(store, "案場B") == ["10.2.2.2"]
    assert _entries(store, "default") == ["127.0.0.1"]


def test_switch_changes_effective_whitelist(store):
    store.create_site("案場A", activate=True)
    store.add_whitelist("10.1.1.1", ports=[502])

    store.set_active_site("default")
    assert store.active_site() == ""
    assert _entries(store) == ["127.0.0.1"]

    store.set_active_site("案場A")
    assert store.active_site() == "案場A"
    assert _entries(store) == ["10.1.1.1"]


def test_site_names_are_case_insensitive(store):
    store.create_site("Plant-A", activate=False)
    assert store.resolve_site("plant-a") == "Plant-A"
    store.set_active_site("PLANT-A")
    assert store.active_site() == "Plant-A"


def test_unknown_site_is_rejected(store):
    with pytest.raises(ValueError):
        store.set_active_site("不存在")
    with pytest.raises(ValueError):
        store.whitelist("不存在")


def test_env_override_wins(store, monkeypatch):
    store.create_site("案場A", activate=False)
    store.create_site("案場B", activate=True)
    store.add_whitelist("10.3.3.3", ports=[502], site="案場A")
    monkeypatch.setenv("MODBUS_MCP_SITE", "案場A")
    assert store.active_site() == "案場A"
    assert _entries(store) == ["10.3.3.3"]


def test_missing_active_folder_falls_back_to_root(store):
    store.create_site("案場A", activate=True)
    import shutil
    shutil.rmtree(store.sites_dir / "案場A")
    assert store.active_site() == ""
    assert _entries(store) == ["127.0.0.1"]


# -- copy ------------------------------------------------------------------
def test_copy_from_root(store):
    store.add_whitelist("192.168.5.5", ports=[1502])
    store.upsert_device(Device(alias="一號機", host="192.168.5.5", port=1502, unit=3))
    info, _ = store.create_site("案場C", copy_from="default")
    assert info.whitelist_count == 2
    assert info.device_count == 2
    assert _entries(store) == _entries(store, "default")
    assert store.find_device("一號機").port == 1502


def test_copy_from_other_site(store):
    store.create_site("範本", activate=True)
    store.add_whitelist("172.16.0.0/24", ports=[502])
    store.create_site("案場D", copy_from="範本", activate=True)
    store.add_whitelist("172.16.0.9", ports=[502])
    assert _entries(store, "範本") == ["172.16.0.0/24"]
    assert _entries(store) == ["172.16.0.0/24", "172.16.0.9"]


# -- devices ---------------------------------------------------------------
def test_device_upsert_and_remove_per_site(store):
    store.create_site("案場A", activate=True)
    device, created = store.upsert_device(
        Device(alias="冰水主機", host="10.5.5.5", port=5020, unit=7, framer="rtu"))
    assert created is True
    assert store.find_device("冰水主機").unit == 7

    updated, created = store.upsert_device(
        Device(alias="冰水主機", host="10.5.5.5", port=5020, unit=9))
    assert created is False
    assert store.find_device("冰水主機").unit == 9

    assert store.remove_device("冰水主機") is True
    assert store.remove_device("冰水主機") is False
    assert store.devices() == []
    # root table untouched
    assert [d.alias for d in store.devices("default")] == ["本機測試"]


# -- delete ----------------------------------------------------------------
def test_delete_moves_to_trash_and_requires_consent_path(store):
    store.create_site("案場E", activate=False)
    moved = store.delete_site("案場E")
    assert moved is not None and moved.parent == store.trash_dir
    assert "案場E" not in store.site_ids()
    with pytest.raises(ValueError):
        store.delete_site("案場E")


def test_cannot_delete_active_site(store):
    store.create_site("案場F", activate=True)
    with pytest.raises(ValueError):
        store.delete_site("案場F")


def test_cannot_delete_root_scope(store):
    with pytest.raises(ValueError):
        store.delete_site("default")


# -- validation ------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "   ", "a/b", "a\\b", "..", ".", "_trash",
                                 "x:y", "a|b", "end.", "a" * 65])
def test_bad_site_names_rejected(bad):
    with pytest.raises(ValueError):
        normalize_site_id(bad)


@pytest.mark.parametrize("good", ["案場A", "台中廠-1", "client_A", "A1"])
def test_good_site_names_accepted(good):
    assert normalize_site_id(good) == good


def test_hand_made_site_folder_is_discovered(store):
    base = store.sites_dir / "手動案場"
    base.mkdir(parents=True)
    (base / "whitelist.json").write_text(
        json.dumps({"entries": [{"target": "10.7.7.7", "ports": [502]}]}),
        encoding="utf-8")
    ids = store.site_ids()
    assert "手動案場" in ids
    assert _entries(store, "手動案場") == ["10.7.7.7"]


# -- gate integration ------------------------------------------------------
def test_gate_never_leaks_targets_between_sites(store):
    gate = Gate(store, resolve_hostnames=False)
    store.create_site("案場A", activate=True)
    store.add_whitelist("10.1.1.1", ports=[502])

    allowed, refusal = resolve_target(gate, "10.1.1.1", port=502)
    assert allowed is not None and refusal is None

    store.set_active_site("default")
    blocked, refusal = resolve_target(gate, "10.1.1.1", port=502)
    assert blocked is None
    assert refusal["error"]["kind"] == "not_whitelisted"


def test_gate_blocks_target_that_belongs_to_another_site(store):
    gate = Gate(store, resolve_hostnames=False)
    store.create_site("隔離測試", activate=True)
    # 新案場的白名單是空的，所以 root 裡有的 127.0.0.1 不該被放行
    blocked, refusal = resolve_target(gate, "127.0.0.1", port=502)
    assert blocked is None
    assert refusal["error"]["kind"] == "not_whitelisted"
