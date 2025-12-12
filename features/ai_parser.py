# features/ai_parser.py

import os
import json
import logging
import google.generativeai as genai
from datetime import datetime

# 設定日誌記錄器 (這樣才能在 Fly logs 看到)
logger = logging.getLogger(__name__)

import pprint
env_vars = os.environ.keys()
logger.info(f"🔍 目前系統有的環境變數: {pprint.pformat(list(env_vars))}")

# 取得 API Key
api_key = os.environ.get("AIzaSyDcOMwWCIriGj_rQFaSJcLgJ-8N8Sq89JM")

# 設定模型
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config={"response_mime_type": "application/json"}
    )
else:
    model = None

def parse_natural_language(user_text, current_time_str):
    """
    使用 Gemini 解析自然語言提醒
    """
    # 1. 檢查 API Key 是否存在
    if not api_key:
        logger.error("❌ 嚴重錯誤: 找不到 GOOGLE_API_KEY！請檢查 Fly.io Secrets 設定。")
        return None

    if not model:
        logger.error("❌ 嚴重錯誤: 模型未初始化 (可能是 API Key 無效)。")
        return None

    prompt = f"""
    你是一個智慧提醒助理。
    現在的時間是：{current_time_str} (Asia/Taipei)。
    
    使用者的輸入是："{user_text}"
    
    請分析使用者的輸入，提取出「提醒內容」和「提醒時間」。
    規則：
    1. 如果使用者沒有明確說時間，請根據語意推斷（例如「明天早上」指明天 09:00，「下班後」指今天 18:30，"20分鐘後"請自行計算具體時間）。
    2. 如果完全無法推斷時間，則回傳 null。
    3. 時間格式必須嚴格為 "YYYY-MM-DD HH:MM"。
    4. 回傳 JSON 格式：{{ "event_content": "...", "event_datetime": "..." }}
    5. 不要回傳任何其他文字。
    """

    try:
        logger.info(f"📤 正在發送請求給 Google AI: {user_text}")
        response = model.generate_content(prompt)
        raw_text = response.text
        
        # 印出 AI 回傳的原始文字
        logger.info(f"🤖 Google AI 回應: {raw_text}")

        # 清洗資料
        clean_text = raw_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.replace("```json", "", 1)
        if clean_text.startswith("```"):
            clean_text = clean_text.replace("```", "")
        
        result = json.loads(clean_text)
        
        # 驗證結果
        if result.get("event_datetime") and result.get("event_content"):
            logger.info("✅ AI 解析成功！")
            return result
        
        logger.warning("⚠️ AI 回傳了 JSON，但欄位缺漏。")
        return None

    except Exception as e:
        logger.error(f"❌ AI 解析發生錯誤: {e}")
        return None