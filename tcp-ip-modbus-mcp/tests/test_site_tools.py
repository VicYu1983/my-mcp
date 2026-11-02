"""Tool-level tests for site (案場) management."""

from __future__ import annotations

import pytest
from fake_device import FakeModbusDevice

from modbus_mcp import server

CONSENT = "好，這個案場可以建立"


@pytest.fixture
def sites():
    """Track created sites and restore root scope afterwards."""
    created: list[str] = []

    def make(name: str, **kwargs):
        out = server.site_create(name, **kwargs)
        assert out["ok"] is True, out
        created.append(out["site"]["id"])
        return out

    yield make
    server.site_use("default")
    for name in created:
        server.site_delete(name, user_consent="測試結束清理")


def test_site_list_when_only_root_scope(sites):
    out = server.site_list()
    assert out["ok"] is True
    assert out.get("active_site") is None


def test_site_create_and_switch_scopes_whitelist(sites):
    sites("案場甲")
    assert server.whitelist_list()["whitelist"] == []
    out = server.whitelist_add("10.20.30.0/24", ports=[502], user_consent=CONSENT)
    assert out["ok"] is True and out["site"] == "案場甲"
    assert [e["target"] for e in server.whitelist_list()["whitelist"]] == ["10.20.30.0/24"]

    server.site_use("default")
    assert "10.20.30.0/24" not in [e["target"] for e in server.whitelist_list()["whitelist"]]

    server.site_use("案場甲")
    assert [e["target"] for e in server.whitelist_list()["whitelist"]] == ["10.20.30.0/24"]


def test_whitelist_add_can_target_other_site(sites):
    sites("甲地", activate=True)
    sites("乙地", activate=True)
    server.whitelist_add("10.1.0.1", ports=[502], user_consent=CONSENT, site="甲地")
    assert [e["target"] for e in server.whitelist_list(site="甲地")["whitelist"]] == ["10.1.0.1"]
    assert server.whitelist_list(site="乙地")["whitelist"] == []


def test_device_add_and_list_per_site(sites):
    sites("設備案場")
    out = server.device_add("冰水主機", "10.2.2.2", port=5020, unit=3, framer="rtu")
    assert out["ok"] is True and out["created"] is True
    listed = server.device_list()
    assert listed["devices"][0]["alias"] == "冰水主機"
    assert listed["devices"][0]["unit"] == 3

    removed = server.device_remove("冰水主機")
    assert removed["ok"] is True
    assert server.device_remove("冰水主機")["ok"] is False


def test_site_delete_requires_consent(sites):
    sites("待刪案場", activate=False)
    out = server.site_delete("待刪案場")
    assert out["ok"] is False and out["error"]["kind"] == "consent_required"
    assert "待刪案場" in server.site_list()["sites"][0]["id"]
    ok = server.site_delete("待刪案場", user_consent="可以刪")
    assert ok["ok"] is True


def test_site_delete_refuses_active_site(sites):
    sites("使用中", activate=True)
    out = server.site_delete("使用中", user_consent="可以刪")
    assert out["ok"] is False and out["error"]["kind"] == "refused"


def test_site_show_does_not_switch(sites):
    sites("甲地", activate=True)
    sites("乙地", activate=True)
    out = server.site_show("甲地")
    assert out["ok"] is True and out["site"] == "甲地"
    assert server.site_list()["active_site"] == "乙地"


async def test_check_all_is_site_scoped(sites):
    dev = FakeModbusDevice(registers=[7], coils=[True])
    await dev.start()
    try:
        sites("檢查案場", activate=True)
        server.whitelist_add("127.0.0.1", ports=[dev.port], user_consent=CONSENT)
        out = await server.check_all(scope="whitelist", level="read", timeout_ms=800)
        assert out["ok"] is True
        assert out["site"] == "檢查案場"
        assert out["counts"]["total"] == 1
        assert out["summary"].startswith("[案場：檢查案場]")
    finally:
        await dev.stop()


def test_audit_records_carry_site_tag(sites):
    sites("稽核案場", activate=True)
    server.whitelist_add("10.44.44.44", ports=[502], user_consent=CONSENT)
    tail = server.audit_tail(10, site="稽核案場")
    assert any(rec.get("site") == "稽核案場" for rec in tail["records"])


def test_unknown_site_argument_is_reported(sites):
    out = server.whitelist_list(site="沒有這個案場")
    assert out["ok"] is False and out["error"]["kind"] == "not_found"
