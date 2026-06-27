# -*- coding: utf-8 -*-
"""
gemini_client.py — Gemini API 封裝（使用 google-genai SDK）
"""

import json
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL


def _get_client(api_key: str = None):
    """若有傳入 api_key 則使用該 key，否則用伺服器預設。"""
    return genai.Client(api_key=api_key or GEMINI_API_KEY)


def build_prompt(
    doc_text: str,
    scene: str,
    level: str,
    check_items: list[dict],
    skill_context: str,
) -> str:
    """組合送給 Gemini 的完整 prompt。"""
    items_text = ""
    for it in check_items:
        items_text += (
            f"\n【序{it['seq']}】{it['item']}\n"
            f"  做好的標準：{it['standard']}\n"
            f"  需要的資訊：{it['needed_info']}\n"
            f"  常見錯誤：{it['common_errors']}\n"
            f"  被提及次數：{it['mention_count']}\n"
        )

    return f"""你是 William 老師的專案檢核 AI 助理。
你的任務是分析 member 上傳的規劃文件，對照 William 的檢核資料庫，找出落差並提供建議。

【學員說明若有提供，請優先理解其背景、需求與擔憂，並在判斷時納入考量】

═══════════════════════════════════════
【William 的決策框架與原則（節選）】
═══════════════════════════════════════
{skill_context}

═══════════════════════════════════════
【本次使用場景與層級】
═══════════════════════════════════════
使用場景：{scene}
專案層級：{level} 級
（A級：對外/首次/比賽/安全風險；B級：內部但人數多/有家長；C級：小型例行）

═══════════════════════════════════════
【適用檢核項目清單（共 {len(check_items)} 項）】
═══════════════════════════════════════
{items_text}

═══════════════════════════════════════
【Member 提供的內容】
═══════════════════════════════════════
{doc_text[:10000]}

═══════════════════════════════════════
【你的任務】
═══════════════════════════════════════
請逐項對照檢核資料庫，完成以下分析：

1. 場景確認：確認或修正自動辨識的場景與層級
2. 逐項判斷：每個檢核項目的結果（只能選以下六種）
   - 已完成：明確滿足所有條件
   - 部分完成：有做但缺關鍵資訊
   - 未完成：完全沒有或嚴重不足
   - 不適用：本案不涉及此項目
   - 需補件：缺具體資料，可由 member 補填
   - 需確認：資料矛盾或模糊
3. 常見錯誤偵測：依「常見錯誤」欄的說明，特別標記發現的問題
4. 具體建議：對「需補件」和「部分完成」的項目，給出 William 風格的具體補件建議
   （風格：直接、具體，用「你需要補：XXX 格式」，不說「請考慮」「可能需要」等模糊詞）

請嚴格以 JSON 格式輸出（不要加 markdown code block）：
{{
  "scene_confirmed": "場景名稱",
  "level_confirmed": "A或B或C",
  "scene_note": "若場景有調整說明原因，否則空字串",
  "items": [
    {{
      "seq": 數字,
      "item": "檢核項目名稱",
      "result": "已完成|部分完成|未完成|不適用|需補件|需確認",
      "evidence": "引用文件原文（無則填「文件未提及」）",
      "missing": ["需補充的具體資訊1", "需補充的具體資訊2"],
      "suggestion": "William 風格的具體建議（result 為需補件或部分完成時必填）",
      "common_error_found": "若發現常見錯誤描述，否則空字串"
    }}
  ],
  "overall_comment": "整體評語（William 會對 member 說的話，直接指出最大問題）"
}}
"""


def analyze(
    doc_text: str,
    scene: str,
    level: str,
    check_items: list[dict],
    skill_context: str,
    api_key: str = None,
    model_override: str = None,
) -> dict:
    """呼叫 Gemini，回傳解析後的 dict。"""
    effective_key   = api_key or GEMINI_API_KEY
    effective_model = model_override or GEMINI_MODEL

    if not effective_key:
        return {"error": "GEMINI_API_KEY 未設定，請在頁面的「AI 設定」欄填入你的 API Key"}

    try:
        client = _get_client(effective_key)
        prompt = build_prompt(doc_text, scene, level, check_items, skill_context)

        resp = client.models.generate_content(
            model=effective_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        raw = resp.text.strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            cleaned = re.sub(r"```(?:json)?```", "", raw).strip()
            return json.loads(cleaned)

    except Exception as e:
        return {"error": str(e)}
