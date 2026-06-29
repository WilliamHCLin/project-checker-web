# -*- coding: utf-8 -*-
"""
db_loader.py — 讀取 Excel 檢核資料庫 + 三份 Skill 文件
啟動時載入一次，快取在記憶體中。
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import openpyxl

from config import (
    DB_PATH, SKILL_PATHS, LEVEL_ORDER,
    SCENE_KEYWORDS, LEVEL_KEYWORDS,
    SKILL_INJECT_CHARS, MENTION_HIGH_THRESHOLD,
    IMPORTANCE_ORDER,
)


# ─── Excel 資料庫 ────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_db() -> list[dict]:
    """讀取「檢核資料庫」sheet，回傳所有項目清單。"""
    wb = openpyxl.load_workbook(DB_PATH, read_only=True, data_only=True)
    ws = wb["檢核資料庫"]
    items = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        items.append({
            "seq":           row[0],
            "scene":         row[1] or "",
            "scope":         row[2] or "",
            "item":          row[3] or "",
            "standard":      row[4] or "",
            "needed_info":   row[5] or "",
            "level":         row[6] or "C",
            "respondent":    row[7] or "",
            "common_errors": row[8] or "",
            "mention_count": int(row[9]) if row[9] else 0,
            "notes":         row[10] or "",
            "group":         row[11] or "",
            "domain":        row[12] or "",   # 能力領域 A-J
            "phase":         row[13] or "",   # 階段：前期/執行/收尾/通用
            "importance":    row[14] or "白", # 重要性：紅/黃/白
        })
    wb.close()
    return items


def filter_items(scene: str, level: str, phase: str = "") -> list[dict]:
    """
    依能力領域（domain）和層級篩選適用的檢核項目。
    G. 專案管理基本功 永遠包含（通用底層）。
    層級：A 包含 A+B+C，B 包含 B+C，C 只含 C。
    phase 可選：前期/執行/收尾/通用，不填則全包含。
    排序：重要性（紅>黃>白）→ 被提及次數降序。
    """
    all_items = load_db()
    min_level = LEVEL_ORDER.get(level, 1)

    result = []
    for item in all_items:
        domain       = item.get("domain", "")
        item_phase   = item.get("phase", "")
        item_level_v = LEVEL_ORDER.get(item["level"], 1)

        # 能力領域匹配：以場景碼首字母比對 domain 首字母，或 domain 含 G
        domain_letter = domain[0] if domain else ""
        scene_letter  = scene[0] if scene else ""
        domain_match = (
            domain_letter == scene_letter
            or domain_letter == "G"
            or (scene_letter and scene.startswith(domain_letter))
        )

        level_match = item_level_v >= min_level

        # 階段篩選（有指定才篩，通用永遠包含）
        phase_match = (
            not phase
            or item_phase == phase
            or item_phase == "通用"
        )

        if domain_match and level_match and phase_match:
            result.append(item)

    # 排序：重要性（紅=3 > 黃=2 > 白=1）→ 被提及次數降序
    result.sort(
        key=lambda x: (
            IMPORTANCE_ORDER.get(x.get("importance", "白"), 1),
            x["mention_count"]
        ),
        reverse=True
    )
    return result


def get_mention_weight(mention_count: int) -> float:
    from config import (HIGH_MENTION_WEIGHT, MID_MENTION_WEIGHT,
                        LOW_MENTION_WEIGHT, ZERO_MENTION_WEIGHT)
    if mention_count >= MENTION_HIGH_THRESHOLD:
        return HIGH_MENTION_WEIGHT
    elif mention_count >= 4:
        return MID_MENTION_WEIGHT
    elif mention_count >= 1:
        return LOW_MENTION_WEIGHT
    else:
        return ZERO_MENTION_WEIGHT


# ─── Skill 文件 ──────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def load_skills() -> dict[str, str]:
    """讀取三份 skill md 全文，快取在記憶體。"""
    result = {}
    for name, path in SKILL_PATHS.items():
        if Path(path).exists():
            result[name] = Path(path).read_text(encoding="utf-8")
        else:
            result[name] = ""
    return result


def extract_skill_context(scene: str, level: str) -> str:
    """
    依場景和層級，從三份 Skill 中萃取最相關的段落，
    回傳整合後的文字（注入 Gemini prompt 用）。
    """
    skills = load_skills()
    parts = []

    # ── 工作決策框架：取 T1 品質危機 + T5 專案執行 + T9 交付物審查 ──
    work_md = skills.get("工作框架", "")
    work_sections = _extract_sections(
        work_md,
        targets=["T1 · 品質危機", "T5 · 專案執行", "T9 · 交付物審查"],
        max_chars=SKILL_INJECT_CHARS["工作框架"]
    )
    if work_sections:
        parts.append("## William 工作決策框架（節選）\n" + work_sections)

    # ── 生命動能框架：取 TLQ8 活動執行 + TLQ9 新活動規劃前 ──
    assoc_md = skills.get("協會框架", "")
    # 比賽場景加入 TLQ4 策略
    extra_tlq = ["TLQ4 · 協會策略"] if scene.startswith("C.") else []
    assoc_sections = _extract_sections(
        assoc_md,
        targets=["TLQ8 · 活動執行", "TLQ9 · 新活動規劃", "TLQ1 · 臨時任務"] + extra_tlq,
        max_chars=SKILL_INJECT_CHARS["協會框架"]
    )
    if assoc_sections:
        parts.append("## 生命動能協會管理框架（節選）\n" + assoc_sections)

    # ── 情境決策原則庫：取「入場協議」+ 「專案管理」前段 ──
    principle_md = skills.get("原則庫", "")
    principle_sections = _extract_sections(
        principle_md,
        targets=["入場協議", "專案管理（25 條）", "風險管理與危機處理"],
        max_chars=SKILL_INJECT_CHARS["原則庫"]
    )
    if principle_sections:
        parts.append("## William 情境決策原則（節選）\n" + principle_sections)

    return "\n\n".join(parts)


def _extract_sections(md_text: str, targets: list[str], max_chars: int) -> str:
    """
    從 markdown 全文中找出包含 targets 關鍵字的 section（## / ### 開頭），
    合併後截斷至 max_chars。
    """
    if not md_text:
        return ""

    # 把文件切成 section
    sections = re.split(r'\n(?=#{1,3} )', md_text)
    collected = []
    total = 0

    for target in targets:
        for sec in sections:
            if target in sec and sec not in collected:
                collected.append(sec)
                total += len(sec)
                if total >= max_chars:
                    break
        if total >= max_chars:
            break

    combined = "\n\n".join(collected)
    return combined[:max_chars] if len(combined) > max_chars else combined


# ─── 場景自動辨識 ─────────────────────────────────────────────────────

def detect_scene(text: str) -> dict:
    """
    依文件全文關鍵字比對，回傳場景辨識結果。
    """
    text_lower = text.lower()
    scores: dict[str, int] = {}
    matched_kw: dict[str, list] = {}

    for scene_code, keywords in SCENE_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text_lower or kw in text]
        scores[scene_code]    = len(hits)
        matched_kw[scene_code] = hits

    best_scene = max(scores, key=scores.get) if scores else "G. 專案管理基本功"
    best_score = scores.get(best_scene, 0)
    confidence = min(int(best_score / max(len(SCENE_KEYWORDS.get(best_scene, [1])), 1) * 100), 100)

    # 層級判斷
    level_scores: dict[str, int] = {}
    for lv, kws in LEVEL_KEYWORDS.items():
        level_scores[lv] = sum(1 for kw in kws if kw in text)
    best_level = max(level_scores, key=level_scores.get) if level_scores else "B"
    if level_scores.get("A", 0) == 0 and level_scores.get("B", 0) == 0:
        best_level = "C"

    return {
        "scene":       best_scene,
        "level":       best_level,
        "confidence":  confidence,
        "matched_kws": matched_kw.get(best_scene, []),
    }
