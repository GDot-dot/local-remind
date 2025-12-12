# features/ai_parser.py

import os
import json
import logging
import google.generativeai as genai

# 設定日誌
logger = logging.getLogger(__name__)

def parse_natural_language(user_text, current_time_str):
    """
    使用 Gemini 解析自然語言提醒
    """
    # 1. 取得並檢查 Key
    api_key = os.environ.get("AIzaSyDcOMwWCIriGj_rQFaSJcLgJ-8N8Sq89JM")
    if not api_key:
        logger.error("❌ [AI] 失敗: 系統環境變數中找不到 GOOGLE_API_KEY")
        return None

    try:
        # 2. 初始化模型 (直接在這裡做，最穩)
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        
        # 3. 準備提示詞
        prompt = f"""
        你是一個智慧提醒助理。
        現在的時間是：{current_time_str} (Asia/Taipei)。
        
        使用者的輸入是："{user_text}"
        
        請分析使用者的輸入，提取出「提醒內容」和「提醒時間」。
        規則：
        1. 如果使用者沒有明確說時間，請根據語意推斷。
        2. 如果完全無法推斷時間，則回傳 null。
        3. 時間格式必須嚴格為 "YYYY-MM-DD HH:MM"。
        4. 回傳 JSON 格式：{{ "event_content": "...", "event_datetime": "..." }}
        5. 不要回傳任何其他文字。
        """

        # 4. 發送請求
        logger.info(f"📤 [AI] 發送請求: {user_text}")
        response = model.generate_content(prompt)
        raw_text = response.text
        logger.info(f"🤖 [AI] 收到回應: {raw_text}")

        # 5. 清洗與解析
        clean_text = raw_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.replace("```json", "", 1)
        if clean_text.startswith("```"):
            clean_text = clean_text.replace("```", "")
        
        result = json.loads(clean_text)
        
        if result.get("event_datetime") and result.get("event_content"):
            return result
        
        logger.warning(f"⚠️ [AI] 解析失敗: 欄位不完整 - {result}")
        return None

    except Exception as e:
        logger.error(f"❌ [AI] 發生錯誤: {e}")
        return None