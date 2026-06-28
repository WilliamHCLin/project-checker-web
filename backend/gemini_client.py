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

# 資料庫裡所有合法場景代碼
ALL_SCENES = [
    "A. 班級日常管理",
    "A. 班級日常經營",
    "D1. 對外藝文交流：前期籌備",
    "D2. 對外藝文交流：交通與後勤",
    "D3. 對外藝文交流：彩排與演出",
    "D4. 對外藝文交流：公關與接待",
    "D5. 對外藝文交流：成果與後續",
    "D6. 對外藝文交流：整復服務",
    "E. 招生與傳承",
    "F. 人力資源管理",
    "F. 外部邀約應對",
    "F. 競賽管理",
    "G. 專案管理基本功",
    "H. 人員/紀律",
    "H. 個人升學/書審",
    "I. 師資與傳承管理",
    "I. 教學品質管理",
    "J. 設備物資管理",
    "K. 班級經營決策框架",
    "L. 做事標準框架",
    "M. 會議管理與追蹤",
]


# --- Gemini client 快取 ---

_clients: dict = {}

def _get_gemini_client(api_key: str):
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


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

    text = re.sub(r'```json\s*', '', raw)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()

    start = text.rfind('\n{')
    if start == -1:
        start = text.find('{')
    else:
        start += 1

    if start == -1:
        return {"error": f"AI 回傳非 JSON 格式：{text[:200]}"}

    end = text.rfind('}')
    if end == -1 or end < start:
        return {"error": f"AI 回傳非 JSON 格式（找不到結尾）：{text[:200]}"}

    candidate = text[start:end+1]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    last_complete = candidate.rfind('}]')
    if last_complete != -1:
        truncated = candidate[:last_complete+2]
        repaired = truncated + ', "overall_comment": "（AI 輸出被截斷，以下為部分結果）"}'
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}。原始內容前200字：{candidate[:200]}"}


# --- 第一輪：場景辨識 ---

def detect_scenes_ai(
    doc_text: str,
    api_key: str = None,
    model_override: str = None,
    provider: str = "gemini",
    third_party_key: str = None,
    third_party_model: str = None,
    third_party_base_url: str = "https://api.runapi.sbs/v1",
) -> list[str]:
    """
    讓 AI 讀完文件，從 ALL_SCENES 中選出最相關的 3 個場景代碼。
    回傳 list[str]，若失敗回傳 ["G. 專案管理基本功"]。
    """
    scenes_list = "\n".join(f"- {s}" for s in ALL_SCENES)
    prompt = f"""你是生命動能協會的 AI 助手。請仔細閱讀以下文件，判斷這份文件最適合的使用場景。

【可選場景清單】
{scenes_list}

【文件內容】
{doc_text[:60000]}

【你的任務】
從上方場景清單中，選出最符合這份文件內容的 3 個場景代碼（必須完全符合清單中的文字）。
排序原則：最相關的排第一，依相關程度遞減。
G. 專案管理基本功 是通用底層，如果文件是具體的活動/比賽/班級規劃，請優先選更具體的場景，只有在真的找不到其他合適場景時才選 G。

請直接輸出純 JSON，格式如下：
{{
  "scenes": ["場景1", "場景2", "場景3"],
  "reason": "簡短說明選擇依據"
}}
"""

    raw = None

    if provider == "openai_compat" and third_party_key and third_party_model:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=third_party_key, base_url=third_party_base_url)
            resp = client.chat.completions.create(
                model=third_party_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
            )
            raw = resp.choices[0].message.content or ""
        except Exception:
            pass
    else:
        effective_key   = api_key or GEMINI_API_KEY
        effective_model = model_override or GEMINI_MODEL
        if effective_key:
            try:
                client = _get_gemini_client(effective_key)
                resp = client.models.generate_content(
                    model=effective_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=512,
                    ),
                )
                raw = resp.text or ""
            except Exception:
                pass

    if raw:
        result = _parse_json(raw)
        scenes = result.get("scenes", [])
        # 過濾非法場景代碼
        valid = [s for s in scenes if s in ALL_SCENES]
        if valid:
            return valid[:3]

    return ["G. 專案管理基本功"]


# --- 第二輪：完整分析 Prompt ---

def build_prompt(doc_text, scenes, level, check_items, skill_context):
    scenes_str = "、".join(scenes)
    items_text = ""
    for it in check_items:
        items_text += (
            f"\n【序號{it['seq']}】[場景:{it['scene']}] {it['item']}\n"
            f"  應達到的標準：{it['standard']}\n"
            f"  需要的資訊：{it['needed_info']}\n"
            f"  常見錯誤：{it['common_errors']}\n"
            f"  重要程度：{it['mention_count']}\n"
        )

    return f"""你是 William 老師的生命動能協會 AI 助手。
你的任務是幫助 member 完成規劃文件，對照 William 的要求資料庫，找出落差並生成補件清單。

{skill_context}

【本次文件涵蓋的使用場景（共 {len(scenes)} 個）】
{scenes_str}
生命動能層級：{level} 層次
（A層次：初級成長／自我認知；B層次：有活動籌辦能力；C層次：具體執行）

【待檢核的檢核項目（共 {len(check_items)} 項，來自以上場景）】
{items_text}

【Member 上傳的規劃文件】
{doc_text[:100000]}

【分析方法 — 請嚴格依照以下步驟進行】

第一步：完整理解文件
- 先從頭到尾讀完整份文件，包含所有表格、欄位、備註
- 理解這份文件的整體目的、活動性質、執行脈絡
- 文件可能以表格形式呈現，表格中的每一格都是正式資訊，請完整納入分析

第二步：推論式對照
- 對照每個檢核項目時，不要只看有沒有明確的「標題關鍵字」
- 若文件中的內容（包含表格欄位、流程說明、備註）可以合理推論出某項目已被覆蓋，請標記為已達標，並在 evidence 中引用該文件片段說明推論依據
- 不適用於本份文件的項目，請標記為「不適用」並簡短說明

第三步：謹慎判定缺失
- 只有在整份文件（含表格）完全找不到相關資訊，且無法合理推論時，才標記為「未達標」或「缺失資訊」
- 不要因為沒有明確的標題或關鍵字就判定缺失

第四步：給出具體可執行的建議
- 對於真正缺失的項目，使用「你需要補充：XXX」格式
- 對於部分達標的項目，指出具體還缺什麼
- 不要說「可以考慮」等模糊語氣

請直接輸出純 JSON，不要輸出任何思考過程或說明文字，格式如下：
{{
  "scene_confirmed": "主場景名稱",
  "scenes_used": ["場景1", "場景2", "場景3"],
  "level_confirmed": "A或B或C",
  "scene_note": "場景說明",
  "items": [
    {{
      "seq": 序號,
      "item": "檢核項目名稱",
      "result": "已達標|部分達標|未達標|不適用|有問題|缺失資訊",
      "evidence": "引用文件片段或推論說明",
      "missing": ["缺少1", "缺少2"],
      "suggestion": "補件建議",
      "common_error_found": "常見錯誤說明"
    }}
  ],
  "overall_comment": "整體評語"
}}
"""


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
    scenes,
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
    prompt = build_prompt(doc_text, scenes, level, check_items, skill_context)

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
