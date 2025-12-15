import os
import json
import logging
import google.generativeai as genai

# 設定日誌
logger = logging.getLogger(__name__)

def parse_natural_language(user_text, current_time_str):
    """
    使用 Gemini 解析自然語言提醒 (指定極速模型版)
    """
    # 1. 抓取 Key (保留模糊搜尋，以防萬一)
    api_key = None
    for key in os.environ.keys():
        if "GOOGLE_API_KEY" in key:
            api_key = os.environ[key]
            break

    if not api_key:
        logger.error("❌ [AI] 失敗: 找不到 GOOGLE_API_KEY")
        return None

    try:
        genai.configure(api_key=api_key)

        # 2. 直接指定模型 (省去查詢時間)
        # 根據你的 Log，你的帳號支援最新的 2.5 flash
        target_model = 'gemini-2.0-flash'
        model = genai.GenerativeModel(target_model)
        
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

        logger.info(f"📤 [AI] 發送請求 ({target_model}): {user_text}")
        response = model.generate_content(prompt)
        raw_text = response.text
        logger.info(f"🤖 [AI] 原始回應: {raw_text}")

        # 3. 強力清洗 (保留這個，非常重要)
        clean_text = raw_text.strip()
        
        # 去除開頭
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
            
        # 去除結尾
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
            
        clean_text = clean_text.strip()
        
        result = json.loads(clean_text)
        
        if result.get("event_datetime") and result.get("event_content"):
            return result
        
        return None

    except Exception as e:
        logger.error(f"❌ [AI] 發生錯誤: {e}")
        return None