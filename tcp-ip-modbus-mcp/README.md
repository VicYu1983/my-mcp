# tcp-ip-modbus MCP Server

讓大模型（Claude Desktop、Cherry Studio、任何支援 MCP 的 client）能**實際探測** TCP/IP 連線與
Modbus 設備狀態，而不是憑猜測回答。

> 「一號機連不上」→ 模型會先用 `tcp_check` 判斷網路層，再用 `modbus_probe` 判斷 Modbus 層，
> 最後才用 `modbus_read` 讀值，並把卡在哪一層、為什麼卡住講清楚。

## 特色

- **分層診斷**：TCP 不通 / TCP 通但 Modbus 無回應 / 回應例外碼，三者明確區分
- **案場隔離**：每個客戶 / 廠區一組自己的白名單與設備表，切案場就整組換掉，不會誤觸別人家設備
- **兩種框架**：Modbus TCP（MBAP）與 Modbus RTU over TCP
- **錯誤碼翻譯**：例外碼 0x01–0x0B 直接轉成中文說明與可能原因
- **白名單硬閘門**：不在白名單的目標，一個封包都不會發出去；改為回傳「授權請求」
- **寫入三段式保護**：預設 dry-run → 需 `confirm` → 需使用者同意原話，全程稽核 + 讀回驗證
- **零 Modbus 依賴**：訊框自行實作，diagnostics 會回傳實際收送的 hex bytes
- **熱重載**：手改 `config/*.json` 不用重啟 server

## 安裝

需要 Python 3.11+，以及 `mcp` 2.3 以上（本專案用 MCP 2.x 的 `MCPServer` API）。

```powershell
cd tcp-ip-modbus-MCP
uv sync
```

或用 pip：

```powershell
pip install -e .
```

## 先用模擬器驗證（不需要實機 PLC）

專案內附一台 Modbus 設備模擬器，支援 TCP 與 RTU-over-TCP 兩種框架：

```powershell
# 終端機 1：啟動模擬設備（TCP，127.0.0.1:5020）
uv run python scripts/modbus_sim.py --port 5020 --registers 100,200,300,400

# 終端機 2：透過真正的 MCP stdio 協定跑一輪完整驗證
uv run python scripts/demo_client.py
```

`demo_client.py` 會實際走完：握手 → 列出工具 → 白名單 → `tcp_check` → `modbus_probe`
→ 讀 holding/coil → dry-run → 缺 confirm 被擋 → 真寫入 + 讀回驗證 → 非白名單被擋
→ `check_all` → 稽核紀錄，最後印出「N 項通過」。

模擬器參數：

| 參數 | 說明 |
|---|---|
| `--port 5020` | 監聽埠（模擬器用 5020，避開真實設備的 502） |
| `--unit 1` | slave id |
| `--framer tcp\|rtu` | Modbus TCP（MBAP）或 RTU over TCP |
| `--registers 100,200,300` | holding / input register 初值 |
| `--coils 1,0,1` | coil 初值 |
| `--silent` | 只接受 TCP 連線但永不回應（模擬「TCP 通、Modbus 不通」） |

`config/devices.json` 已預先放好兩個佔位設備：`本機模擬器`（5020/tcp）與
`本機模擬器-RTU`（5021/rtu），等有實機時直接改成真實 IP 即可。

## 設定

| 檔案 | 用途 |
|---|---|
| `config/whitelist.json` | 根目錄白名單（尚未選案場時使用） |
| `config/devices.json` | 根目錄設備別名表（`別名 → host/port/unit/framer`），此檔內的設備視為已信任 |
| `config/sites.json` | 案場索引 + 目前作用中的案場 |
| `config/sites/<案場>/whitelist.json` | 該案場的白名單 |
| `config/sites/<案場>/devices.json` | 該案場的設備別名表 |
| `config/sites/_trash/` | 被移除的案場（搬過來，不會硬刪） |
| `logs/audit.jsonl` | 寫入、授權與案場切換的稽核紀錄（每筆都帶 `site`） |

**單一案場**（只有一個客戶 / 一個廠區）可以完全不建案場，直接用根目錄的
`whitelist.json` / `devices.json`，行為與舊版相同。

**多案場**：每個案場一組獨立白名單，只有「作用中」那一組會生效。

範例 `config/devices.json`：

```json
{
  "devices": [
    { "alias": "一號機", "host": "192.168.1.10", "port": 502, "unit": 1, "framer": "tcp" },
    { "alias": "電表",   "host": "192.168.1.20", "port": 5020, "unit": 5, "framer": "rtu" }
  ]
}
```

`framer`：`tcp` = Modbus TCP（MBAP header）；`rtu` = Modbus RTU over TCP（raw RTU 幀走 TCP）。

環境變數（全部可選）：

| 變數 | 預設 | 說明 |
|---|---|---|
| `MODBUS_MCP_CONFIG_DIR` | `./config` | 設定檔目錄 |
| `MODBUS_MCP_LOG_DIR` | `./logs` | 稽核紀錄目錄 |
| `MODBUS_MCP_SITE` | 空 | 強制指定作用中的案場（優先於 `sites.json`；適合每個案場跑一個 server 實例） |
| `MODBUS_WRITE_ENABLED` | `true` | 設 `false` 變成純唯讀 server |
| `MODBUS_TIMEOUT_MS` | `3000` | 預設逾時 |
| `MODBUS_MAX_CONCURRENCY` | `16` | `check_all` 併發硬上限 |
| `MODBUS_TEMP_TTL` | `600` | 一次性授權預設秒數 |
| `MODBUS_RESOLVE_HOSTNAMES` | `true` | 是否解析 hostname 以便比對 CIDR 白名單 |

## 接到 MCP client

### Cherry Studio

設定 → MCP 伺服器 → 新增 → 類型選 **STDIO**：

```json
{
  "command": "C:\\Users\\user\\Desktop\\baoqi\\開發\\my-mcp\\tcp-ip-modbus-MCP\\.venv\\Scripts\\python.exe",
  "args": ["-m", "modbus_mcp"],
  "cwd": "C:\\Users\\user\\Desktop\\baoqi\\開發\\my-mcp\\tcp-ip-modbus-MCP",
  "env": { "MODBUS_WRITE_ENABLED": "true" }
}
```

### Claude Desktop（`claude_desktop_config.json`）

```json
{
  "mcpServers": {
    "tcp-ip-modbus": {
      "command": "C:\\Users\\user\\Desktop\\baoqi\\開發\\my-mcp\\tcp-ip-modbus-MCP\\.venv\\Scripts\\python.exe",
      "args": ["-m", "modbus_mcp"],
      "cwd": "C:\\Users\\user\\Desktop\\baoqi\\開發\\my-mcp\\tcp-ip-modbus-MCP"
    }
  }
}
```

> `cwd` 建議設定，這樣相對路徑的 `config/`、`logs/` 會落在專案目錄。

## 工具（20 個）

### 案場管理
| 工具 | 說明 |
|---|---|
| `site_list` | 列出所有案場 + 目前作用中的一個 |
| `site_create` | 建立案場（可 `copy_from` 複製既有案場或根目錄當範本） |
| `site_use` | 切換作用中案場（`default` = 切回根目錄設定） |
| `site_show` | 看某個案場的白名單 + 設備表，不切換 |
| `site_delete` | 移除案場（搬到 `sites/_trash/`，需 `user_consent`；使用中的不能刪） |

### 白名單 / 授權 / 設備
| 工具 | 說明 |
|---|---|
| `whitelist_list` | 列出白名單 + 未到期的一次性授權（可帶 `site`） |
| `whitelist_add` | 永久加入（**必須帶 `user_consent`**，即使用者同意原話；可帶 `site`） |
| `whitelist_remove` | 永久移除（可帶 `site`） |
| `authorize_temporary` | 一次性授權（只在記憶體、會過期、重啟即失效） |
| `device_list` | 列出設備別名（可帶 `site`） |
| `device_add` | 新增 / 更新設備別名（`alias → host/port/unit/framer`） |
| `device_remove` | 移除設備別名 |

> 沒帶 `site` 的工具都作用在「目前作用中的案場」。

### 連通性 / Modbus
| 工具 | 說明 |
|---|---|
| `tcp_check` | 只做 TCP 握手，判斷網路/埠這一層 |
| `modbus_probe` | 最小讀取請求，判斷「TCP 通但 Modbus 有無回應」 |
| `modbus_read` | FC01/02/03/04，支援 `uint16/int16/uint32/int32/float32/float64/hex` 與 word/byte order |
| `modbus_write` | FC05/06/15/16，三段式保護 |
| `modbus_scan_units` | 掃 unit id 範圍找線上 slave |
| `modbus_diagnose` | 單一目標重試 N 次 → 成功率 / 延遲 / 抖動，判 `stable` / `flaky` / `down` |
| `check_all` | **一次檢查所有白名單（或設備表）**：分層、併發、總預算 60 秒（可帶 `site`） |
| `audit_tail` | 看最近的寫入 / 授權紀錄（可帶 `site` 只看某個案場） |

## 案場（多客戶 / 多廠區）管理

每個案場是**完全獨立的授權空間**：白名單、設備別名表、稽核標籤都分開。
切換案場 = 整組目標換掉，所以 A 客戶的 IP 不會在 B 客戶的案場裡被放行。

```
1) site_create("台中廠", copy_from="default")   ← 用根目錄設定當範本（或留空建空白案場）
2) whitelist_add("192.168.10.0/24", ports=[502], user_consent="…")
   device_add("一號機", "192.168.10.11", unit=1)
3) site_use("高雄廠")                            ← 切換，白名單整組換掉
4) check_all(level="read")                       ← 只檢查「目前案場」的設備
5) site_show("台中廠")                           ← 看別案場的內容，不會切換
```

- 想知道現在在哪個案場：`site_list`（回傳 `active_site`）
- 想一次性指定別案場、不動全域狀態：帶 `site="台中廠"` 給 `whitelist_list`、
  `device_list`、`check_all`、`whitelist_add` 等工具
- 想每個案場各跑一個 server 實例：設 `MODBUS_MCP_SITE=台中廠`，該行程就固定看那個案場
- 刪除案場只是搬到 `config/sites/_trash/<案場>-<時間>/`，要救回就把資料夾搬回去

> **邊界要講清楚**：切換案場不會撤銷已發出的一次性授權（那是 host:port 層級、在記憶體裡），
> 也不會影響寫入保護。要徹底收回，重啟 server 即可（一次性授權不落地）。

## 白名單與授權流程

1. 模型對非白名單目標操作 → **不會建立任何連線**，回傳：

```json
{
  "ok": false,
  "error": { "kind": "not_whitelisted" },
  "needs_authorization": {
    "host": "192.168.1.50", "port": 502,
    "request_id": "auth-1a2b3c4d",
    "options": ["temporary", "permanent"],
    "default_ttl_seconds": 600
  },
  "summary": "192.168.1.50:502 不在白名單內... 要授權我連線嗎？"
}
```

2. 你口頭同意 → 模型呼叫 `authorize_temporary`（一次性、會過期）或
   `whitelist_add`（永久，需把你的同意原話放進 `user_consent`）。

> **誠實說明限制**：MCP 架構下，模型呼叫哪個工具無法被人類「點確認」硬性阻擋。
> 因此設計成：預設只能取得**會過期的臨時授權**，永久授權則強制留下同意文字到稽核檔，
> 事後可以查出「誰、什麼時候、同意了什麼」。

## 寫入安全

```
dry_run=true（預設）  → 只回預演內容，不建立連線
dry_run=false + confirm=true + user_consent="…"  → 真正寫入
```

真正寫入時會：
1. 檢查白名單與 `MODBUS_WRITE_ENABLED`
2. 驗證設備回傳的 echo（功能碼 / 位址 / 值）
3. 寫入後**自動讀回**同一位址確認
4. 寫一筆 `logs/audit.jsonl`（含 `user_consent`、結果、讀回值）

## 回傳格式

所有工具都回傳 `ok` + `summary`（一句人話），細節在結構化欄位：

```json
{
  "ok": false,
  "summary": "192.168.1.10:502 unit=1 (tcp) TCP 通了，但 Modbus 沒有回應。",
  "target": "192.168.1.10:502 unit=1 (tcp)",
  "stage": "modbus",
  "error": {
    "kind": "modbus_timeout",
    "hint": "最常見：unit id 錯誤、設備忙碌、或 RTU-over-TCP 的序列參數不符。"
  }
}
```

常見 `error.kind`：

| kind | 意思 |
|---|---|
| `not_whitelisted` | 目標不在白名單，已停止且未連線 |
| `tcp_timeout` / `tcp_refused` / `tcp_dns_fail` / `tcp_unreachable` | TCP 層失敗（含原因） |
| `modbus_timeout` | TCP 通，但設備不回應 Modbus |
| `modbus_exception` | 設備回例外碼（附 `exception_name` 與中文說明） |
| `modbus_gateway_no_target` | gateway 有回應但找不到目標 slave（unit id 錯或 slave 離線） |
| `crc_error` / `protocol_error` | 訊框異常（framer 選錯、被截斷、多 client 共用連線） |
| `write_disabled` / `confirmation_required` / `consent_required` | 寫入保護觸發 |

## 開發

```powershell
uv run pytest -q            # 107 個測試：codec / 白名單 / 案場 / client / 批次 / 工具層
uv run python -m modbus_mcp # 以 stdio 啟動 server
```

專案結構：

```
scripts/
├─ modbus_sim.py       Modbus 設備模擬器（TCP / RTU-over-TCP）
└─ demo_client.py      透過 MCP stdio 跑一輪完整驗證

src/modbus_mcp/
├─ codec.py        Modbus 訊框編解碼（MBAP + RTU + CRC16 + 例外碼）
├─ client.py       非同步交易、read/write/probe/scan
├─ transport.py    TCP 握手與錯誤分類
├─ diagnostics.py  重試統計 + check_all 批次檢查
├─ safety.py       白名單比對、臨時授權、目標解析
├─ config.py       案場 / 白名單 / 設備 / 稽核存取 + 熱重載
├─ models.py       回傳格式 + register 編解碼
└─ server.py       MCP 工具定義（20 個）
```

## 疑難排解

| 現象 | 檢查 |
|---|---|
| `tcp_refused` | PLC/網關沒開、埠號不是 502、或防火牆擋 |
| `tcp_timeout` | IP 不同網段、設備未上電、VPN 未連 |
| `modbus_timeout`（TCP 卻通） | unit id 錯、設備忙碌、gateway 的 RTU 參數（baud/parity）不符 |
| `crc_error` | `framer` 選錯（TCP 設備用了 `rtu`，或反之） |
| `modbus_exception 0x02` | 位址不存在，對照設備手冊的 register map |
| 讀值明顯不對 | `word_order` / `byte_order` 換一組試（ABCD / CDAB / BADC / DCBA） |
| 切了案場後原本的 IP 變成 `not_whitelisted` | 正常：每個案場白名單獨立，要另外加進該案場 |

## 授權

僅供自己的設備維運使用。請只把**你有權存取的設備**放進白名單。
