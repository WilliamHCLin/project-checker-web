# -*- coding: utf-8 -*-
"""
gemini_client.py - AI API
"""

import json
import re

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL


_clients: dict = {}

def _get_gemini_client(api_key: str):
    if api_key not in _clients:
        _clients[api_key] = genai.Client(api_key=api_key)
    return _clients[api_key]


def _parse_json(raw: str) -> dict:
    if not raw or not raw.strip():
        return {"error": "AI 回傳空白內容"}

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
        repaired = truncated + ', "overall_comment": "(AI 輸出被截斷)"}'
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}。原始內容前200字：{candidate[:200]}"}


def _call_openai_compatible(prompt, api_key, model, base_url="https://api.runapi.sbs/v1"):
    try:
        from openai import OpenAI
    except ImportError:
        return {"error": "缺少 openai 套件"}

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


SCENE_LIST_TEXT = (
    "A. 班級日常管理\n"
    "B. 競賽執行\n"
    "C. 競賽管理\n"
    "D. 對外藝文交流\n"
    "E. 招生與傳承\n"
    "F. 外部邀約應對\n"
    "G. 專案管理基本功\n"
    "H. 個人升學/書審\n"
    "I. 師資與傳承管理\n"
    "J. 設備物資管理"
)


def detect_scenes_ai(
    doc_text: str,
    api_key=None,
    model_override=None,
    provider="gemini",
    third_party_key=None,
    third_party_model=None,
    third_party_base_url="https://api.runapi.sbs/v1",
) -> list:
    from config import SCENE_KEYWORDS
    from db_loader import detect_scene

    prompt = (
        "你是生命動能協會 AI 助手。請分析以下文件，從場景列表中選出最相關的 1-3 個場景。\n\n"
        "【可選場景】\n" + SCENE_LIST_TEXT + "\n\n"
        "【文件內容（前3000字）】\n" + doc_text[:3000] + "\n\n"
        "請直接輸出純 JSON：{\"scenes\": [\"A. 班級日常管理\"]}"
    )

    try:
        if provider == "openai_compat" and third_party_key and third_party_model:
            result = _call_openai_compatible(prompt, third_party_key, third_party_model, third_party_base_url)
        else:
            effective_key = api_key or GEMINI_API_KEY
            effective_model = model_override or GEMINI_MODEL
            if not effective_key:
                raise ValueError("no api key")
            client = _get_gemini_client(effective_key)
            resp = client.models.generate_content(
                model=effective_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=256,
                ),
            )
            result = _parse_json(resp.text or "")

        scenes = result.get("scenes", [])
        valid = set(SCENE_KEYWORDS.keys())
        scenes = [s for s in scenes if s in valid]
        if scenes:
            return scenes
    except Exception:
        pass

    fallback = detect_scene(doc_text)
    return [fallback["scene"]]


def build_prompt(doc_text, scene, level, check_items, skill_context):
    IMP_SYMBOL = {"red": "[底線]", "yellow": "[核心]", "white": "[一般]",
                  "紅": "[底線]", "黃": "[核心]", "白": "[一般]"}

    items_text = ""
    for it in check_items:
        imp = it.get("importance", "白")
        phase = it.get("phase", "")
        domain = it.get("domain", "")
        imp_label = IMP_SYMBOL.get(imp, "[一般]")
        items_text += (
            "\n[序號" + str(it['seq']) + "] " + imp_label + " " + str(it['item']) + "\n"
            "  能力領域：" + str(domain) + "　階段：" + str(phase) + "\n"
            "  應達到的標準：" + str(it['standard']) + "\n"
            "  需要的資訊：" + str(it['needed_info']) + "\n"
            "  常見錯誤：" + str(it['common_errors']) + "\n"
        )

    importance_legend = (
        "【重要性說明】\n"
        "[底線]= 安全底線，絕對不能缺少\n"
        "[核心]= A級核心項目，鍾老師高度關注\n"
        "[一般]= 一般品質項目\n"
    )

    json_schema = (
        '{\n'
        '  "scene_confirmed": "場景名稱",\n'
        '  "level_confirmed": "A或B或C",\n'
        '  "scene_note": "場景說明",\n'
        '  "critical_missing": ["底線未達標項目1"],\n'
        '  "items": [\n'
        '    {\n'
        '      "seq": 序號,\n'
        '      "item": "檢核項目名稱",\n'
        '      "importance": "紅或黃或白",\n'
        '      "result": "已達標|部分達標|未達標|不適用|有問題|缺失資訊",\n'
        '      "evidence": "引用文件片段",\n'
        '      "missing": ["缺少1"],\n'
        '      "suggestion": "補件建議",\n'
        '      "common_error_found": "常見錯誤說明"\n'
        '    }\n'
        '  ],\n'
        '  "overall_comment": "整體評語"\n'
        '}'
    )

    return (
        "你是 William 老師（鍾老師）的生命動能協會 AI 助手。\n"
        "任務：對照 William 的要求資料庫，找出落差並生成補件清單。\n\n"
        + importance_legend + "\n"
        + skill_context + "\n\n"
        "【當前使用場景與層級】\n"
        "能力領域：" + str(scene) + "\n"
        "生命動能層級：" + str(level) + " 層次\n\n"
        "【待檢核的檢核項目（共 " + str(len(check_items)) + " 項，依重要性排列）】\n"
        + items_text + "\n\n"
        "【Member 上傳的規劃文件】\n"
        + doc_text[:100000] + "\n\n"
        "【你的任務】\n"
        "請對照上方檢核項目，分析文件，找出落差與缺口。\n"
        "底線項目若未達標，必須第一時間指出，語氣要直接。\n"
        "補件建議格式：使用「你需要補充：XXX」。\n\n"
        "請直接輸出純 JSON，格式如下：\n"
        + json_schema
    )


def analyze(
    doc_text,
    scene=None,
    level="B",
    check_items=None,
    skill_context="",
    api_key=None,
    model_override=None,
    provider="gemini",
    third_party_key=None,
    third_party_model=None,
    third_party_base_url="https://api.runapi.sbs/v1",
    scenes=None,
):
    if scenes is not None:
        scene_arg = scenes[0] if scenes else ""
    else:
        scene_arg = scene or ""
    if check_items is None:
        check_items = []

    prompt = build_prompt(doc_text, scene_arg, level, check_items, skill_context)

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

    effective_key = api_key or GEMINI_API_KEY
    effective_model = model_override or GEMINI_MODEL

    if not effective_key:
        return {"error": "GEMINI_API_KEY 未設定，請填入你的 API Key"}

    FALLBACK_MODELS = [
        effective_model,
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
    ]
    last_err = None
    for model_try in FALLBACK_MODELS:
        try:
            client = _get_gemini_client(effective_key)
            resp = client.models.generate_content(
                model=model_try,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=8192,
                ),
            )
            return _parse_json(resp.text or "")
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "503" in err_str or "UNAVAILABLE" in err_str or "quota" in err_str.lower():
                continue
            return {"error": err_str}
    return {"error": str(last_err)}
