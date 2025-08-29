# features/location.py

from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, PostbackAction,
    LocationSendMessage, MessageAction
)
from db import (
    add_location, get_all_locations_by_user, get_location_by_name,
    delete_location_by_name
)

def handle_list_locations_command(event, line_bot_api):
    """處理'地點清單'指令，作為地點功能主選單"""
    user_id = event.source.user_id
    locations = get_all_locations_by_user(user_id)

    if not locations:
        quick_reply = QuickReply(items=[QuickReplyButton(action=PostbackAction(label="👋 點我新增第一個地點", data="action=loc_add_prompt"))])
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您尚未記錄任何地點喔！", quick_reply=quick_reply))
        return
    
    location_list = "\n".join([f"📍 {loc.name}" for loc in locations])
    reply_text = f"您目前記錄的地點有：\n{location_list}"

    quick_reply = QuickReply(items=[
        QuickReplyButton(action=PostbackAction(label="➕ 新增", data="action=loc_add_prompt")),
        QuickReplyButton(action=PostbackAction(label="🔍 檢視", data="action=loc_view_prompt")),
        QuickReplyButton(action=PostbackAction(label="⛔ 刪除", data="action=loc_delete_prompt")),
        QuickReplyButton(action=PostbackAction(label="❌ 取消", data="action=cancel"))
    ])
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))

def handle_save_location_command(event, line_bot_api, user_states):
    """處理使用者在'awaiting_loc_name'狀態下輸入的地點名稱"""
    user_id, location_name = event.source.user_id, event.message.text.strip()
    
    if user_id in user_states: del user_states[user_id]
    user_states[user_id] = {'action': 'awaiting_location', 'name': location_name}
    
    quick_reply = QuickReply(items=[QuickReplyButton(action=PostbackAction(label="取消新增", data="action=cancel"))])
    line_bot_api.reply_message(
        event.reply_token, 
        TextSendMessage(text=f"📍好的，請傳送您要為「{location_name}」記錄的 LINE 位置訊息給我。", quick_reply=quick_reply)
    )

def handle_find_location_command(event, line_bot_api):
    """處理'找地點'指令"""
    user_id, text = event.source.user_id, event.message.text.strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🔍 請輸入要尋找的地點名稱。\n格式：找地點 [地點名稱]"))
        return
    
    location_name = parts[1]
    location = get_location_by_name(user_id, location_name)
    if location:
        line_bot_api.reply_message(event.reply_token, LocationSendMessage(
            title=location.name, address=location.address if location.address else "無地址資訊",
            latitude=location.latitude, longitude=location.longitude))
    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到名為「{location_name}」的地點記錄。"))

def handle_delete_location_command(event, line_bot_api):
    """處理 '刪除地點：[地點名稱]' 的指令"""
    user_id, text = event.source.user_id, event.message.text.strip()
    try:
        location_name = text.split('：', 1)[1]
    except IndexError: return
    
    if delete_location_by_name(user_id, location_name):
        reply_text = f"✅ 地點「{location_name}」已成功刪除。"
    else:
        reply_text = f"🤔 咦？找不到名為「{location_name}」的地點，可能已被刪除。"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

def handle_location_message(event, line_bot_api, user_states):
    """處理使用者傳送的 LINE 位置訊息"""
    user_id = event.source.user_id
    if user_id in user_states and user_states[user_id]['action'] == 'awaiting_location':
        state = user_states[user_id]
        location_name, loc_msg = state['name'], event.message
        result = add_location(user_id=user_id, name=location_name, address=loc_msg.address,
                              latitude=loc_msg.latitude, longitude=loc_msg.longitude)
        reply_text = f"✅ 地點「{location_name}」已成功儲存！" if result == "成功" else f"❌ 儲存失敗：{result}"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        del user_states[user_id]

def handle_location_postback(event, line_bot_api, user_states):
    """處理地點功能相關的 Postback 事件"""
    data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
    action = data.get('action')
    user_id = event.source.user_id

    if action == 'loc_add_prompt':
        user_states[user_id] = {'action': 'awaiting_loc_name'}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 請直接輸入您想新增的地點名稱（例如：公司停車位）：")
        )
    
    elif action in ['loc_view_prompt', 'loc_delete_prompt']:
        is_view = action == 'loc_view_prompt'
        prompt_text = "請點擊您想檢視的地點：" if is_view else "請點擊您想刪除的地點："
        action_prefix = "找地點 " if is_view else "刪除地點："
        
        locations = get_all_locations_by_user(user_id)
        if not locations:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您沒有可操作的地點。"))
            return

        items = [QuickReplyButton(action=MessageAction(label=loc.name, text=f"{action_prefix}{loc.name}")) for loc in locations]
        
        if len(items) > 12: items = items[:12]
        
        items.append(QuickReplyButton(action=PostbackAction(label="取消", data="action=cancel")))
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=prompt_text, quick_reply=QuickReply(items=items))
        )