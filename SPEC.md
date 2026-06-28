# 生命動能協會 專案檢核系統 — SPEC

> 接手 AI 必讀。本文件描述系統的完整現狀、架構、流程、已知問題與待辦事項。
> 最後更新：2026-06-28

---

## 1. 專案概覽

**用途**：幫助生命動能協會的 member 在送件給 William 老師前，自行上傳規劃文件，讓 AI 對照 437 筆檢核資料庫找出落差，計算 TC4 評分（70 分以上才可預約開會）。

**GitHub**：`WilliamHCLin/project-checker-web`
**部署平台**：Render（Free tier，Docker 部署，閒置 15 分鐘後 cold start）
**使用者**：William 老師 + 協會 member

---

## 2. 技術棧

| 層 | 技術 |
|---|---|
| 後端 | Python 3.11 + FastAPI + Uvicorn |
| AI（主）| Google Gemini（`google-genai` SDK，`genai.Client`）|
| AI（備）| 任何 OpenAI-compatible 第三方 API（`openai` SDK，`base_url` 覆寫）|
| 文件解析 | `python-docx`（docx）、`openpyxl`（xlsx）|
| 資料庫 | SQLite（本地）或透過 `DATABASE_URL` 環境變數接 PostgreSQL |
| ORM | SQLAlchemy 2.0 |
| 前端 | 純 HTML + Tailwind CSS（CDN）|
| 部署 | Docker（`Dockerfile`）+ `render.yaml` |

---

## 3. 目錄結構

```
project-checker-web/
├── Dockerfile
├── render.yaml              # Render 部署設定
├── railway.json             # Railway 備用（目前用 Render）
├── requirements.txt
├── SPEC.md                  # ← 本文件
├── backend/
│   ├── main.py              # FastAPI 入口，所有路由
│   ├── checker.py           # 核心流程（文件解析 → 場景辨識 → AI 分析 → 評分）
│   ├── gemini_client.py     # AI 呼叫（第一輪場景辨識 + 第二輪分析）
│   ├── db_loader.py         # 讀取 Excel 資料庫 + Skill 文件 + 場景辨識備援
│   ├── models.py            # SQLAlchemy CheckRecord 模型 + DB 操作
│   ├── report_generator.py  # 產生 Word / HTML 報告
│   ├── config.py            # 全域設定（路徑、模型、閾值、關鍵字）
│   └── data/
│       ├── 檢核資料庫.xlsx           # 437 筆檢核項目（主資料庫）
│       ├── William_工作決策框架(...).md
│       ├── William_生命動能協會管理_決策框架.md
│       └── William_完整情境決策原則庫(188條).md
├── frontend/
│   ├── index.html           # 主頁（兩欄：左輸入、右結果）
│   ├── result.html          # 獨立結果頁（歷史記錄用，目前主流程不跳轉此頁）
│   └── admin.html           # 管理頁（查看歷史記錄，需 X-Admin-Token header）
```

---

## 4. 核心流程

### 4.1 上傳檢核（`POST /api/check`）

```
member 上傳文件 + 填說明文字
    ↓
main.py: 讀取所有上傳檔案，每個呼叫 checker.extract_text()
    ↓
checker.py: 合併文字 → 組成 doc_text
    ↓
【第一輪 AI】gemini_client.detect_scenes_ai()
  ├─ 讓 AI 讀完文件
  └─ 從 21 個場景代碼中選出最相關的 3 個
    ↓
db_loader.filter_items(scenes, level)
  ├─ 多場景合併撈出檢核項目（去重）
  └─ G. 專案管理基本功 永遠包含（通用底層）
    ↓
db_loader.extract_skill_context(scenes, level)
  └─ 從三份 Skill MD 萃取相關段落注入 prompt
    ↓
【第二輪 AI】gemini_client.analyze()
  ├─ 完整分析：理解文件 → 推論式對照 → 謹慎判定缺失
  └─ 回傳 JSON：scene_confirmed, items[], overall_comment
    ↓
checker.calculate_tc4_score()
  └─ 依 mention_count 加權，計算百分制 TC4 分數
    ↓
回傳結果 JSON，前端直接渲染在右欄
```

### 4.2 場景辨識邏輯

- **member 手動選**（前端下拉）：直接使用，跳過 AI 辨識
- **未選（自動）**：第一輪 AI 讀完文件，從 21 個場景中選 3 個
- **AI 失敗備援**：`db_loader.detect_scene()` 用關鍵字比對（舊邏輯，只在 AI 掛掉時觸發）

### 4.3 TC4 評分

| 結果 | 分數 |
|---|---|
| 已達標 / 已完成 | 1.0 |
| 部分達標 / 部分完成 | 0.5 |
| 未達標 / 未完成 / 需補件 / 需確認 | 0.0 |
| 不適用 | 不計入 |

加權由 `mention_count`（被 William 提及次數）決定：

| mention_count | 權重 |
|---|---|
| ≥ 6 | 1.5（HIGH）|
| 4–5 | 1.2（MID）|
| 1–3 | 1.0（LOW）|
| 0   | 0.8（ZERO）|

門檻：**70 分** 以上可找 William 老師開會。

---

## 5. 檔案說明

### `backend/main.py`

FastAPI 路由：

| 路由 | 說明 |
|---|---|
| `GET /` | 回傳 `frontend/index.html` |
| `POST /api/check` | 主要檢核 API |
| `GET /api/result/{check_id}` | 查詢歷史結果 |
| `GET /api/report/{check_id}/word` | 下載 Word 報告 |
| `GET /api/report/{check_id}/html` | 開啟 HTML 報告（可列印成 PDF）|
| `GET /api/scenes` | 回傳資料庫中所有場景清單（前端下拉用）|
| `GET /api/admin/history` | 查看所有歷史記錄（需 `X-Admin-Token` header）|
| `POST /api/admin/reload-db` | 重新載入 Excel 資料庫（上傳新版後呼叫）|
| `GET /health` | 健康檢查（Render 用）|
| `GET /admin` | 管理頁面 HTML |

`/api/check` 接受的 Form 欄位：

```
files[]             — 上傳檔案（可多個，.docx / .xlsx）
member_name         — 學員姓名
scene_hint          — 手動指定場景（空字串 = 自動辨識）
context_input       — 學員說明文字
api_provider        — "gemini" 或 "openai_compat"
gemini_api_key      — Gemini API Key（可覆寫環境變數）
gemini_model        — Gemini 模型名稱（可覆寫）
third_party_key     — 第三方 API Key（sk-xxx）
third_party_model   — 第三方模型名稱
third_party_base_url— 第三方接口地址（預設 https://api.runapi.sbs/v1）
```

### `backend/gemini_client.py`

**`ALL_SCENES`**：21 個合法場景代碼的列表（與資料庫同步）。

**`detect_scenes_ai(doc_text, ...)`**：第一輪 AI，輕量呼叫（max_tokens=512），讀完文件選出 3 個場景，回傳 `list[str]`。失敗時回傳 `["G. 專案管理基本功"]`。

**`build_prompt(doc_text, scenes, level, check_items, skill_context)`**：組成第二輪分析 prompt。指令邏輯：
1. 先完整理解文件（含表格）
2. 推論式對照（不需要明確標題關鍵字）
3. 謹慎判定缺失（整份文件找不到才標未達標）
4. 給具體可執行建議

**`_parse_json(raw)`**：容錯 JSON 解析：
- 移除 markdown code fence
- `rfind('\n{')` 跳過思考型模型輸出的前置文字
- 截斷修復：找最後的 `}]` 補上 `overall_comment` 結尾

**`analyze(...)`**：第二輪 AI 呼叫，支援 Gemini 和 OpenAI-compat。

### `backend/checker.py`

**`extract_text_from_docx(file_bytes)`**：讀取 docx 的**段落 + 表格**（早期版本只讀段落，已修正）。

**`extract_text_from_xlsx(file_bytes)`**：讀取所有工作表。

**`run_check(...)`**：主流程，接受 `pre_extracted` 參數（已由 main.py 解析好的文字），執行兩輪 AI 分析。

### `backend/db_loader.py`

**`load_db()`**：讀取 `data/檢核資料庫.xlsx` 的「檢核資料庫」sheet，`@lru_cache` 快取。

**`get_all_scenes()`**：回傳資料庫中所有不重複場景，供 `/api/scenes` 使用。

**`filter_items(scenes, level)`**：接受 `str` 或 `list[str]`，多場景合併去重。

**`extract_skill_context(scenes, level)`**：從三份 Skill MD 萃取相關段落（各有字元上限）。

**`detect_scene(text)`**：舊的關鍵字備援辨識，僅在 AI 辨識失敗時使用。

### `backend/config.py`

重要常數：

```python
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-latest")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TC4_THRESHOLD  = 70
LEVEL_ORDER    = {"A": 3, "B": 2, "C": 1}
```

`SCENE_KEYWORDS`：舊的關鍵字辨識用，已不是主要場景辨識邏輯，但備援仍用。

### `frontend/index.html`

- 兩欄佈局：左欄固定 420px（輸入），右欄彈性（結果）
- 提交後結果直接渲染在右欄，不跳頁
- 場景下拉從 `/api/scenes` 動態載入（21 個真實場景）
- `dropZone` 是 `<div onclick="...">` 而非 `<label>`（label 會破壞 drag-drop）
- `fileInput` 有 `multiple` 屬性
- 設定可存 `localStorage`（API key、模型等）
- 篩選按鈕：全部 / 缺失資訊 / 部分達標 / 未達標 / 已達標 / 不適用

---

## 6. 資料庫結構

### `data/檢核資料庫.xlsx`（「檢核資料庫」sheet）

| 欄位（欄序） | 說明 |
|---|---|
| row[0] | seq（序號）|
| row[1] | scene（場景代碼）|
| row[2] | scope（範圍）|
| row[3] | item（檢核項目）|
| row[4] | standard（應達到的標準）|
| row[5] | needed_info（需要的資訊）|
| row[6] | level（A/B/C）|
| row[7] | respondent（負責人）|
| row[8] | common_errors（常見錯誤）|
| row[9] | mention_count（被提及次數，決定加權）|
| row[10]| notes（備註）|

**21 個場景代碼**（2026-06 現狀）：

```
A. 班級日常管理
A. 班級日常經營
D1. 對外藝文交流：前期籌備
D2. 對外藝文交流：交通與後勤
D3. 對外藝文交流：彩排與演出
D4. 對外藝文交流：公關與接待
D5. 對外藝文交流：成果與後續
D6. 對外藝文交流：整復服務
E. 招生與傳承
F. 人力資源管理
F. 外部邀約應對
F. 競賽管理
G. 專案管理基本功
H. 人員/紀律
H. 個人升學/書審
I. 師資與傳承管理
I. 教學品質管理
J. 設備物資管理
K. 班級經營決策框架
L. 做事標準框架
M. 會議管理與追蹤
```

> 注意：比賽類場景在資料庫是 `F. 競賽管理`，不是 `C. 比賽類專案`。`config.py` 裡的 `SCENE_KEYWORDS` 有舊的 `C. 比賽類專案` 是歷史遺留，已不是主要辨識來源。

---

## 7. 環境變數（Render 後台設定）

| 變數 | 說明 | 預設值 |
|---|---|---|
| `GEMINI_API_KEY` | Google Gemini API Key | 無（必填）|
| `GEMINI_MODEL` | Gemini 模型名稱 | `gemini-2.0-flash-latest` |
| `ADMIN_TOKEN` | 管理員 token（`X-Admin-Token` header）| `william_admin_2026` |
| `DATABASE_URL` | 資料庫連線字串 | SQLite（`data/history.db`）|

---

## 8. 部署

**Render 設定**：
- Runtime：Docker（`Dockerfile`）
- Health check：`GET /health`
- Free tier：閒置 15 分鐘後休眠，下次請求 cold start 約 30–60 秒

**推送流程**：
```bash
cd /tmp/repo
git add .
git commit -m "描述"
git push origin main
# Render 自動偵測 push，重新 build + deploy
```

**Git token（歷史）**：
- 過期 token：`ghp_DgynYKsJM2dAZ0bTVz5rMmzTB8mMON0pcDHm`（已失效）
- 工作用 token：寫在 `/tmp/repo` 的 remote URL 中

---

## 9. 已解決的重要 Bug

### Bug 1：docx 表格內容未讀取
**現象**：上傳以表格為主的 docx，AI 說「專案目的完全缺失」
**原因**：`extract_text_from_docx` 只讀 `doc.paragraphs`，不讀 `doc.tables`
**修正**（commit `cf8abe3`）：加入表格逐列讀取，欄位以 `|` 分隔

### Bug 2：drag-drop 失效
**現象**：拖放檔案沒反應
**原因**：dropZone 用 `<label for="fileInput">` 時，dragover/drop 事件被吃掉
**修正**：改用 `<div onclick="document.getElementById('fileInput').click()">`

### Bug 3：第三方 API JSON "Extra data"
**現象**：思考型模型在 JSON 前輸出推理文字，解析失敗
**修正**：`_parse_json` 用 `rfind('\n{')` 找最後一個 JSON 起始點

### Bug 4：第三方 API JSON "Expecting ','"
**現象**：輸出 token 超限，JSON 被截斷
**修正**：`max_tokens=16000` + 截斷修復邏輯（找 `}]` 補尾）

### Bug 5：場景誤判（e.g. 比賽文件被判為其他場景）
**原因**：程式關鍵字辨識能力有限，21 個場景中有複雜子分類
**修正**（commit `b6a0c05`）：改成兩輪 AI，第一輪 AI 讀文件選 3 個場景，不再靠關鍵字猜測

---

## 10. 已知問題 / 待辦

- [ ] **`result.html` 統計標籤未更新**：前端 `STAT_LABELS` 還用舊標籤（已完成/部分完成），但 AI 現在回傳「已達標/部分達標」，統計欄位顯示會是空 emoji
- [ ] **`config.py` 的 `SCENE_KEYWORDS` 與資料庫場景代碼不一致**（`C. 比賽類專案` vs `F. 競賽管理`）：關鍵字備援仍可能誤判，但已不影響主流程
- [ ] **Render free tier 無持久化 SQLite**：每次重新部署歷史記錄會消失，若需要持久化需接 PostgreSQL
- [ ] **`models.py` 未存 `scenes_used`**：新版回傳 `scenes_used`（3 個場景），但 DB 模型沒有此欄位，歷史查詢時此欄位遺失
- [ ] **前端場景下拉僅支援單選**：現在 AI 自動選 3 個場景，但 member 手動只能選 1 個，未來可考慮多選

---

## 11. 接手開發注意事項

1. **改 Python 檔案務必用 heredoc**（`cat > file << 'EOF'`），不要用 Write tool 直接寫，容易 CRLF / 截斷造成 SyntaxError
2. **改完後驗語法**：`python3 -c "import ast; ast.parse(open('file.py').read()); print('OK')"`
3. **`/tmp/repo`** 是 git clone 的副本，改完 copy 過去再 push
4. **`gemini_client.py` 的 `ALL_SCENES`** 要和資料庫同步，若資料庫新增場景記得更新
5. **兩輪 AI 的 token 消耗**：第一輪 512 tokens（場景辨識），第二輪最多 8192 tokens（完整分析），Gemini free tier 每分鐘有 RPM 限制，測試時注意
6. **Render cold start**：閒置後第一次請求約等 30–60 秒，這不是 bug
