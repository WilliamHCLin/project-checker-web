# -*- coding: utf-8 -*-
"""
main.py — FastAPI 入口
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import checker
import report_generator
from models import save_record, get_all_records, get_record
from config import (
    ADMIN_TOKEN, MAX_UPLOAD_MB, ALLOWED_EXTENSIONS,
    SCENE_KEYWORDS, OUTPUT_DIR
)

app = FastAPI(title="專案檢核系統", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 靜態前端
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ─── 首頁 ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>專案檢核系統運行中</h1><p><a href='/docs'>API 文件</a></p>")


# ─── 主要 API ────────────────────────────────────────────────────────

@app.post("/api/check")
async def api_check(
    file:        UploadFile = File(...),
    scene_hint:  str        = Form(""),
    member_name: str        = Form(""),
):
    """
    上傳規劃文件，執行完整檢核流程。
    回傳 JSON 結果（含評分、逐項結果）。
    """
    # 檔案類型檢查
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"不支援的檔案格式 {suffix}，請上傳 .docx 或 .xlsx")

    # 檔案大小檢查
    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"檔案超過 {MAX_UPLOAD_MB}MB 限制")

    # 執行檢核
    result = checker.run_check(
        file_bytes   = file_bytes,
        filename     = file.filename,
        scene_hint   = scene_hint,
        member_name  = member_name,
    )

    if "error" in result:
        raise HTTPException(500, result["error"])

    # 存歷史記錄
    try:
        save_record(result)
    except Exception:
        pass  # 存 DB 失敗不影響回傳

    return result


@app.get("/api/result/{check_id}")
async def api_get_result(check_id: str):
    """取得歷史檢核結果。"""
    rec = get_record(check_id)
    if not rec:
        raise HTTPException(404, "找不到此檢核記錄")
    return rec


@app.get("/api/report/{check_id}/word")
async def api_download_word(check_id: str):
    """下載 Word 格式報告。"""
    rec = get_record(check_id)
    if not rec:
        raise HTTPException(404, "找不到此檢核記錄")
    word_bytes = report_generator.generate_word_report(rec)
    filename   = f"檢核報告_{check_id[:8]}.docx"
    return Response(
        content     = word_bytes,
        media_type  = "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers     = {"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/report/{check_id}/html")
async def api_download_html(check_id: str):
    """取得 HTML 格式報告（可在瀏覽器列印成 PDF）。"""
    rec = get_record(check_id)
    if not rec:
        raise HTTPException(404, "找不到此檢核記錄")
    html = report_generator.generate_html_report(rec)
    return HTMLResponse(content=html)


# ─── 管理員 API ──────────────────────────────────────────────────────

def _check_admin(token: Optional[str]):
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "需要管理員 token")


@app.get("/api/admin/history")
async def api_history(x_admin_token: Optional[str] = Header(None)):
    """William 查看所有檢核記錄（需 X-Admin-Token header）。"""
    _check_admin(x_admin_token)
    return get_all_records(limit=200)


@app.post("/api/admin/reload-db")
async def api_reload_db(x_admin_token: Optional[str] = Header(None)):
    """重新載入 Excel 資料庫和 Skill 文件（上傳新版後呼叫）。"""
    _check_admin(x_admin_token)
    from db_loader import load_db, load_skills
    load_db.cache_clear()
    load_skills.cache_clear()
    return {"status": "ok", "message": "資料庫與 Skill 已重新載入"}


@app.get("/api/scenes")
async def api_scenes():
    """回傳所有可用場景清單（前端下拉選單用）。"""
    return {"scenes": list(SCENE_KEYWORDS.keys())}


# ─── 管理頁面 ────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    admin_path = FRONTEND_DIR / "admin.html"
    if admin_path.exists():
        return HTMLResponse(content=admin_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>管理頁面</h1>")


# ─── 健康檢查 ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    from db_loader import load_db, load_skills
    db_count   = len(load_db())
    skill_keys = list(load_skills().keys())
    return {
        "status":     "ok",
        "db_items":   db_count,
        "skills_loaded": skill_keys,
    }
