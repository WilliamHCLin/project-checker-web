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


# --- Gemini client 快取 ---

_clients: dict = {}

def _get_gemini_client(api_key: str):
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


# --- Prompt 建構 ---

def build_prompt(doc_text, scene, level, check_items, skill_context):
    # 重要性符號對應
    IMP_SYMBOL = {"紅": "🔴【底線】", "黃": "🟡【核心】", "白": "⚪【一般】"}

    items_text = ""
    for it in check_items:
        imp = it.get("importance", "白")
        phase = it.get("phase", "")
        domain = it.get("domain", "")
        imp_label = IMP_SYMBOL.get(imp, "⚪")
        items_text += (
            f"\n【序號{it['seq']}】{imp_label} {it['item']}\n"
            f"  能力領域：{domain}　階段：{phase}\n"
            f"  應達到的標準：{it['standard']}\n"
            f"  需要的資訊：{it['needed_info']}\n"
            f"  常見錯誤：{it['common_errors']}\n"
        )

    importance_legend = """
【重要性說明】
🔴【底線】= 安全底線，絕對不能缺少，不做等於沒有做這件事
🟡【核心】= A級核心項目，鍾老師高度關注，缺少會影響整體品質
⚪【一般】= 一般品質項目，有做更好但缺少可接受
"""

    return f"""你是 William 老師（鍾老師）的生命動能協會 AI 助手。
你的任務是幫助 member 完成規劃文件，對照 William 的要求資料庫，找出落差並生成補件清單。

{importance_legend}

{skill_context}

【當前使用場景與層級】
能力領域：{scene}
生命動能層級：{level} 層次
（A層次：初級成長／自我認知；B層次：有活動籌辦能力；C層次：具體執行）

【待檢核的檢核項目（共 {len(check_items)} 項，依重要性排列）】
{items_text}

【Member 上傳的規劃文件】
{doc_text[:100000]}

【你的任務】
請對照上方檢核項目，分析文件，找出落差與缺口。

分析重點優先序：
1. 🔴底線項目若未達標，必須第一時間指出，語氣要直接
2. 🟡核心項目若有落差，要具體說明缺什麼
3. ⚪一般項目若有缺，簡要提示即可

補件建議格式：使用「你需要補充：XXX」，不要用「可以考慮」「建議」等模糊語氣。

請直接輸出純 JSON，不要輸出任何思考過程或說明文字，格式如下：
{{
  "scene_confirmed": "場景名稱",
  "level_confirmed": "A或B或C",
  "scene_note": "場景說明",
  "critical_missing": ["🔴底線未達標項目簡述1", "🔴底線未達標項目簡述2"],
  "items": [
    {{
      "seq": 序號,
      "item": "檢核項目名稱",
      "importance": "紅或黃或白",
      "result": "已達標|部分達標|未達標|不適用|有問題|缺失資訊",
      "evidence": "引用文件片段",
      "missing": ["缺少1", "缺少2"],
      "suggestion": "補件建議",
      "common_error_found": "常見錯誤說明"
    }}
  ],
  "overall_comment": "整體評語"
}}
"""


# --- JSON 解析（容錯） ---

def _parse_json(raw: str) -> dict:
    """
    從原始文字中提取最後一個完整 JSON 物件。
    - 移除 markdown code fence
    - 跳過模型思考過程文字（找最後一個換行後的 { 開始）
    - 若截斷，嘗試在最後完整 item 處補尾
    """
    if not raw or not raw.strip():
        return {"error": "AI 回傳空白內容，請換模型或稍後再試"}

    # 移除 markdown code block
    text = re.sub(r'```json\s*', '', raw)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()

    # 找最後一個以換行開頭的 {（跳過思考過程）
    start = text.rfind('\n{')
    if start == -1:
        start = text.find('{')
    else:
        start += 1  # 跳過換行

    if start == -1:
        return {"error": f"AI 回傳非 JSON 格式：{text[:200]}"}

    end = text.rfind('}')
    if end == -1 or end < start:
        return {"error": f"AI 回傳非 JSON 格式（找不到結尾）：{text[:200]}"}

    candidate = text[start:end+1]

    # 第一次嘗試：直接解析
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 第二次嘗試：JSON 被截斷 → 找最後完整 item（}] 結尾）補收尾
    last_complete = candidate.rfind('}]')
    if last_complete != -1:
        truncated = candidate[:last_complete+2]
        repaired = truncated + ', "overall_comment": "（AI 輸出被截斷，以下為部分結果）"}'
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # 最後回退
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}。原始內容前200字：{candidate[:200]}"}


# --- 第三方 OpenAI-compatible API ---

def _call_openai_compatible(prompt, api_key, model, base_url="https://api.runapi.sbs/v1"):
    try:
        from openai import OpenAI
    except ImportError:
        return {"error": "缺少 openai 套件，請聯絡管理員"}

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16000,
        )
        raw_text = resp.choices[0].message.content or ""
        return _parse_json(raw_text)
    except Exception as e:
        return {"error": str(e)}


# --- 主入口 ---

def analyze(
    doc_text,
    scene,
    level,
    check_items,
    skill_context,
    api_key=None,
    model_override=None,
    provider="gemini",
    third_party_key=None,
    third_party_model=None,
    third_party_base_url="https://api.runapi.sbs/v1",
):
    prompt = build_prompt(doc_text, scene, level, check_items, skill_context)

    if provider == "openai_compat":
        if not third_party_key:
            return {"error": "請填入第三方 API Key"}
        if not third_party_model:
            return {"error": "