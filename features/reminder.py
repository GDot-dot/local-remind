# features/reminder.py (最終完整版)

import re
from datetime import datetime, timedelta
from linebot.exceptions import LineBotApiError
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton, PostbackAction, MessageAction
)
from db import (
    add_event, get_event, update_reminder_time, reset_reminder_sent_status,
    get_all_events_by_user, delete_event_by_id
)

WEEKDAYS_MAP = {"MON": "一", "TUE": "二", "WED": "三", "THU": "四", "FRI": "五", "SAT": "六", "SUN": "日"}

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

def handle_reminder_command(event, line_bot_api, TAIPEI_TZ):
    """處理'提醒'指令"""
    try:
        text = event.message.text.strip()
        creator_user_id = event.source.user_id
        source = event.source
        source_type = source.type
        destination_id = getattr(source, f'{source.type}_id', None)
        if not destination_id: return
        match = re.match(r'^提醒\s*(@?[^\s]+)\s+([0-9]{1,4}/[0-9]{1,2}/[0-9]{1,2}|[0-9]{1,2}/[0-9]{1,2}|今天|明天|後天)\s*([0-9]{1,2}:[0-9]{2})?\s*(.+)$', text)
        if not match:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 提醒格式錯誤。\n請確認 [誰] 和 [日期] 之間有空格。"))
            return
        who_to_remind_text, date_str, time_str, content = match.groups()
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
        if event_dt <= datetime.now(TAIPEI_TZ):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 提醒時間不能設定在過去喔！"))
            return
        target_display_name = who_to_remind_text
        if who_to_remind_text == '我':
            try:
                if source_type == 'group':
                    profile = line_bot_api.get_group_member_profile(destination_id, creator_user_id)
                elif source_type == 'room':
                    profile = line_bot_api.get_room_member_profile(destination_id, creator_user_id)
                else:
                    profile = line_bot_api.get_profile(creator_user_id)
                target_display_name = profile.display_name
            except LineBotApiError:
                target_display_name = "您"
        event_id = add_event(
            creator_user_id=creator_user_id, target_id=destination_id, target_type=source_type,
            display_name=target_display_name, content=content, event_datetime=event_dt,
            is_recurring=0, recurrence_rule=None, next_run_time=None)
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

def handle_reminder_postback(event, line_bot_api, scheduler, send_reminder_func, safe_add_job_func, TAIPEI_TZ):
    from datetime import datetime, timedelta
    from linebot.models import TextSendMessage
    data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
    action = data.get('action')
    event_id = int(data.get('id', 0))
    user_id = event.source.user_id
    if not event_id: return
    if action == 'confirm_reminder':
        event_record = get_event(event_id)
        if event_record:
            if not event_record.is_recurring:
                result = delete_event_by_id(event_id, user_id)
                if result.get("status") == "success":
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 任務已完成並移除！"))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 收到確認，但移除資料時出錯。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 提醒已確認收到！"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="找不到該提醒，可能已被處理。"))
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
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="週期性提醒不支援延後功能。"))
            
def format_event_for_display(event):
    if event.is_recurring:
        try:
            rule_parts = event.recurrence_rule.split('|')
            days_code, time_str = rule_parts[0].split(','), rule_parts[1]
            day_names = [WEEKDAYS_MAP.get(d, '') for d in days_code]
            schedule_desc = f"每週{','.join(day_names)} {time_str}"
        except:
            schedule_desc = "週期設定"
    else:
        schedule_desc = event.event_datetime.astimezone().strftime('%Y/%m/%d %H:%M')
    return f"【{schedule_desc}】{event.event_content}"

def handle_list_reminders(event, line_bot_api):
    user_id = event.source.user_id
    events = get_all_events_by_user(user_id)
    if not events:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您目前沒有設定任何提醒。"))
        return
    reminders_list = ["您設定的提醒清單："]
    for e in events:
        reminders_list.append(f"∙ {format_event_for_display(e)}")
    reply_text = "\n".join(reminders_list)
    quick_reply = QuickReply(items=[
        QuickReplyButton(action=PostbackAction(label="⛔ 刪除提醒", data="action=delete_reminder_prompt"))
    ])
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))

def handle_delete_reminder_command(event, line_bot_api, scheduler):
    user_id = event.source.user_id
    text = event.message.text.strip()
    try:
        event_id_to_delete = int(text.split(':', 1)[1])
    except (IndexError, ValueError):
        return
    result = delete_event_by_id(event_id_to_delete, user_id)
    if result.get("status") == "success":
        job_id = f"recurring_{event_id_to_delete}" if result.get("is_recurring") else f"reminder_{event_id_to_delete}"
        if job_id and scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
        reply_text = "✅ 提醒已成功刪除。"
    else:
        reply_text = "🤔 找不到該提醒，或您沒有權限刪除。"
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))