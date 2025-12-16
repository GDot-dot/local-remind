# features/memory.py

from linebot.models import TextSendMessage
from db import save_memory, get_memory, delete_memory, get_all_memories

def handle_memory_command(event, line_bot_api):
    text = event.message.text.strip()
    user_id = event.source.user_id
    
    # 1. 存入: 記住 [關鍵字] [內容]
    if text.startswith('記住'):
        try:
            # 切割字串，限制切成 3 等份 (指令, 關鍵字, 內容)
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 格式錯誤\n範例：記住 wifi 12345678"))
                return
            
            keyword = parts[1]
            content = parts[2]
            
            action = save_memory(user_id, keyword, content)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已{action}記憶！\n關鍵字：{keyword}\n內容：{content}"))
            
        except Exception as e:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 儲存失敗"))

    # 2. 查詢: 查詢 [關鍵字]
    elif text.startswith('查詢'):
        try:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入要查詢的關鍵字。\n範例：查詢 wifi"))
                return
            
            keyword = parts[1]
            result = get_memory(user_id, keyword)
            
            if result:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔍 【{keyword}】\n{result.content}"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤔 找不到關於「{keyword}」的記憶。"))
        except Exception:
            pass

    # 3. 刪除: 忘記 [關鍵字]
    elif text.startswith('忘記'):
        try:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                return
            
            keyword = parts[1]
            if delete_memory(user_id, keyword):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🗑️ 已刪除關於「{keyword}」的記憶。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🤔 找不到這筆記憶，可能已經刪除了。"))
        except Exception:
            pass

    # 4. 清單: 記憶清單
    elif text == '記憶清單':
        memories = get_all_memories(user_id)
        if not memories:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您目前沒有儲存任何記憶喔！\n試試看：記住 wifi 密碼123"))
            return
        
        # 組合成列表字串
        # 格式：
        # 1. wifi
        # 2. 護照號碼
        list_text = "🧠 您的記憶庫：\n\n"
        for idx, mem in enumerate(memories, 1):
            list_text += f"{idx}. {mem.keyword}\n"
            
        list_text += "\n輸入「查詢 [關鍵字]」來查看內容。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=list_text))