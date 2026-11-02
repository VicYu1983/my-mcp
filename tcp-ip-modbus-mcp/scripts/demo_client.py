"""透過真正的 MCP stdio 協定跑一輪完整驗證（不需要實機 PLC）。

前置：先啟動模擬器
    uv run python scripts/modbus_sim.py --port 5020

然後：
    uv run python scripts/demo_client.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
PASS: list[str] = []
FAIL: list[str] = []


def show(title: str, data: dict, *, fields: tuple[str, ...] = (),
         expect_fail: bool = False) -> None:
    if expect_fail:
        status = "DENY" if not data.get("ok") else "FAIL"
    else:
        status = "OK  " if data.get("ok") else "FAIL"
    print(f"\n[{status}] {title}")
    print(f"       {data.get('summary')}")
    for field in fields:
        value = data.get(field)
        if value in (None, [], {}):
            continue
        print(f"       {field}: {json.dumps(value, ensure_ascii=False)}")
    err = data.get("error")
    if err:
        hint = f" → {err.get('hint')}" if err.get("hint") else ""
        print(f"       error: {err.get('kind')}{hint}")


def check(label: str, condition: bool) -> None:
    (PASS if condition else FAIL).append(label)
    print(f"  {'✓' if condition else '✗'} {label}")


async def run(port: int, alias: str) -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "modbus_mcp"],
                                   cwd=str(ROOT))
    async with stdio_client(params) as streams, ClientSession(*streams) as session:
        init = await session.initialize()
        tools = await session.list_tools()
        print(f"已連上 MCP server：{init.server_info.name}，共 {len(tools.tools)} 個工具")
        check("握手成功並列出 20 個工具", len(tools.tools) == 20)

        async def call(name: str, **args) -> dict:
            result = await session.call_tool(name, args)
            return json.loads(result.content[0].text)

        print("\n── 1) 白名單 / 設備表 ──")
        show("whitelist_list", await call("whitelist_list"), fields=("whitelist",))
        devices = await call("device_list")
        show("device_list", devices, fields=("devices",))
        check("設備表可列出別名", devices.get("ok") is True)

        print("\n── 2) 分層診斷：TCP → Modbus ──")
        tcp = await call("tcp_check", target=alias, timeout_ms=1000)
        show("tcp_check", tcp, fields=("latency_ms", "peer", "local_address"))
        check("TCP 握手成功", tcp.get("ok") is True)

        probe = await call("modbus_probe", target=alias, timeout_ms=1000)
        show("modbus_probe", probe,
             fields=("latency_ms", "raw", "frame_tx", "frame_rx"))
        check("Modbus 層有應答", probe.get("ok") is True)

        print("\n── 3) 讀取（FC03 / FC01） ──")
        holding = await call("modbus_read", target=alias, area="holding",
                             address=0, count=8, timeout_ms=1000)
        show("modbus_read holding 0..7", holding, fields=("values",))
        # 模擬器是 stateful 的（重跑時位址 2 已被上次寫成 999），所以只驗長度
        check("讀到 8 個 register", len(holding.get("values") or []) == 8)

        coils = await call("modbus_read", target=alias, area="coil", address=0,
                           count=4, timeout_ms=1000)
        show("modbus_read coil 0..3", coils, fields=("values",))
        check("讀到 4 個 coil", coils.get("values") == [True, False, True, False])

        print("\n── 4) 寫入保護：dry-run 不送封包 ──")
        dry = await call("modbus_write", target=alias, area="holding", address=2,
                         values=[999], timeout_ms=1000)
        show("modbus_write（預設 dry-run）", dry, fields=("dry_run", "preview"))
        check("dry-run 只回預演內容", dry.get("dry_run") is True)

        blocked = await call("modbus_write", target=alias, area="holding", address=2,
                             values=[999], dry_run=False, timeout_ms=1000)
        show("modbus_write（缺 confirm）", blocked, expect_fail=True)
        check("缺 confirm 被擋下",
              blocked.get("error", {}).get("kind") == "confirmation_required")

        print("\n── 5) 真正寫入 + 讀回驗證 ──")
        real = await call("modbus_write", target=alias, area="holding", address=2,
                          values=[999], dry_run=False, confirm=True,
                          user_consent="好，測試用模擬器可以寫入", timeout_ms=1000)
        show("modbus_write（已授權）", real,
             fields=("function", "verified", "readback", "frame_tx", "frame_rx"))
        check("寫入成功且讀回一致",
              real.get("ok") is True and real.get("verified") is True)

        after = await call("modbus_read", target=alias, area="holding", address=2,
                           count=1, timeout_ms=1000)
        show("modbus_read 位址 2（寫入後）", after, fields=("values",))
        check("值已變成 999", after.get("values") == [999])

        print("\n── 6) 錯誤情境：不在白名單 ──")
        denied = await call("tcp_check", target="192.0.2.99", port=502, timeout_ms=300)
        show("tcp_check（非白名單）", denied, fields=("needs_authorization",),
             expect_fail=True)
        check("被擋下並產生授權請求",
              denied.get("error", {}).get("kind") == "not_whitelisted"
              and bool(denied.get("needs_authorization")))

        print("\n── 7) 批次檢查 check_all ──")
        batch = await call("check_all", scope="both", level="probe", timeout_ms=800)
        show("check_all scope=both", batch,
             fields=("counts", "elapsed_seconds"), expect_fail=True)
        for item in batch.get("results", []):
            name = item.get("alias") or f"{item['host']}:{item['port']}"
            latency = f"{item['latency_ms']}ms" if item.get("latency_ms") else ""
            err = f" {item['error']['kind']}" if item.get("error") else ""
            print(f"       - {'OK ' if item['ok'] else 'ERR'} {name}"
                  f" stage={item.get('stage')} {latency}{err}")
        check("批次回傳彙總與明細",
              batch.get("counts", {}).get("total", 0) >= 2
              and len(batch.get("results", [])) >= 2)

        print("\n── 8) 稽核紀錄 ──")
        audit = await call("audit_tail", limit=3)
        show("audit_tail", audit, fields=("records",))
        check("寫入已留下稽核紀錄",
              any(r.get("event") == "modbus_write" for r in audit.get("records", [])))

        print("\n── 9) 案場（多客戶 / 多廠區）──")
        created = await call("site_create", site="demo-案場", copy_from="default",
                            note="demo_client 自動建立的示範案場")
        show("site_create", created, fields=("site", "active_site"))
        check("案場建立並切換過去",
              created.get("ok") is True and created.get("active_site") == "demo-案場")

        scoped = await call("whitelist_add", target="192.0.2.99", ports=[502],
                            note="demo", user_consent="好，這個案場可以加入測試 IP")
        show("whitelist_add（在 demo 案場）", scoped, fields=("whitelist_entry", "site"))
        check("新增只影響目前案場", scoped.get("site") == "demo-案場")

        root_wl = await call("whitelist_list", site="default")
        root_targets = [e.get("target") for e in root_wl.get("whitelist", [])]
        check("根目錄白名單不受影響", "192.0.2.99" not in root_targets)

        back = await call("site_use", site="default")
        show("site_use default", back, fields=("active_site", "whitelist_count"))
        check("可切回根目錄設定", back.get("ok") is True and not back.get("active_site"))

        gone = await call("site_delete", site="demo-案場",
                         user_consent="好，示範案場可以刪除")
        show("site_delete", gone, fields=("moved_to",))
        check("案場可移除（搬到 sites/_trash，可救回）", gone.get("ok") is True)

    print("\n" + "=" * 60)
    print(f"驗證結果：{len(PASS)} 項通過，{len(FAIL)} 項失敗")
    for item in FAIL:
        print(f"  失敗：{item}")
    if FAIL:
        raise SystemExit(1)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default="本機模擬器")
    parser.add_argument("--port", type=int, default=5020)
    args = parser.parse_args()
    if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    await run(args.port, args.alias)


if __name__ == "__main__":
    asyncio.run(main())
