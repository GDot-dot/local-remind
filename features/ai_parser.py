import os
import json
import logging
import google.generativeai as genai

# 設定日誌
logger = logging.getLogger(__name__)

def parse_natural_language(user_text, current_time_str):
    """
    使用 Gemini 解析自然語言提醒 (自動模型選擇 + 強力清洗版)
    """
    # 1. 抓取 Key
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

        # 2. 自動選擇模型 (保留這個成功的邏輯)
        logger.info("🔍 正在查詢可用模型清單...")
        available_models = []
        target_model_name = None

        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
                if 'flash' in m.name and not target_model_name:
                    target_model_name = m.name
                elif 'gemini' in m.name and not target_model_name:
                    target_model_name = m.name

        if not target_model_name:
            if available_models:
                target_model_name = available_models[0]
            else:
                logger.error("❌ [AI] 嚴重錯誤: 帳號沒有可用模型")
                return None
        
        logger.info(f"✅ 系統自動選擇使用模型: {target_model_name}")

        # 3. 發送請求
        model = genai.GenerativeModel(target_model_name)
        
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
        logger.info(f"🤖 [AI] 原始回應: {raw_text}")

        # --- 4. 強力清洗 (修正 Extra data 錯誤) ---
        clean_text = raw_text.strip()
        
        # 去除開頭的 Markdown 標記
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]  # 移除 ```json
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]  # 移除 ```
            
        # 去除結尾的 Markdown 標記 (這就是上次缺少的!)
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3] # 移除最後三個字元
            
        clean_text = clean_text.strip() # 最後再清一次空白
        # ----------------------------------------
        
        result = json.loads(clean_text)
        
        if result.get("event_datetime") and result.get("event_content"):
            return result
        
        return None

    except Exception as e:
        logger.error(f"❌ [AI] 發生錯誤: {e}")
        return None