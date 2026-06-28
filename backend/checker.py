# -*- coding: utf-8 -*-
"""
checker.py — 核心檢核邏輯
"""

import io
import uuid
from pathlib import Path
from datetime import datetime

from docx import Document as DocxDocument
import openpyxl

import gemini_client
from db_loader import filter_items, detect_scene, extract_skill_context, get_mention_weight
from config import TC4_THRESHOLD, LEVEL_ORDER


# --- 文件解析 ---

def extract_text_from_docx(file_bytes: bytes) -> str:
    doc = DocxDocument(io.BytesIO(file_bytes))
    parts = []
    # 段落文字
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    # 表格文字（逐列合併，以 | 分隔欄位）
    for table in doc.tables:
        for row in table.rows:
            row_text = "  |  ".join(c.text.strip() for c in row.cells if c.text.strip())
            if row_text:
                parts.append(row_text)
    return "\n".join(parts)


def extract_text_from_xlsx(file_bytes: bytes) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"[工作表：{ws.title}]")
        for row in ws.iter_rows(values_only=True):
            row_text = "  ".join(str(c) for c in row if c is not None)
            if row_text.strip():
                lines.append(row_text)
    wb.close()
    return "\n".join(lines)


def extract_text(file_bytes: bytes, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return extract_text_from_docx(file_bytes)
    elif suffix == ".xlsx":
        return extract_text_from_xlsx(file_bytes)
    else:
        raise ValueError(f"不支援的檔案格式：{suffix}")


# --- TC4 評分 ---

RESULT_SCORES = {
    "已達標":   1.0,
    "已完成":   1.0,
    "部分達標": 0.5,
    "部分完成": 0.5,
    "未達標":   0.0,
    "未完成":   0.0,
    "需補件":   0.0,
    "需確認":   0.0,
    "不適用":   None,
}


def calculate_tc4_score(items_result: list, db_items: list) -> dict:
    db_map = {it["seq"]: it for it in db_items}
    weighted_sum = 0.0
    weight_total = 0.0
    detail = []

    for res in items_result:
        seq        = res.get("seq")
        result     = res.get("result", "未達標")
        base_score = RESULT_SCORES.get(result)

        if base_score is None:
            continue

        db_item = db_map.get(seq, {})
        mention = db_item.get("mention_count", 0)
        weight  = get_mention_weight(mention)

        weighted_sum += base_score * weight
        weight_total += weight
        detail.append({
            "seq":    seq,
            "item":   res.get("item", ""),
            "result": result,
            "weight": weight,
            "score":  round(base_score * weight, 2),
        })

    if weight_total == 0:
        final_score = 0.0
    else:
        final_score = round((weighted_sum / weight_total) * 100, 1)

    return {
        "score":  final_score,
        "passed": final_score >= TC4_THRESHOLD,
        "detail": detail,
    }


# --- 主流程 ---

def run_check(
    file_bytes: bytes,
    filename: str,
    scene_hint: str = "",
    member_name: str = "",
    context_input: str = "",
    gemini_api_key: str = None,
    gemini_model: str = None,
    pre_extracted: str = None,
    api_provider: str = "gemini",
    third_party_key: str = None,
    third_party_model: str = None,
    third_party_base_url: str = "https://api.runapi.sbs/v1",
) -> dict:
    check_id  = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    # 共用 AI 呼叫參數
    ai_kwargs = dict(
        api_key              = gemini_api_key,
        model_override       = gemini_model,
        provider             = api_provider,
        third_party_key      = third_party_key,
        third_party_model    = third_party_model,
        third_party_base_url = third_party_base_url,
    )

    # 1. 解析文件
    doc_text = ""
    if pre_extracted:
        doc_text = pre_extracted
    elif file_bytes and filename:
        doc_text = extract_text(file_bytes, filename)

    # 合併學員說明文字
    if context_input.strip():
        context_section = f"【學員說明與背景】\n{context_input.strip()}\n\n"
        doc_text = context_section + doc_text

    detect_text = doc_text if doc_text else context_input

    # 2. 場景辨識（第一輪 AI）
    if scene_hint:
        # member 手動指定場景，直接使用
        scenes = [scene_hint]
        confidence = 100
    else:
        # 讓 AI 讀完文件，選出最相關的 3 個場景
        scenes = gemini_client.detect_scenes_ai(detect_text, **ai_kwargs)
        confidence = 90  # AI 辨識信心值

    # 3. 層級判斷（保留關鍵字備援）
    fallback = detect_scene(detect_text)
    level    = fallback["level"]

    # 4. 篩選檢核項目 + skill context（多場景合併去重）
    db_items      = filter_items(scenes, level)
    skill_context = extract_skill_context(scenes, level)

    # 5. 第二輪 AI：完整分析
    ai_result = gemini_client.analyze(
        doc_text      = doc_text,
        scenes        = scenes,
        level         = level,
        check_items   = db_items,
        skill_context = skill_context,
        **ai_kwargs,
    )

    if "error" in ai_result:
        return {
            "check_id":  check_id,
            "error":     ai_result["error"],
            "timestamp": timestamp,
        }

    # 6. TC4 評分
    tc4 = calculate_tc4_score(ai_result.get("items", []), db_items)

    # 7. 把 mention_count 和 weight 合併進每個 item
    db_map = {it["seq"]: it for it in db_items}
    items = []
    for it in ai_result.get("items", []):
        db_it = db_map.get(it.get("seq"), {})
        mention = db_it.get("mention_count", 0)
        weight  = get_mention_weight(mention)
        items.append({
            **it,
            "mention_count": mention,
            "weight":        weight,
        })

    # 8. 統計
    stats = {r: sum(1 for i in items if i.get("result") == r)
             for r in ["已達標", "部分達標", "未達標", "不適用", "需補件", "需確認"]}

    return {
        "check_id":          check_id,
        "timestamp":         timestamp,
        "filename":          filename,
        "member_name":       member_name,
        "scene":             ai_result.get("scene_confirmed", scenes[0] if scenes else ""),
        "scenes_used":       ai_result.get("scenes_used", scenes),
        "level":             ai_result.get("level_confirmed", level),
        "scene_note":        ai_result.get("scene_note", ""),
        "auto_confidence":   confidence,
        "score":             tc4["score"],
        "passed":            tc4["passed"],
        "stats":             stats,
        "items":             items,
        "overall_comment":   ai_result.get("overall_comment", ""),
        "total_db_items":    len(db_items),
        "has_context_input": bool(context_input.strip()),
    }
