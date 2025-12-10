# features/reminder.py (最終完整版 - 修正刷新按鈕)

import re
from datetime import datetime, timedelta
from linebot.exceptions import LineBotApiError
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, PostbackAction, MessageAction,
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, 
    ButtonComponent, SeparatorComponent
)
from db import (
    add_event, get_event, update_reminder_time, reset_reminder_sent_status,
    get_all_events_by_user, delete_event_by_id
)

WEEKDAYS_MAP = {"MON": "一", "TUE": "二", "WED": "三", "THU": "四", "FRI": "五", "SAT": "六", "SUN": "日"}

PRIORITY_RULES = {
    1: {"color": "#28a745", "label": "🟢 綠色 (30分/1次)", "interval": 30, "repeats": 1},
    2: {"color": "#ffc107", "label": "🟡 黃色 (10分/2次)", "interval": 10, "repeats": 2},
    3: {"color": "#dc3545", "label": "🔴 紅色 (5分/3次)",  "interval": 5,  "repeats": 3}
}

EARLY_REMINDER_OPTIONS = {
    0: "準時",
    5: "前 5 分鐘",
    10: "前 10 分鐘",
    30: "前 30 分鐘",
    60: "前 1 小時"
}

def parse_datetime(datetime_str, TAIPEI_TZ):
    from dateutil.parser import parse
    try:
        return parse(datetime_str, yearfirst=False)
    except Exception:
        now = datetime.now(TAIPEI_TZ)
        parts = datetime_str.replace('/', '-').split()
        date_part, time_part = parts[0], parts[1] if len(parts) > 1 else f"{now.hour}:{now.minute}"
        try:
            if date_part.count('-') == 1: date_part = f"{now.year}-{date_part}"
            full_dt_str = f"{date_part} {time_part}"
            return datetime.strptime(full_dt_str, '%Y-%m-%d %H:%M')
        except Exception:
            return None

def handle_reminder_command(event, line_bot_api, TAIPEI_TZ, now_in_taipei):
    """處理'提醒'指令"""
    try:
        text = event.message.text.strip()
        creator_user_id = event.source.user_id
        source = event.source
        source_type = source.type
        destination_id = getattr(source, f'{source.type}_id', None)
        if not destination_id: return
        match = re.match(r'^提醒(.*?)\s+(今天|明天|後天|[0-9/]+)\s*([0-9]{1,2}:[0-9]{2})?\s*(.+)$', text)
        if not match:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 提醒格式錯誤。\n請確認 [誰] 和 [日期] 之間有空格。\n範例：提醒我 今天 10:30 開會"))
            return
        who_to_remind_text, date_str, time_str, content = match.groups()
        who_to_remind_text = who_to_remind_text.strip()
        if not who_to_remind_text: who_to_remind_text = "我"
        content = content.strip()
        now_in_taipei = datetime.now(TAIPEI_TZ)
        dt_map = {'今天': 0, '明天': 1, '後天': 2}
        dt = now_in_taipei + timedelta(days=dt_map.get(date_str, 0))
        datetime_str = f"{dt.strftime('%Y/%m/%d') if date_str in dt_map else date_str} {time_str if time_str else ''}".strip()
        naive_dt = parse_datetime(datetime_str, TAIPEI_TZ)
        if not naive_dt:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 時間格式有誤，請檢查後重新輸入。"))
            return
        event_dt = TAIPEI_TZ.localize(naive_dt)
        if event_dt <= now_in_taipei:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 提醒時間不能設定在過去喔！"))
            return
        target_display_name = who_to_remind_text
        if who_to_remind_text == '我':
            try:
                if source_type == 'group': profile = line_bot_api.get_group_member_profile(destination_id, creator_user_id)
                elif source_type == 'room': profile = line_bot_api.get_room_member_profile(destination_id, creator_user_id)
                else: profile = line_bot_api.get_profile(creator_user_id)
                target_display_name = profile.display_name
            except LineBotApiError: target_display_name = "您"
            
        event_id = add_event(
            creator_user_id=creator_user_id, target_id=destination_id, target_type=source_type,
            display_name=target_display_name, content=content, event_datetime=event_dt,
            is_recurring=0, priority_level=0, remaining_repeats=0)
        if not event_id:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 建立提醒失敗，請稍後再試。"))
            return
        quick_reply_buttons = QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="10分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=10")),
            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
        ])
        reply_text = f"✅ 已記錄：{target_display_name} {event_dt.strftime('%Y/%m/%d %H:%M')} {content}\n\n希望什麼時候提醒您呢？"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply_buttons))
    except Exception as e:
        raise e

def handle_priority_reminder_command(event, line_bot_api, user_states, TAIPEI_TZ):
    """處理'重要提醒'指令 - 第一步：選擇提早時間"""
    text = event.message.text.strip()
    match = re.match(r'^重要提醒(.*?)\s+(今天|明天|後天|[0-9/]+)\s*([0-9]{1,2}:[0-9]{2})?\s*(.+)$', text)
    
    if not match:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 格式錯誤。範例：重要提醒 我 明天 10:00 搶票"))
        return

    user_id = event.source.user_id
    user_states[user_id] = {
        "action": "setting_priority_time",
        "data": match.groups()
    }

    buttons = []
    # 按照時間順序排列
    for minutes, label in sorted(EARLY_REMINDER_OPTIONS.items(), key=lambda x: x[0]):
        buttons.append(
            ButtonComponent(
                style='secondary',
                height='sm',
                action=PostbackAction(label=label, data=f'action=set_priority_time&minutes={minutes}')
            )
        )
    buttons.append(ButtonComponent(style='link', height='sm', action=PostbackAction(label='取消', data='action=cancel')))

    bubble = BubbleContainer(
        body=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='設定提前提醒', weight='bold', size='xl', align='center'),
                TextComponent(text='您希望在事件發生前多久收到通知？', size='sm', margin='md', color='#aaaaaa', wrap=True),
                SeparatorComponent(margin='md'),
                *buttons
            ]
        )
    )
    line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="選擇提前時間", contents=bubble))


def handle_reminder_postback(event, line_bot_api, scheduler, send_reminder_func, safe_add_job_func, TAIPEI_TZ, user_states):
    """處理提醒功能相關的 Postback 事件"""
    data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
    action = data.get('action')
    user_id = event.source.user_id

    # --- 1. 優先處理不需要 event_id 的操作 ---

    # --- 重新整理 / 翻頁 ---
    if action == 'refresh_manage_panel':
        # 從 data 中獲取 page 參數，如果沒有則預設為 1
        try:
            page = int(data.get('page', 1))
        except ValueError:
            page = 1
            
        events = get_all_events_by_user(user_id)
        if events:
            # 傳入 page 參數
            bubble = create_management_flex(events, page=page)
            flex_message = FlexSendMessage(alt_text=f"提醒管理面板 (第 {page} 頁)", contents=bubble)
            line_bot_api.reply_message(event.reply_token, flex_message)
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有任何提醒。"))
        return

    # --- 重要提醒：選擇提早時間 ---
    if action == 'set_priority_time':
        if user_id not in user_states or user_states[user_id].get("action") != "setting_priority_time": return
        
        minutes_early = int(data.get('minutes'))
        user_states[user_id]["minutes_early"] = minutes_early
        user_states[user_id]["action"] = "setting_priority_level"

        bubble = BubbleContainer(
            body=BoxComponent(
                layout='vertical',
                contents=[
                    TextComponent(text='選擇重要程度', weight='bold', size='xl', align='center'),
                    TextComponent(text='請選擇重複提醒的頻率：', size='sm', margin='md', color='#aaaaaa'),
                    SeparatorComponent(margin='md'),
                    ButtonComponent(style='primary', color=PRIORITY_RULES[3]['color'], margin='md', action=PostbackAction(label=PRIORITY_RULES[3]['label'], data='action=set_priority&level=3')),
                    ButtonComponent(style='primary', color=PRIORITY_RULES[2]['color'], margin='sm', action=PostbackAction(label=PRIORITY_RULES[2]['label'], data='action=set_priority&level=2')),
                    ButtonComponent(style='primary', color=PRIORITY_RULES[1]['color'], margin='sm', action=PostbackAction(label=PRIORITY_RULES[1]['label'], data='action=set_priority&level=1')),
                    ButtonComponent(style='link', margin='sm', height='sm', action=PostbackAction(label='取消', data='action=cancel'))
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="選擇重要程度", contents=bubble))
        return

    # --- 重要提醒：選擇等級並設定排程 ---
    if action == 'set_priority':
        if user_id not in user_states or user_states[user_id].get("action") != "setting_priority_level": return
        level = int(data.get('level'))
        
        raw_data = user_states[user_id]["data"]
        minutes_early = user_states[user_id]["minutes_early"]
        del user_states[user_id]
        
        who, date_str, time_str, content = raw_data
        who = who.strip() or "我"
        content = content.strip()
        
        now_in_taipei = datetime.now(TAIPEI_TZ)
        dt_map = {'今天': 0, '明天': 1, '後天': 2}
        dt = now_in_taipei + timedelta(days=dt_map.get(date_str, 0))
        datetime_str = f"{dt.strftime('%Y/%m/%d') if date_str in dt_map else date_str} {time_str if time_str else ''}".strip()
        naive_dt = parse_datetime(datetime_str, TAIPEI_TZ)
        if not naive_dt:
             line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 時間格式錯誤。"))
             return
        event_dt = TAIPEI_TZ.localize(naive_dt)

        reminder_dt = event_dt - timedelta(minutes=minutes_early)
        if reminder_dt <= datetime.now(TAIPEI_TZ):
             line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 計算出的提醒時間已過，無法設定。"))
             return

        source = event.source
        destination_id = getattr(source, f'{source.type}_id', user_id)
        rule = PRIORITY_RULES[level]
        
        event_id = add_event(
            creator_user_id=user_id,
            target_id=destination_id,
            target_type=source.type,
            display_name=who,
            content=content,
            event_datetime=event_dt, # 資料庫存事件發生的時間
            is_recurring=0,
            priority_level=level,
            remaining_repeats=rule['repeats']
        )
        
        if safe_add_job_func(send_reminder_func, reminder_dt, [event_id], f'reminder_{event_id}'):
            early_text = f"({EARLY_REMINDER_OPTIONS[minutes_early]})" if minutes_early > 0 else ""
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已設定重要提醒！將於 {reminder_dt.strftime('%H:%M')} {early_text} 開始提醒您。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 排程設定失敗。"))
        return

    # --- 2. 嘗試獲取 event_id (針對需要 ID 的操作) ---
    try:
        event_id = int(data.get('id', 0))
    except ValueError: return
    if not event_id: return

    # --- 3. 處理需要 event_id 的操作 ---

    if action == 'confirm_reminder':
        event_record = get_event(event_id)
        if event_record:
            if not event_record.is_recurring:
                result = delete_event_by_id(event_id, user_id)
                if result.get("status") == "success":
                    # 同時嘗試從排程器移除
                    if scheduler.get_job(f"reminder_{event_id}"):
                        scheduler.remove_job(f"reminder_{event_id}")
                    
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 任務已完成並移除！"))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 收到確認 (資料可能已被移除)。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 提醒已確認收到！"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 提醒已確認 (任務已結束)。"))
    
    elif action == 'set_reminder':
        event_record = get_event(event_id)
        if not event_record:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該提醒事件。"))
            return
        event_dt = event_record.event_datetime.astimezone(TAIPEI_TZ)
        reminder_dt, reply_msg_text = None, "❌ 未知的提醒類型。"
        reminder_type = data.get('type')
        if reminder_type == 'none':
            reply_msg_text = "✅ 好的，這個事件將不設定提醒。"
        else:
            value = int(data.get('val', 0))
            delta = timedelta(days=value) if reminder_type == 'day' else timedelta(minutes=value)
            reminder_dt = event_dt - delta
            if reminder_dt <= datetime.now(TAIPEI_TZ):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 提醒時間已過，無法設定。"))
                return
            if safe_add_job_func(send_reminder_func, reminder_dt, [event_id], f'reminder_{event_id}'):
                reply_msg_text = f"✅ 設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
            else:
                reply_msg_text = "❌ 設定提醒時發生錯誤。"
        if update_reminder_time(event_id, reminder_dt):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg_text))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 更新資料庫失敗。"))

    elif action == 'snooze_reminder':
        event_record = get_event(event_id)
        if event_record and not event_record.is_recurring:
            minutes = int(data.get('minutes', 5))
            reset_reminder_sent_status(event_id)
            snooze_time = datetime.now(TAIPEI_TZ) + timedelta(minutes=minutes)
            if safe_add_job_func(send_reminder_func, snooze_time, [event_id], f'reminder_{event_id}'):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⏰ 好的，{minutes}分鐘後再次提醒您！"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 延後提醒設定失敗。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="週期性提醒或重要提醒不支援此延後功能。"))

    elif action == 'snooze_custom':
        event_record = get_event(event_id)
        if event_record and not event_record.is_recurring:
            selected_datetime_str = event.postback.params.get('datetime')
            if not selected_datetime_str:
                 selected_datetime_str = event.postback.params.get('time') or event.postback.params.get('date')

            if not selected_datetime_str:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法獲取選擇的時間。"))
                return

            try:
                if len(selected_datetime_str) > 16:
                    dt_obj = datetime.strptime(selected_datetime_str, "%Y-%m-%dT%H:%M:%S")
                else:
                    dt_obj = datetime.strptime(selected_datetime_str, "%Y-%m-%dT%H:%M")
                
                new_snooze_time = TAIPEI_TZ.localize(dt_obj)
            except Exception as e:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 時間格式錯誤"))
                return

            now_with_buffer = datetime.now(TAIPEI_TZ) - timedelta(minutes=1)
            if new_snooze_time <= now_with_buffer:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 延後時間必須在未來喔！"))
                return

            reset_reminder_sent_status(event_id)
            
            if safe_add_job_func(send_reminder_func, new_snooze_time, [event_id], f'reminder_{event_id}'):
                formatted_time = new_snooze_time.strftime('%Y/%m/%d %H:%M')
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⏰ 好的，已將提醒延後至 {formatted_time}！"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 延後設定失敗。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="此提醒無法延後。"))
            
    elif action == 'delete_single':
        result = delete_event_by_id(event_id, user_id)
        if result.get("status") == "success":
            job_id = f"recurring_{event_id}" if result.get("is_recurring") else f"reminder_{event_id}"
            if scheduler.get_job(job_id): scheduler.remove_job(job_id)
            events = get_all_events_by_user(user_id)
            if events:
                bubble = create_management_flex(events)
                flex_message = FlexSendMessage(alt_text="提醒管理面板", contents=bubble)
                line_bot_api.reply_message(event.reply_token, flex_message)
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🗑️ 已刪除，目前沒有其他提醒了。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 刪除失敗。"))


# --- Flex Message ---
def create_management_flex(events, page=1): # 增加 page 參數
    if not events: return None
    
    ITEMS_PER_PAGE = 10
    total_events = len(events)
    start_index = (page - 1) * ITEMS_PER_PAGE
    end_index = start_index + ITEMS_PER_PAGE
    
    display_events = events[start_index:end_index]
    
    if not display_events and page > 1:
        return create_management_flex(events, page=1)

    header = BoxComponent(layout='vertical', contents=[TextComponent(text=f'📋 提醒管理 ({page})', weight='bold', size='xl', color='#1DB446')])
    body_contents = []
    
    for event in display_events:
        if event.is_recurring:
            try:
                rule_parts = event.recurrence_rule.split('|')
                days_code = rule_parts[0].split(',')
                time_str = rule_parts[1]
                day_names = [WEEKDAYS_MAP.get(d, '') for d in days_code]
                time_text = f"每週{','.join(day_names)} {time_str}"
            except: time_text = "週期設定"
            icon = "🔄"
        else:
            time_text = event.event_datetime.astimezone().strftime('%Y/%m/%d %H:%M')
            icon = "⏰"
            if event.priority_level == 3: icon = "🔴"
            elif event.priority_level == 2: icon = "🟡"
            elif event.priority_level == 1: icon = "🟢"

        row = BoxComponent(
            layout='horizontal', margin='md', align_items='center',
            contents=[
                BoxComponent(layout='vertical', flex=1, contents=[TextComponent(text=f"{icon} {time_text}", size='xs', color='#aaaaaa'), TextComponent(text=event.event_content, size='sm', color='#555555', wrap=True)]),
                ButtonComponent(style='link', height='sm', width='40px', flex=0, action=PostbackAction(label='❌', data=f'action=delete_single&id={event.id}'))
            ]
        )
        body_contents.append(row)
        body_contents.append(SeparatorComponent(margin='sm'))

    # 翻頁按鈕
    footer_contents = []
    if end_index < total_events:
        next_page = page + 1
        btn_label = f"顯示更多 ({next_page})"
        btn_data = f'action=refresh_manage_panel&page={next_page}'
    else:
        btn_label = "回到第一頁"
        btn_data = 'action=refresh_manage_panel&page=1'

    footer_contents.append(ButtonComponent(style='primary', color='#333333', action=PostbackAction(label=btn_label, data=btn_data)))
    
    return BubbleContainer(header=header, body=BoxComponent(layout='vertical', contents=body_contents), footer=BoxComponent(layout='vertical', spacing='sm', contents=footer_contents))

def handle_list_reminders(event, line_bot_api):
    """處理 '提醒清單' 指令"""
    user_id = event.source.user_id
    events = get_all_events_by_user(user_id)
    if not events:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您目前沒有設定任何提醒，好清閒！🍵"))
        return
    bubble = create_management_flex(events, page=1)
    flex_message = FlexSendMessage(alt_text="提醒管理面板", contents=bubble)
    line_bot_api.reply_message(event.reply_token, flex_message)

def handle_delete_reminder_command(event, line_bot_api, scheduler):
    """(保留給文字指令刪除)"""
    user_id = event.source.user_id
    text = event.message.text.strip()
    try:
        event_id_to_delete = int(text.split(':', 1)[1])
    except (IndexError, ValueError):
        return
    result = delete_event_by_id(event_id_to_delete, user_id)
    if result.get("status") == "success":
        job_id = f"recurring_{event_id_to_delete}" if result.get("is_recurring") else f"reminder_{event_id_to_delete}"
        if scheduler.get_job(job_id): scheduler.remove_job(job_id)
        reply_text = "✅ 提醒已成功刪除。"
    else:
        reply_text = "🤔 找不到該提醒，或您沒有權限刪除。"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))