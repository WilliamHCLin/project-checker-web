# 生命動能協會 專案檢核系統 — SPEC

> 接手 AI 必讀。本文件描述系統的完整現狀、架構、流程、已知問題與待辦事項。
> 最後更新：2026-06-28（最新 commit：`8a3ed1a`）

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
  ├─ 讓 AI 讀完文件（含段落 + 表格）
  └─ 從 21 個場景代碼中選出最相關的 3 個（max_tokens=512）
    ↓
db_loader.filter_items(scenes, level)
  ├─ 多場景合併撈出檢核項目（去重）
  └─ G. 專案管理基本功 永遠包含（通用底層）
    ↓
db_loader.extract_skill_context(scenes, level)
  └─ 從三份 Skill MD 萃取相關段落注入 prompt
    ↓
【第二輪 AI】gemini_client.analyze()
  ├─ 四步驟：理解文件 → 推論式對照 → 謹慎判定缺失 → 具體建議
  └─ 回傳 JSON：scene_confirmed, scenes_used, items[], overall_comment
    ↓
checker.py: 把 mention_count + weight 合併進每個 item
    ↓
checker.calculate_tc4_score()
  └─ 100 分往下扣，依 mention_count 加權計算
    ↓
回傳結果 JSON，前端直接渲染在右欄（含權重標籤）
```

### 4.2 場景辨識邏輯

- **member 手動選**（前端下拉）：直接使用，跳過 AI 辨識
- **未選（自動）**：第一輪 AI 讀完文件，從 21 個場景中選 3 個
- **AI 失敗備援**：`db_loader.detect_scene()` 用關鍵字比對（只在 AI 掛掉時觸發）

### 4.3 TC4 評分（100 分往下扣）

**扣分比例：**

| 結果 | 扣分比例 |
|---|---|
| 已達標 / 已完成 | 0（不扣）|
| 部分達標 / 部分完成 | 0.5（扣一半）|
| 未達標 / 未完成 / 需補件 / 需確認 / 有問題 / 缺失資訊 | 1.0（全扣）|
| 不適用 | 不計入 |

**加權**（依 William 提及次數）：

| mention_count | 權重 |
|---|---|
| ≥ 6 次 | 1.5（高）|
| 4–5 次 | 1.2（中高）|
| 1–3 次 | 1.0（中）|
| 0 次 | 0.8（一般）|

**公式：**
```
TC4 = 100 - (Σ 扣分比例 × 權重) / (Σ 計入項目的權重) × 100
```

全部達標 = **100 分**；門檻 **70 分**以上可找 William 老師開會。

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
files[]              — 上傳檔案（可多個，.docx / .xlsx）
member_name          — 學員姓名
scene_hint           — 手動指定場景（空字串 = 自動辨識）
context_input        — 學員說明文字
api_provider         — "gemini" 或 "openai_compat"
gemini_api_key       — Gemini API Key（可覆寫環境變數）
gemini_model         — Gemini 模型名稱（可覆寫）
third_party_key      — 第三方 API Key（sk-xxx）
third_party_model    — 第三方模型名稱
third_party_base_url — 第三方接口地址（預設 https://api.runapi.sbs/v1）
```

**注意**：`checker.run_check()` 整個呼叫包在 `try/except` 裡，任何後端錯誤都會回傳 `{"detail": "後端執行錯誤：..."}` JSON，不再讓 Render 回純文字 500。

### `backend/gemini_client.py`

**`ALL_SCENES`**：21 個合法場景代碼的列表（與資料庫同步，若資料庫新增場景必須更新此處）。

**`detect_scenes_ai(doc_text, ...)`**：第一輪 AI，輕量呼叫（max_tokens=512），讀完文件選出最多 3 個場景。失敗時回傳 `["G. 專案管理基本功"]`。

**`build_prompt(doc_text, scenes, level, check_items, skill_context)`**：第二輪分析 prompt，四步驟分析邏輯：理解 → 推論 → 謹慎判定 → 具體建議。

**`_parse_json(raw)`**：三段容錯 JSON 解析：
1. 移除 markdown fence + 跳過思考型模型前置文字（`rfind('\n{')`)
2. 直接解析
3. 找最後 `}]` 補 overall_comment 收尾（output 截斷修復）
4. 找最後完整 item 的 `}` 補 `]` 收尾（更嚴重截斷修復）

**`analyze(...)`**：第二輪 AI，`max_output_tokens=32768`（Gemini flash 上限，避免截斷）。

### `backend/checker.py`

**`extract_text_from_docx(file_bytes)`**：讀取 docx 的**段落 + 表格**（早期只讀段落，已修正，cf8abe3）。

**`DEDUCT_RATIO`**：扣分比例表（取代舊的 RESULT_SCORES 正向給分）。

**`calculate_tc4_score()`**：100 分扣分制，公式見 4.3 節。

**`run_check()`**：主流程，`ai_kwargs` 統一傳遞 API 參數給兩輪 AI。執行完後把 `mention_count` 和 `weight` 合併進每個 item，前端直接使用。

### `backend/db_loader.py`

**`load_db()`**：`@lru_cache`，讀取 `data/檢核資料庫.xlsx`。

**`get_all_scenes()`**：回傳資料庫中所有不重複場景，供 `/api/scenes` 使用（不再用 config.py 的舊清單）。

**`filter_items(scenes, level)`**：接受 `str` 或 `list[str]`，多場景合併去重，G 通用底層永遠包含。

**`extract_skill_context(scenes, level)`**：從三份 Skill MD 萃取相關段落（各有字元上限）。

### `frontend/index.html`

- 兩欄佈局：左欄固定 420px（輸入），右欄彈性（結果）
- 提交後結果直接渲染在右欄，不跳頁
- 場景下拉從 `/api/scenes` 動態載入（21 個真實場景）
- `dropZone` 是 `<div onclick="...">` 而非 `<label>`（label 會破壞 drag-drop）
- `fileInput` 有 `multiple` 屬性，支援多檔上傳
- 設定可存 `localStorage`（API key、模型等）
- 每個項目卡片右上角顯示**權重標籤**（⚖️ 高×1.5 / 中高×1.2 / 中×1.0 / 一般×0.8）
- fetch 改用 `resp.text()` 先讀再 `JSON.parse`，避免 502/503 純文字讓 `.json()` 爆炸

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

> 注意：比賽類場景在資料庫是 `F. 競賽管理`。`config.py` 裡的 `SCENE_KEYWORDS` 有舊的 `C. 比賽類專案` 是歷史遺留，備援辨識時可能誤判，但主流程已改由 AI 辨識，影響很小。

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
git pull origin main      # 先拉最新
# 修改檔案...
python3 -c "import ast; ast.parse(open('backend/xxx.py').read()); print('OK')"  # 驗語法
git add .
git commit -m "描述"
git push origin main
# Render 自動偵測 push，重新 build + deploy（約 2–3 分鐘）
```

**重要：修改 Python 檔案用 heredoc，不要用 cp 覆蓋**（cp 會把 CRLF / 舊版本帶進去）：
```bash
cat > /tmp/repo/backend/xxx.py << 'PYEOF'
# ... 完整檔案內容 ...
PYEOF
python3 -c "import ast; ast.parse(open('/tmp/repo/backend/xxx.py').read()); print('OK')"
```

---

## 9. 已解決的重要 Bug

| commit | 問題 | 修法 |
|---|---|---|
| `cf8abe3` | docx 以表格為主的文件被 AI 誤判「目的缺失」 | `extract_text_from_docx` 加入 `doc.tables` 讀取 |
| `b6a0c05` | 程式關鍵字猜場景不準，容易誤判 | 改成第一輪 AI 讀文件選 3 個場景 |
| `1431eb8` | `detect_scenes_ai` 被 cp 覆蓋消失，部署後立即 500 | 用 heredoc 完整重寫，禁止用 cp 覆蓋 |
| `4a79204` | Gemini 回傳截斷（JSON 找不到結尾）| `max_output_tokens` 8192→32768，三段截斷修復 |
| `49ef356` | Render 502 純文字讓前端 `resp.json()` 爆炸 | 改用 `resp.text()` 先讀再 `JSON.parse` |
| `a10d159` | main.py traceback f-string 內換行 SyntaxError | 改用字串相加 |
| `8a3ed1a` | TC4 是 0 分往上加，與設計相反 | 改為 100 分往下扣，未達標依權重扣分 |
| `fb650fb` | 無法看出哪些項目優先改 | 結果卡片加權重標籤（⚖️ 高/中高/中/一般）|

---

## 10. 已知問題 / 待辦

- [ ] **`result.html` 統計標籤未更新**：`STAT_LABELS` 還用舊標籤（已完成/部分完成），但 AI 現在回傳「已達標/部分達標」，統計欄位 emoji 會空白
- [ ] **`config.py` 的 `SCENE_KEYWORDS` 與資料庫場景代碼不一致**：關鍵字備援可能誤判，主流程已不影響
- [ ] **Render free tier 無持久化 SQLite**：每次重新部署歷史記錄消失，需要持久化請接 PostgreSQL（設定 `DATABASE_URL` 環境變數）
- [ ] **`models.py` 未存 `scenes_used` 和 `mention_count/weight`**：新欄位沒有進 DB，歷史查詢時這些資料遺失
- [ ] **前端場景下拉只能單選**：AI 自動選 3 個場景，但 member 手動只能選 1 個

---

## 11. 接手開發注意事項

1. **改 Python 檔案必須用 heredoc**，禁止用 `cp` 從本地覆蓋 `/tmp/repo`，否則會把舊版本或 CRLF 帶進去
2. **改完立即驗語法**：`python3 -c "import as