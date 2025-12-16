import os
import requests
import logging
import datetime # 新增時間套件
from features.ai_parser import parse_natural_language
import google.generativeai as genai
from db import get_user_cards

logger = logging.getLogger(__name__)

def google_search(query):
    """使用 Google Custom Search API 搜尋 (強制過濾舊資料)"""
    api_key = os.environ.get("GOOGLE_API_KEY")
    cx = os.environ.get("GOOGLE_SEARCH_CX")
    
    if not api_key or not cx:
        return None

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        'key': api_key,
        'cx': cx,
        'q': query,
        'num': 5,       # 抓前 5 筆
        'gl': 'tw',     # 地區限定台灣
        'dateRestrict': 'y1', # 【關鍵修改】只抓「最近 1 年」內的網頁
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
                link = item.get('link', '') # 把連結也抓下來，或許未來有用
                snippets.append(f"標題: {title}\n摘要: {snippet}")
        
        return "\n\n".join(snippets)
    except Exception as e:
        logger.error(f"Google Search Error: {e}")
        return None

def analyze_best_card(user_id, merchant):
    """
    1. 撈使用者卡片
    2. Google 搜尋商家優惠 (最新)
    3. AI 綜合分析
    """
    # 1. 取得使用者卡片
    my_cards = get_user_cards(user_id)
    if not my_cards:
        return "您還沒有設定任何信用卡喔！請先輸入「新增卡片 [卡名]」。"

    my_cards_str = ", ".join(my_cards)

    # 2. Google 搜尋 (動態加入年份)
    current_year = datetime.datetime.now().year
    # 關鍵字策略：商家 + 信用卡 + 回饋 + 年份 + ptt/dcard (論壇資訊通常最新)
    search_query = f"{merchant} 信用卡 回饋 {current_year} ptt dcard"
    
    search_results = google_search(search_query)
    
    if not search_results:
        return "抱歉，我無法連線到搜尋引擎，或找不到相關的最新資訊。"

    # 3. AI 分析
    # 這裡借用 features/ai_parser 的邏輯來抓 Key，確保統一
    api_key = os.environ.get("GOOGLE_API_KEY")
    genai.configure(api_key=api_key)
    
    # 使用 gemini-flash-latest 或 gemini-2.0-flash (看你帳號哪個穩)
    model = genai.GenerativeModel('gemini-flash-latest') 

    prompt = f"""
    你是一個專業的信用卡理財顧問。
    現在年份是：{current_year}。
    
    【使用者擁有的卡片】：{my_cards_str}
    
    【使用者想消費的商家】：{merchant}
    
    【Google 最新搜尋結果 (已過濾為一年內新資料)】：
    {search_results}
    
    請根據上述資訊進行分析：
    1. 優先從「使用者擁有的卡片」中，找出刷 {merchant} 回饋最高的一張。
    2. 忽略搜尋結果中明顯過期或年份不符的資訊（例如標題寫 2023 的）。
    3. 如果使用者的卡片回饋都很低，請根據搜尋結果，推薦一張「市面上最強的卡」作為對比。
    
    請用簡潔的格式回答：
    🏆 **推薦刷：[卡片名稱]** (回饋約 X%)
    💡 **原因**：... (請說明是否有特殊限制，如需登錄或切換權益)
    (如果有更好的卡) 🚀 **市面最強**：[卡片名稱] (回饋 X%)
    """

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI 分析失敗: {e}"