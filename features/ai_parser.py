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
    # --- 🔍 暴力抓取 Key (解決隱形符號問題) ---
    api_key = None
    target_key_name = "AIzaSyDcOMwWCIriGj_rQFaSJcLgJ-8N8Sq89JM"

    # 方法 1: 直接讀取
    if target_key_name in os.environ:
        api_key = os.environ[target_key_name]
    
    # 方法 2: 如果方法 1 失敗，遍歷所有變數找「長得像」的
    if not api_key:
        logger.warning("⚠️ 直接讀取失敗，嘗試模糊搜尋 Key...")
        for key in os.environ.keys():
            # 只要變數名稱包含 GOOGLE_API_KEY 就抓出來 (忽略前後空白或隱形符號)
            if "GOOGLE_API_KEY" in key:
                api_key = os.environ[key]
                logger.info(f"✅ 透過搜尋找到 Key 了！(原始名稱: '{key}')")
                break

    # 如果還是沒有...
    if not api_key:
        logger.error(f"❌ [AI] 徹底失敗: 系統變數裡真的沒有 Key。現有變數: {list(os.environ.keys())}")
        return None
    # -------------------------------------------

    try:
        # 初始化模型
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )
        
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