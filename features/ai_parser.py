# features/ai_parser.py

import os
import json
import logging
import google.generativeai as genai
from datetime import datetime

# 設定日誌
logger = logging.getLogger(__name__)

# --- 注意：我把初始化移到函式內，避免 Import 時環境變數還沒載入 ---
model = None

def get_model():
    global model
    api_key = os.environ.get("AIzaSyDcOMwWCIriGj_rQFaSJcLgJ-8N8Sq89JM")
    if api_key and not model:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
    return model

def parse_natural_language(user_text, current_time_str):
    """
    使用 Gemini 解析自然語言提醒
    """
    # 1. 嘗試獲取 Key
    api_key = os.environ.get("GOOGLE_API_KEY")
    
    # --- 🔍 抓鬼大隊：印出所有變數名稱 ---
    if not api_key:
        logger.error("❌ 找不到 GOOGLE_API_KEY！")
        
        # 把所有變數名稱印出來檢查 (只印名稱，不印值，確保安全)
        all_vars = list(os.environ.keys())
        logger.error(f"🔍 目前系統內有的變數: {all_vars}")
        
        # 檢查是否有類似的名稱 (例如多了空白鍵)
        for key in all_vars:
            if "GOOGLE" in key:
                logger.error(f"⚠️ 發現疑似變數: '{key}' (長度: {len(key)})")
                
        return None
    # -----------------------------------

    current_model = get_model()
    if not current_model:
        logger.error("❌ 模型初始化失敗")
        return None

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

    try:
        logger.info(f"📤 發送請求: {user_text}")
        response = current_model.generate_content(prompt)
        raw_text = response.text
        logger.info(f"🤖 AI 回應: {raw_text}")

        clean_text = raw_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.replace("```json", "", 1)
        if clean_text.startswith("```"):
            clean_text = clean_text.replace("```", "")
        
        result = json.loads(clean_text)
        
        if result.get("event_datetime") and result.get("event_content"):
            return result
        return None

    except Exception as e:
        logger.error(f"❌ AI 解析錯誤: {e}")
        return None