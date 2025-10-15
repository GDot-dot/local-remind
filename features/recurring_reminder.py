# features/recurring_reminder.py

from linebot.models import (
    TextSendMessage, FlexSendMessage
)
from db import add_event

WEEKDAYS_MAP = {"MON": "一", "TUE": "二", "WED": "三", "THU": "四", "FRI": "五", "SAT": "六", "SUN": "日"}

def _create_flex_message(selected_days):
    """根據當前選擇的星期，動態生成 Flex Message"""
    flex_json = {
      "type": "bubble",
      "body": {
        "type": "box",
        "layout": "vertical",
        "contents": [
          {"type": "text", "text": "設定週期提醒", "weight": "bold", "size": "xl"},
          {"type": "text", "text": "1. 選擇要重複提醒的星期 (可多選):", "margin": "lg", "wrap": True},
          {"type": "box", "layout": "horizontal", "spacing": "sm", "margin": "md", "contents": []}, # Row 1
          {"type": "box", "layout": "horizontal", "spacing": "sm", "margin": "sm", "contents": []}, # Row 2
          {"type": "separator", "margin": "xl"},
          {"type": "text", "text": "2. 選擇提醒時間:", "margin": "lg"},
          {
            "type": "button",
            "action": {
              "type": "datetimepicker", "label": "點我選擇時間",
              "data": "action=set_recurring_time", "mode": "time", "initial": "09:00"
            },
            "height": "sm", "style": "primary", "margin": "md"
          }
        ]
      }
    }

    buttons = []
    for day_code, day_name in WEEKDAYS_MAP.items():
        style = "primary" if day_code in selected_days else "secondary"
        buttons.append({
            "type": "button",
            "action": {"type": "postback", "label": day_name, "data": f"action=toggle_weekday&day={day_code}"},
            "height": "sm", "style": style, "flex": 1
        })
    
    flex_json["body"]["contents"][2]["contents"] = buttons[:4]
    flex_json["body"]["contents"][3]["contents"] = buttons[4:]
    
    return flex_json

def start_flow(event, line_bot_api, user_states):
    """開始設定流程"""
    user_id = event.source.user_id
    user_states[user_id] = {"action": "setting_recurring", "selected_days": set(), "selected_time": None}
    
    flex_contents = _create_flex_message(set())
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text="好的，我們來設定一個新的週期提醒。"),
            FlexSendMessage(alt_text="設定週期提醒", contents=flex_contents)
        ]
    )

def handle_postback(event, line_bot_api, user_states):
    """處理週期提醒相關的 Postback 事件"""
    user_id = event.source.user_id
    data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
    action = data.get('action')

    if user_id not in user_states or user_states[user_id].get("action") not in ["setting_recurring", "awaiting_recurring_content"]:
        return

    state = user_states[user_id]

    if action == 'toggle_weekday':
        if state.get("action") != "setting_recurring": return
        day_to_toggle = data.get('day')
        if day_to_toggle in state["selected_days"]:
            state["selected_days"].remove(day_to_toggle)
        else:
            state["selected_days"].add(day_to_toggle)
        
        flex_contents = _create_flex_message(state["selected_days"])
        line_bot_api.reply_message(
            event.reply_token,
            FlexSendMessage(alt_text="更新星期選擇", contents=flex_contents)
        )

    elif action == 'set_recurring_time':
        if state.get("action") != "setting_recurring": return
        selected_time = event.postback.params.get('time')
        state["selected_time"] = selected_time
        
        if not state["selected_days"]:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請先選擇至少一個星期！"))
            return

        user_states[user_id]['action'] = 'awaiting_recurring_content'
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"好的，時間設定為 {selected_time}。\n現在，請直接輸入要提醒的【事件內容】：")
        )

def handle_content_input(event, line_bot_api, user_states, scheduler, send_reminder_func, TAIPEI_TZ):
    """處理使用者輸入的提醒內容，並完成最終設定"""
    from linebot.exceptions import LineBotApiError
    user_id = event.source.user_id
    content = event.message.text.strip()
    state = user_states[user_id]

    days_str = ",".join(sorted(list(state["selected_days"])))
    hour, minute = state["selected_time"].split(':')
    
    rule_str = f"{days_str}|{state['selected_time']}"

    # 獲取使用者名稱
    try:
        profile = line_bot_api.get_profile(user_id)
        display_name = profile.display_name
    except LineBotApiError:
        display_name = "您"
    
    # 取得 target_id
    source = event.source
    target_id = getattr(source, f'{source.type}_id', user_id)

    event_id = add_event(
        creator_user_id=user_id,
        target_id=target_id,
        target_type=source.type,
        display_name=display_name,
        content=content,
        event_datetime=None,
        is_recurring=1,
        recurrence_rule=rule_str
    )

    if not event_id:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 建立週期提醒失敗，請稍后再试。"))
        del user_states[user_id]
        return

    job_id = f"recurring_{event_id}"
    scheduler.add_job(
        send_reminder_func,
        trigger='cron',
        args=[event_id],
        id=job_id,
        day_of_week=days_str.lower(),
        hour=int(hour),
        minute=int(minute),
        replace_existing=True,
        misfire_grace_time=60
    )

    del user_states[user_id]
    
    selected_day_names = [WEEKDAYS_MAP[day] for day in sorted(list(state["selected_days"]))]
    reply_text = (
        f"✅ 設定完成！\n"
        f"將在每【周{', '.join(selected_day_names)}】的【{state['selected_time']}】\n"
        f"提醒您：『{content}』"
    )
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))