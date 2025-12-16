# features/memory.py

from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, PostbackAction
)
from db import (
    save_memory, delete_memory, get_all_memories, 
    search_memories_by_keyword, get_memory_by_id  # <--- 記得匯入新函式
)

def handle_memory_command(event, line_bot_api):
    text = event.message.text.strip()
    user_id = event.source.user_id
    
    # 1. 存入
    if text.startswith('記住'):
        try:
            parts = text.split(maxsplit=2)
            if len(parts) < 3:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 格式錯誤\n範例：記住 wifi 12345678"))
                return
            
            keyword = parts[1]
            content = parts[2]
            
            action = save_memory(user_id, keyword, content)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已{action}記憶！\n關鍵字：{keyword}\n內容：{content}"))
        except Exception:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 儲存失敗"))

    # 2. 查詢 (修改為搜尋列表模式)
    elif text.startswith('查詢'):
        try:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入關鍵字。\n範例：查詢 wifi"))
                return
            
            keyword = parts[1]
            # 搜尋所有符合的結果
            results = search_memories_by_keyword(user_id, keyword)
            
            if not results:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🤔 找不到包含「{keyword}」的記憶。"))
                return

            # 如果只有 1 筆，直接顯示內容 (省去點擊)
            if len(results) == 1:
                mem = results[0]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🔍 【{mem.keyword}】\n{mem.content}"))
                return

            # 如果有 2 筆以上，製作按鈕清單讓使用者選
            items = []
            for mem in results[:13]: # LINE QuickReply 最多 13 個按鈕
                items.append(QuickReplyButton(
                    action=PostbackAction(
                        label=mem.keyword[:20], # 標籤不能太長
                        data=f"action=view_memory&id={mem.id}"
                    )
                ))
            
            line_bot_api.reply_message(
                event.reply_token, 
                TextSendMessage(
                    text=f"🔍 找到 {len(results)} 筆相關記憶，請選擇：",
                    quick_reply=QuickReply(items=items)
                )
            )

        except Exception as e:
            print(e)

    # 3. 刪除
    elif text.startswith('忘記'):
        try:
            parts = text.split(maxsplit=1)
            if len(parts) < 2: return
            
            keyword = parts[1]
            if delete_memory(user_id, keyword):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🗑️ 已刪除關於「{keyword}」的記憶。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🤔 找不到這筆記憶。"))
        except Exception: pass

    # 4. 清單
    elif text == '記憶清單':
        memories = get_all_memories(user_id)
        if not memories:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="記憶庫是空的。"))
            return
        
        list_text = "🧠 您的記憶庫：\n\n"
        for idx, mem in enumerate(memories, 1):
            list_text += f"{idx}. {mem.keyword}\n"
        list_text += "\n輸入「查詢 [關鍵字]」來查看內容。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=list_text))

# --- 新增：處理按鈕點擊的函式 ---
def handle_memory_postback(event, line_bot_api):
    data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
    memory_id = int(data.get('id'))
    
    memory_item = get_memory_by_id(memory_id)
    if memory_item:
        line_bot_api.reply_message(
            event.reply_token, 
            TextSendMessage(text=f"🔍 【{memory_item.keyword}】\n{memory_item.content}")
        )
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 讀取失敗，該記憶可能已被刪除。"))