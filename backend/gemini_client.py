# -*- coding: utf-8 -*-
"""
gemini_client.py — AI API 呼叫
支援兩種模式：
  1. Gemini（google-genai SDK）
  2. 第三方 OpenAI-compatible API（如 runapi.sbs）
"""

import json
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL


# ─── Gemini client 快取 ───────────────────────────────────────────────

_clients: dict = {}

def _get_gemini_client(api_key: str):
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


# ─── Prompt 建構 ─────────────────────────────────────────────────────

def build_prompt(
    doc_text: str,
    scene: str,
    level: str,
    check_items: list,
    skill_context: str,
) -> str:
    items_text = ""
    for it in check_items:
        items_text += (
            f"\n【序號{it['seq']}】{it['item']}\n"
            f"  應達到的標準：{it['standard']}\n"
            f"  需要的資訊：{it['needed_info']}\n"
            f"  常見錯誤：{it['common_errors']}\n"
            f"  重要程度：{it['mention_count']}\n"
        )

    return f"""你是 William 老師的生命動能協會 AI 助手。
你的任務是幫助 member 完成規劃文件，對照 William 的要求資料庫，找出落差並生成補件清單。

═══════════════════════════════════════
【William 的評估框架與知識庫（精簡版）】
═══════════════════════════════════════
{skill_context}

═══════════════════════════════════════
【當前使用場景與層級】
═══════════════════════════════════════
使用場景：{scene}
生命動能層級：{level} 層次
（A層次：初級成長／自我認知／角色扮演／家庭管理；B層次：有活動籌辦能力／有財務管理；C層次：具體執行）

═══════════════════════════════════════
【待檢核的檢核項目（共 {len(check_items)} 項）】
═══════════════════════════════════════
{items_text}

═══════════════════════════════════════
【Member 上傳的規劃文件】
═══════════════════════════════════════
{doc_text[:100000]}

═══════════════════════════════════════
【你的任務】
═══════════════════════════════════════
請對照上方 William 要求的檢核項目，分析 member 上傳的規劃文件，找出落差與缺口。

1. 場景確認：場景或確認或更正為 William 要求的場景、生命動能層級
2. 逐項分析：判斷各檢核項目的達標狀況（包括：
   - 已達標：已達到標準
   - 部分達標：有提到但不夠完整
   - 未達標：沒有提到或完全不符
   - 不適用：這個層次或場景不需要
   - 有問題：找到常見錯誤，並提醒 member
   - 缺失資訊：有缺口，需要補件，並提醒 member
3. 整體評估：整體的評估與建議，讓 member 知道最大問題
4. 補件清單：整理【補件清單】和【部分達標】的檢核項目，列出 William 要求的補件清單，並提醒 member
   （參考規則：若確認缺失，使用「你需要補充：XXX 內容」格式，不要 member 說「你可以補充：」，不要說「是否需要」、「或者」、「可以考慮」等不確定語氣）

輸出純 JSON 格式（不要加 markdown code block）：
{{
  "scene_confirmed": "場景名稱",
  "level_confirmed": "A或B或C",
  "scene_note": "說明場景有沒有需要調整，並說明原因",
  "items": [
    {{
      "seq": 序號,
      "item": "檢核項目名稱",
      "result": "已達標|部分達標|未達標|不適用|有問題|缺失資訊",
      "evidence": "引用規劃文件片段（標題、規劃文件摘錄「規劃文件摘錄內容」）",
      "missing": ["缺少的補件清單1", "缺少的補件清單2"],
      "suggestion": "William 要求的補件清單（result 若為缺失或部分達標或有問題則必填）",
      "common_error_found": "若找到常見錯誤，說明在哪裡"
    }}
  ],
  "overall_comment": "整體評語（William 會對 member 說的話，直接指出最大問題）"
}}
"""


# ─── 第三方 OpenAI-compatible API ────────────────────────────────────

def _call_openai_compatible(
    prompt: str,
    api_key: str,
    model: str,
    base_url: str = "https://api.runapi.sbs/v1",
) -> dict:
    """呼叫 OpenAI-compatible 第三方 API，回傳解析後 dict。"""
    try:
        from openai import OpenAI
    except ImportError:
        return {"error": "缺少 openai 套件，請聯絡管理員"}

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=8192,
            response_format={"type": "json_object"},
        )
        raw_text = resp.choices[0].message.content
        raw_text = re.sub(r'```json\n?', '', raw_text)
        raw_text = re.sub(r'```\n?', '', raw_text)
        return json.loads(raw_text)
    except Exception as e:
        return {"error": str(e)}


# ─── 主入口 ──────────────────────────────────────────────────────────

def analyze(
    doc_text: str,
    scene: str,
    level: str,
    check_items: list,
    skill_context: str,
    api_key: str = None,          # Gemini key（per-request 覆蓋）
    model_override: str = None,   # Gemini model（per-request 覆蓋）
    # 第三方 API 參數
    provider: str = "gemini",     # "gemini" 或 "openai_compat"
    third_party_key: str = None,
    third_party_model: str = None,
    third_party_base_url: str = "https://api.runapi.sbs/v1",
) -> dict:
    prompt = build_prompt(doc_text, scene, level, check_items, skill_context)

    if provider == "openai_compat":
        if not third_party_key:
            return {"error": "請填入第三方 API Key"}
        if not third_party_model:
            return {"error": "請填入第三方模型名稱"}
        return _call_openai_compatible(
            prompt=prompt,
            api_key=third_party_key,
            model=third_party_model,
            base_url=third_party_base_url,
        )

    # 預設：Gemini
    effective_key   = api_key or GEMINI_API_KEY
    effective_model = model_override or GEMINI_MODEL

    if not effective_key:
        return {"error": "GEMINI_API_KEY 未設定，請在頁面的「AI 設定」欄填入你的 API Key"}

    try:
        client = _get_gemini_client(effective_key)
        resp = client.models.generate_content(
            model=effective_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=8192,
            ),
        )
        raw_text = resp.text
        raw_text = re.sub(r'```json\n?', '', raw_text)
        raw_text = re.sub(r'```\n?', '', raw_text)
        return json.loads(raw_text)
    except Exception as e:
        return {"error": str(e)}
