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
    # --- 1. 抓取 Key (模糊搜尋邏輯) ---
    api_key = None
    for key in os.environ.keys():
        if "GOOGLE_API_KEY" in key:
            api_key = os.environ[key]
            break

    if not api_key:
        logger.error("❌ [AI] 失敗: 找不到 GOOGLE_API_KEY")
        return None
    # -------------------------------------------

    try:
        # 2. 初始化模型 (更新套件後，這裡就能支援 1.5-flash 了)
        genai.configure(api_key=api_key)
        
        # 改回最快最新的 1.5-flash
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        你是一個智慧提醒助理。
        現在的時間是：{current_time_str} (Asia/Taipei)。
        
        使用者的輸入是："{user_text}"
        
        請分析使用者的輸入，提取出「提醒內容」和「提醒時間」。
        規則：
        1. 如果使用者沒有明確說時間，請根據語意推斷（例如「明天早上」指明天 09:00，「下班後」指今天 18:30）。
        2. 如果完全無法推斷時間，則回傳 null。
        3. 時間格式必須嚴格為 "YYYY-MM-DD HH:MM"。
        4. 回傳 JSON 格式：{{ "event_content": "...", "event_datetime": "..." }}
        5. 不要回傳任何其他文字。
        """

        logger.info(f"📤 [AI] 發送請求: {user_text}")
        response = model.generate_content(prompt)
        raw_text = response.text
        logger.info(f"🤖 [AI] 收到回應: {raw_text}")

        # 清洗與解析
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