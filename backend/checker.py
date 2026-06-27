# -*- coding: utf-8 -*-
"""
checker.py — 核心檢核邏輯：解析文件 → 辨識場景 → 呼叫 Gemini → 計算 TC4 評分
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


# ─── 文件解析 ─────────────────────────────────────────────────────────

def extract_text_from_docx(file_bytes: bytes) -> str:
    doc = DocxDocument(io.BytesIO(file_bytes))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


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


# ─── TC4 評分 ────────────────────────────────────────────────────────

RESULT_SCORES = {
    "已完成":   1.0,
    "部分完成": 0.5,
    "未完成":   0.0,
    "需補件":   0.0,
    "需確認":   0.0,
    "不適用":   None,
}


def calculate_tc4_score(items_result: list[dict], db_items: list[dict]) -> dict:
    db_map = {it["seq"]: it for it in db_items}
    weighted_sum = 0.0
    weight_total = 0.0
    detail = []

    for res in items_result:
        seq      = res.get("seq")
        result   = res.get("result", "未完成")
        base_score = RESULT_SCORES.get(result)

        if base_score is None:
            continue

        db_item = db_map.get(seq, {})
        mention = db_item.get("mention_count", 0)
        weight  = get_mention_weight(mention)

        weighted_sum  += base_score * weight
        weight_total  += weight
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


# ─── 主流程 ──────────────────────────────────────────────────────────

def run_check(
    file_bytes: bytes,
    filename: str,
    scene_hint: str = "",
    member_name: str = "",
    context_input: str = "",
    gemini_api_key: str = None,
    gemini_model: str = None,
) -> dict:
    check_id  = str(uuid.uuid4())
    timestamp = datetime.now().isoformat()

    # 1. 解析文件（可選）
    doc_text = ""
    if file_bytes and filename:
        doc_text = extract_text(file_bytes, filename)

    # 合併學員說明文字（放在文件內容前面）
    if context_input.strip():
        context_section = f"【學員說明與背景】\n{context_input.strip()}\n\n"
        doc_text = context_section + doc_text

    detect_text = doc_text if doc_text else context_input

    # 2. 場景辨識
    auto_detect = detect_scene(detect_text)
    scene      = scene_hint if scene_hint else auto_detect["scene"]
    level      = auto_detect["level"]
    confidence = auto_detect["confidence"] if not scene_hint else 100

    # 3. 篩選檢核項目 + skill context
    db_items      = filter_items(scene, level)
    skill_context = extract_skill_context(scene, level)

    # 4. 呼叫 Gemini
    gemini_result = gemini_client.analyze(
        doc_text      = doc_text,
        scene         = scene,
        level         = level,
        check_items   = db_items,
        skill_context = skill_context,
        api_key       = gemini_api_key,
        model_override= gemini_model,
    )

    if "error" in gemini_result:
        return {
            "check_id":  check_id,
            "error":     gemini_result["error"],
            "timestamp": timestamp,
        }

    # 5. TC4 評分
    tc4 = calculate_tc4_score(gemini_result.get("items", []), db_items)

    # 6. 統計
    items = gemini_result.get("items", [])
    stats = {r: sum(1 for i in items if i.get("result") == r)
             for r in ["已完成","部分完成","未完成","不適用","需補件","需確認"]}

    return {
        "check_id":           check_id,
        "timestamp":          timestamp,
        "filename":           filename,
        "member_name":        member_name,
        "scene":              gemini_result.get("scene_confirmed", scene),
        "level":              gemini_result.get("level_confirmed", level),
        "scene_note":         gemini_result.get("scene_note", ""),
        "auto_confidence":    confidence,
        "score":              tc4["score"],
        "passed":             tc4["passed"],
        "stats":              stats,
        "items":              items,
        "overall_comment":    gemini_result.get("overall_comment", ""),
        "total_db_items":     len(db_items),
        "has_context_input":  bool(context_input.strip()),
    }
