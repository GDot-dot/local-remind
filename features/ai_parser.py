# features/ai_parser.py

import os
import json
import google.generativeai as genai
from datetime import datetime

# 設定 API Key
genai.configure(api_key=os.environ.get("AIzaSyDcOMwWCIriGj_rQFaSJcLgJ-8N8Sq89JM"))

# 設定模型：建議用 gemini-1.5-flash 速度最快
# 如果堅持要用 Pro，改為 "gemini-1.5-pro" 即可
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config={"response_mime_type": "application/json"} # 強制回傳 JSON
)

def parse_natural_language(user_text, current_time_str):
    """
    使用 Gemini 解析自然語言提醒
    回傳格式: {"event_content": "...", "event_datetime": "YYYY-MM-DD HH:MM"} 或 None
    """
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
    5. 不要回傳任何其他文字，只要 JSON。
    """

    try:
        response = model.generate_content(prompt)
        result = json.loads(response.text)
        
        # 簡單驗證欄位是否存在
        if result.get("event_datetime") and result.get("event_content"):
            return result
        return None
    except Exception as e:
        print(f"AI Parsing Error: {e}")
        return None