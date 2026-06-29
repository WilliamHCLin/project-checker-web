# -*- coding: utf-8 -*-
"""
gemini_client.py - AI API
v2: 全面修正 Opus 審查發現的 8 個盲點
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
    # 找不到結尾：AI 輸出被截斷，用不完整原始文字嘗試修復
    candidate = text[start:end+1] if (end != -1 and end >= start) else text[start:]

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # 修復策略1：找最後一個完整 item 結尾 }]，補上外層 }
    last_complete = candidate.rfind('}]')
    if last_complete != -1:
        repaired = candidate[:last_complete+2] + ', "overall_comment": "(AI 輸出被截斷，部分項目省略)"}'
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # 修復策略2：找最後一個完整 item 的 "}，補上 ]} 收尾
    last_item = candidate.rfind('"}')
    if last_item != -1:
        repaired = candidate[:last_item+2] + '], "overall_comment": "(AI 輸出被截斷，部分項目省略)"}'
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


# ── 官方場景名稱（對齊下拉選單，不可自創）──────────────────────────
SCENE_LIST_TEXT = (
    "A. 班級日常經營\n"
    "B. 專案活動執行\n"
    "C. 比賽類專案\n"
    "D. 對外藝文交流\n"
    "E. 招生與傳承\n"
    "F. 外部邀約應對\n"
    "G. 專案管理基本功\n"
    "H. 個人升學/書審\n"
    "I. 教學品質管理\n"
    "J. 設備物資管理"
)

VALID_SCENES = {
    "A. 班級日常經營",
    "B. 專案活動執行",
    "C. 比賽類專案",
    "D. 對外藝文交流",
    "E. 招生與傳承",
    "F. 外部邀約應對",
    "G. 專案管理基本功",
    "H. 個人升學/書審",
    "I. 教學品質管理",
    "J. 設備物資管理",
}

# ── 官方 result 六狀態（對齊下拉選單，不可自創）──────────────────
RESULT_DEFINITION = (
    "result 只能是以下六者之一（不得自創其他詞）：\n"
    "- 已完成：明確滿足做好的標準「全部」條件，且文件有具體證據（時程/數字/人名/地點/數量）\n"
    "- 部分完成：有相關內容但缺關鍵資訊，或出現未處理的常見錯誤\n"
    "- 未完成：完全沒有提到，或嚴重不足\n"
    "- 不適用：本案「結構上」不涉及此項目（須說明為何不涉及）；「文件沒寫到」不等於不適用，預設應判未完成\n"
    "- 需補件：缺具體資料，同學可實際補填（≠未完成：未完成是沒做，需補件是做了但資料不齊）\n"
    "- 需確認：資料前後矛盾或模糊，需人工確認事實\n"
)

# ── 判定階梯（來自欄位定義 D 欄）─────────────────────────────────
JUDGMENT_LADDER = (
    "【逐項判定步驟】\n"
    "第一步：此項目是否適用本案？\n"
    "  → 否（結構上不存在）→ 不適用（說明原因）\n"
    "  → 是 → 繼續\n\n"
    "第二步：文件中是否有任何相關內容？\n"
    "  → 完全沒提 → 未完成\n\n"
    "第三步：有內容，是否達到「做好的標準」全部條件？\n"
    "  → 全部符合且有具體證據 → 已完成\n"
    "  → 有關鍵字但無具體內容（如出現「教練」但無到場時間）→ 部分完成\n"
    "  → 出現模糊詞（差不多/應該/大概/再說）於時程/數量/費用 → 部分完成或需補件\n"
    "  → 缺關鍵項但同學可補填 → 需補件\n\n"
    "第四步：有矛盾或模糊？\n"
    "  → 前後矛盾（人數、預算、時程不一致）→ 需確認\n"
    "  → 資料僅靠猜測（「教練應該會帶」）→ 需確認\n\n"
    "第五步：命中常見錯誤且文件未處理 → 至少降為部分完成\n\n"
    "每一項都必須在 evidence 引用文件原文；找不到原文一律不可判已完成。\n"
    "「不適用」每個都必須給一句結構性原因，否則改判未完成。\n"
)

# ── 層級判定規則（風險層級，非成長層級）─────────────────────────
LEVEL_JUDGMENT = (
    "【專案風險層級判定（任一成立即為 A 級，不可降級）】\n"
    "A 級（高風險）：對外/首次/比賽/表演/未成年/安全風險/影響組織形象/有貴賓或外部師資\n"
    "B 級（中風險）：內部但人數多、有家長/講師、有成果發布、有預算支出\n"
    "C 級（低風險）：小型例行、人數少、對外影響小\n"
    "⚠️ 涉及未成年人（國中小學生）→ 自動 A 級，底線項目一律不可讓步\n"
    "⚠️ 首次舉辦 → 自動 A 級\n"
    "⚠️ A 級的底線（紅色）項目，無論 member 層級一律必須檢核\n"
)

# ── 填答人規則（來自欄位定義 F 欄）───────────────────────────────
RESPONDENT_RULE = (
    "【補件建議規則】\n"
    "- 不得指派文件中未出現的人名\n"
    "- 需指派時寫「待指定（建議：具備___能力者）」\n"
    "- 不得填「老師」或「William」（老師是決策者，不是執行者）\n"
    "- 安全/未成年 → 建議教練層級；行政/報名 → 建議行政組\n"
)


# ── 第一輪：場景辨識 ──────────────────────────────────────────────

def detect_scenes_ai(
    doc_text: str,
    api_key=None,
    model_override=None,
    provider="gemini",
    third_party_key=None,
    third_party_model=None,
    third_party_base_url="https://api.runapi.sbs/v1",
) -> list:
    """
    第一輪 AI：完整閱讀文件，深度理解使用者意圖後選場景。
    G. 專案管理基本功永遠自動納入（不靠 AI 判斷）。
    """
    from config import SCENE_KEYWORDS
    from db_loader import detect_scene

    prompt = (
        "你是生命動能協會 AI 助手。\n"
        "請完整閱讀以下文件（包含所有段落與表格），先深度理解：\n"
        "1. 這份文件的功能與目的（它要達成什麼任務？）\n"
        "2. 文件的整體架構與設計邏輯\n"
        "3. 使用者（member）目前在做什麼、處於哪個執行階段\n"
        "4. 涉及哪些人員、組織、資源\n"
        "5. 這是例行性的還是單次專案？是對內還是對外？\n\n"
        "充分理解後，從下方官方場景列表中選出最相關的「主場景（1個）」"
        "以及「次要場景（0-2個，僅在文件有實質對應內容時才選）」。\n\n"
        "選擇規則：\n"
        "- 主場景：最能描述這份文件核心任務的場景\n"
        "- 次要場景：文件有實質內容對應，並給出一句依據；沒有依據不可選\n"
        "- G. 專案管理基本功：系統會自動納入，你不需要選它\n"
        "- 不要為了涵蓋而過度選取\n\n"
        "同時判斷專案風險層級：\n"
        "A 級：對外/首次/比賽/表演/未成年/安全風險/影響組織形象\n"
        "B 級：內部但人數多/有家長或外部師資/有成果發布/有預算\n"
        "C 級：小型例行、人數少、對外影響小\n"
        "（有任一 A 級條件成立 → 自動 A 級）\n\n"
        "【官方場景列表】\n"
        + SCENE_LIST_TEXT + "\n\n"
        "【完整文件內容】\n"
        + doc_text[:80000] + "\n\n"
        "請直接輸出純 JSON，格式：\n"
        "{\n"
        '  "primary_scene": "A. 班級日常經營",\n'
        '  "secondary_scenes": [{"scene": "C. 比賽類專案", "reason": "文件提及選手報名流程"}],\n'
        '  "level": "A",\n'
        '  "level_reason": "涉及未成年學員，且為首次對外比賽",\n'
        '  "scope_flags": ["有未成年人", "首次舉辦"]\n'
        "}"
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
                    max_output_tokens=1024,
                    temperature=0.1,
                ),
            )
            result = _parse_json(resp.text or "")

        # 提取主場景 + 次要場景，過濾非法名稱
        primary = result.get("primary_scene", "")
        if primary not in VALID_SCENES:
            primary = ""

        secondary = []
        for item in result.get("secondary_scenes", []):
            sc = item.get("scene", "") if isinstance(item, dict) else item
            if sc in VALID_SCENES and sc != primary:
                secondary.append(sc)

        # G 永遠自動納入
        scenes = []
        if primary:
            scenes.append(primary)
        scenes.extend(secondary)
        if "G. 專案管理基本功" not in scenes:
            scenes.append("G. 專案管理基本功")

        return {
            "_scenes":      scenes,
            "level":        result.get("level", "B"),
            "level_reason": result.get("level_reason", ""),
            "scope_flags":  result.get("scope_flags", []),
        }

    except Exception:
        pass

    # 回退：關鍵字比對
    fallback = detect_scene(doc_text)
    return {
        "_scenes":      [fallback["scene"], "G. 專案管理基本功"],
        "level":        fallback.get("level", "B"),
        "level_reason": "AI 辨識失敗，改用關鍵字比對",
        "scope_flags":  [],
    }


# ── 第二輪：完整分析 ──────────────────────────────────────────────

def build_prompt(doc_text, scene, level, check_items, skill_context):
    # 重要性符號（底線/核心/一般 → 對應紅/黃/白）
    IMP_LABEL = {"紅": "[底線]", "黃": "[核心]", "白": "[一般]"}

    # 分批：底線+核心優先，一般最後
    red_items = [it for it in check_items if it.get("importance") == "紅"]
    yellow_items = [it for it in check_items if it.get("importance") == "黃"]
    white_items = [it for it in check_items if it.get("importance") == "白"]
    ordered_items = red_items + yellow_items + white_items

    items_text = ""
    for it in ordered_items:
        imp = it.get("importance", "白")
        imp_label = IMP_LABEL.get(imp, "[一般]")
        phase = it.get("phase", "")
        domain = it.get("domain", "")
        items_text += (
            "\n[序號" + str(it['seq']) + "] " + imp_label
            + " " + str(it['item']) + "\n"
            "  能力領域：" + str(domain) + "　階段：" + str(phase) + "\n"
            "  做好的標準：" + str(it.get('standard', '')) + "\n"
            "  需要的資訊：" + str(it.get('needed_info', '')) + "\n"
            "  常見錯誤：" + str(it.get('common_errors', '')) + "\n"
        )

    importance_note = (
        "【重要性說明（底線/核心/一般 對應 紅/黃/白）】\n"
        "[底線]=紅色，安全底線，絕對不能缺少，不做等於沒做這件事\n"
        "[核心]=黃色，A 級核心項目，鍾老師高度關注，缺少會影響整體品質\n"
        "[一般]=白色，一般品質項目，有做更好但缺少可接受\n"
        "⚠️ 底線項目若未達標，必須在 critical_missing 第一時間列出\n"
    )

    json_schema = (
        "{\n"
        '  "scene_confirmed": "使用官方場景名稱（對齊下拉選單）",\n'
        '  "level_confirmed": "A或B或C（風險層級，非成長層級）",\n'
        '  "level_reason": "判定依據一句話",\n'
        '  "scene_note": "這份文件在做什麼，用一句話說明",\n'
        '  "critical_missing": ["底線項目未達標的簡述，語氣直接"],\n'
        '  "items": [\n'
        "    {\n"
        '      "seq": 序號,\n'
        '      "item": "檢核項目名稱",\n'
        '      "importance": "紅或黃或白",\n'
        '      "result": "已完成|部分完成|未完成|不適用|需補件|需確認",\n'
        '      "evidence": "引用文件原文（找不到原文則填空字串，不可判已完成）",\n'
        '      "missing": ["缺少的具體資料1", "缺少的具體資料2"],\n'
        '      "suggestion": "你需要補充：XXX（不得指派文件未出現的人名）",\n'
        '      "common_error_found": "命中常見錯誤說明，無則填空字串"\n'
        "    }\n"
        "  ],\n"
        '  "overall_comment": "整體評語，先說底線狀況，再說核心，最後整體"\n'
        "}"
    )

    return (
        "你是 William 老師（鍾老師）的生命動能協會 AI 助手。\n"
        "任務：對照 William 的要求資料庫，逐項判斷文件完成度，生成補件清單。\n\n"
        + importance_note + "\n"
        + RESULT_DEFINITION + "\n"
        + JUDGMENT_LADDER + "\n"
        + LEVEL_JUDGMENT + "\n"
        + RESPONDENT_RULE + "\n"
        + skill_context + "\n\n"
        "【當前使用場景與風險層級】\n"
        "能力領域：" + str(scene) + "\n"
        "風險層級：" + str(level) + " 級\n"
        "（A 級=高風險對外/未成年/首次；B 級=中風險有家長師資；C 級=低風險例行）\n\n"
        "【待檢核項目（共 " + str(len(ordered_items)) + " 項，底線→核心→一般排列）】\n"
        "⚠️ 請依序完成所有底線項目的判定，再處理核心，最後才是一般項目。\n"
        + items_text + "\n\n"
        "【Member 上傳的規劃文件（完整內容）】\n"
        + doc_text[:100000] + "\n\n"
        "【輸出規則】\n"
        "1. 底線項目未達標 → 必須列入 critical_missing，語氣直接\n"
        "2. suggestion 格式：「你需要補充：XXX」，不得用「可以考慮/建議」等模糊語氣\n"
        "3. evidence 必須引用文件原文；找不到原文 → 一律不可判已完成\n"
        "4. 不適用 → 必須說明結構性原因；純粹找不到內容 → 改判未完成\n"
        "5. 出現模糊詞（差不多/應該/大概）→ 至少判部分完成或需補件\n"
        "6. 不得指派文件中未出現的人名\n\n"
        "請直接輸出純 JSON：\n"
        + json_schema
    )


# ── 主入口 ────────────────────────────────────────────────────────

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

    import time
    MAX_RETRY = 5
    RETRY_WAIT = 8  # 秒，每次等 8 秒，5 次共最多等 40 秒撐過冷啟動
    last_err = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            client = _get_gemini_client(effective_key)
            resp = client.models.generate_content(
                model=effective_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    max_output_tokens=8192,
                    temperature=0.1,
                ),
            )
            return _parse_json(resp.text or "")
        except Exception as e:
            last_err = e
            err_str = str(e)
            if ("503" in err_str or "429" in err_str or "overloaded" in err_str.lower()) and attempt < MAX_RETRY:
                time.sleep(RETRY_WAIT)
                continue  # 同模型重試
            break  # 其他錯誤或已達上限

    return {"error": f"Gemini 呼叫失敗（重試 {MAX_RETRY} 次）：{last_err}"}
