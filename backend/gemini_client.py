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

{skill_context}

【當前使用場景與層級】
使用場景：{scene}
生命動能層級：{level} 層次
（A層次：初級成長／自我認知；B層次：有活動籌辦能力；C層次：具體執行）

【待檢核的檢核項目（共 {len(check_items)} 項）】
{items_text}

【Member 上傳的規劃文件】
{doc_text[:100000]}

【你的任務】
請對照上方檢核項目，分析文件，找出落差與缺口。

1. 場景確認：確認或更正場景與層級
2. 逐項分析：判斷各項達標狀況
3. 整體評估：指出最大問題
4. 補件清單：使用「你需要補充：XXX」格式，不要說「可以考慮」等模糊語氣

請輸出純 JSON（不要加任何 markdown code block 或 ```json 標記），格式如下：
{{
  "scene_confirmed": "場景名稱",
  "level_confirmed": "A或B或C",
  "scene_note": "場景說明",
  "items": [
    {{
      "seq": 序號,
      "item": "檢核項目名稱",
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


def _parse_json(raw: str) -> dict:
    """從原始文字中提取 JSON，容錯處理 markdown code fence。"""
    if not raw or not raw.strip():
        return {"error": "AI 回傳空白內容，請換模型或稍後再試"}
    # 移除 markdown code block
    text = re.sub(r'```json\s*', '', raw)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()
    # 找第一個 { 到最後一個 }
    start = text.find('{')
    end   = text.rfind('}')
    if start == -1 or end == -1:
        return {"error": f"AI 回傳非 JSON 格式：{text[:200]}"}
    try:
        return json.loads(text[start:end+1])
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}。原始內容前200字：{text[:200]}"}


# ─── 第三方 OpenAI-compatible API ────────────────────────────────────

def _call_openai_compatible(
    prompt: str,
    api_key: str,
    model: str,
    base_url: str = "https://api.runapi.sbs/v1",
) -> dict:
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
        )
        raw_text = resp.choices[0].message.content or ""
        return _parse_json(raw_text)
    except Exception as e:
        return {"error": str(e)}


# ─── 主入口 ──────────────────────────────────────────────────────────

def analyze(
    doc_text: str,
    scene: str,
    level: str,
    check_items: list,
    skill_context: str,
    api_key: str = None,
    model_override: str = None,
    provider: str = "gemini",
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

    # Gemini
    effective_key   = api_key or GEMINI_API_KEY
    effective_model = model_override or GEMINI_MODEL

    if not effective_key:
        return {"error": "GEMINI_API_KEY 未設定，請填入你的 API Key"}

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
        return _parse_json(resp.text or "")
    except Exception as e:
        return {"error": str(e)}
