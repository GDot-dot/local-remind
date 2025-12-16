import os
import requests
import logging
from features.ai_parser import parse_natural_language # 借用裡面的 Key 設定
import google.generativeai as genai
from db import get_user_cards

logger = logging.getLogger(__name__)

def google_search(query):
    """使用 Google Custom Search API 搜尋"""
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx = os.environ.get("GOOGLE_SEARCH_CX")
    
    if not api_key or not cx:
        return None

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cx,
        'q': query,
        'num': 5, # 抓前 5 筆結果
        'gl': 'tw', # 地區台灣
    }
    
    try:
        response = requests.get(url, params=params)
        results = response.json()
        
        # 整理搜尋摘要
        snippets = []
        if 'items' in results:
            for item in results['items']:
                title = item.get('title', '')
                snippet = item.get('snippet', '')
                snippets.append(f"標題: {title}\n內容: {snippet}")
        
        return "\n\n".join(snippets)
    except Exception as e:
        logger.error(f"Google Search Error: {e}")
        return None

def analyze_best_card(user_id, merchant):
    """
    1. 撈使用者卡片
    2. Google 搜尋商家優惠
    3. AI 綜合分析
    """
    # 1. 取得使用者卡片
    my_cards = get_user_cards(user_id)
    if not my_cards:
        return "您還沒有設定任何信用卡喔！請先輸入「新增卡片 [卡名]」。"

    my_cards_str = ", ".join(my_cards)

    # 2. Google 搜尋
    search_query = f"{merchant} 信用卡 回饋 2025 推薦 ptt dcard"
    search_results = google_search(search_query)
    
    if not search_results:
        return "抱歉，我無法連線到搜尋引擎，暫時無法分析。"

    # 3. AI 分析
    # 這裡我們直接用 features/ai_parser 裡面的 model，或是重新 init 一個
    # 為了方便，這裡簡化寫法，您整合時可以優化
    api_key = os.environ.get("GOOGLE_API_KEY")
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-flash-latest')

    prompt = f"""
    你是一個專業的信用卡理財顧問。
    
    【使用者擁有的卡片】：{my_cards_str}
    
    【使用者想消費的商家】：{merchant}
    
    【網路搜尋到的最新回饋資訊 (2025)】：
    {search_results}
    
    請根據上述資訊，進行分析：
    1. 從「使用者擁有的卡片」中，找出刷 {merchant} 回饋最高的一張。
    2. 如果使用者的卡片都很爛，請根據搜尋結果，推薦一張「市面上最強的卡」作為對比。
    3. 如果搜尋結果不明確，請根據你的常識判斷 (例如 KKTIX 通常屬於網購或娛樂類別)。
    
    請用簡潔的格式回答：
    🏆 **推薦刷：[卡片名稱]** (回饋約 X%)
    💡 **原因**：...
    (如果有更好的卡) 🚀 **市面最強**：[卡片名稱] (回饋 X%)
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 分析失敗: {e}"