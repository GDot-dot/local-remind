# app.py (乾淨版 - 僅保留提醒與地點功能)

import os
import threading
from datetime import datetime, timedelta
from flask import Flask, request, abort
import logging
import atexit

from features.ai_parser import parse_natural_language 
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, PostbackEvent,
    LocationMessage, ConfirmTemplate, PostbackTemplateAction, TemplateSendMessage,
    FlexSendMessage, QuickReply, QuickReplyButton, MessageAction,
    PostbackAction, ButtonsTemplate, DatetimePickerTemplateAction
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import pytz

from db import *
from db import DATABASE_URL
# 移除 scraper 匯入
from features import reminder, location, recurring_reminder

app = Flask(__name__)
user_states = {}
logging.basicConfig(level=logging.INFO)
logging.getLogger('apscheduler').setLevel(logging.DEBUG) 
logger = logging.getLogger(__name__)

# --- 本機設定 START ---
LINE_CHANNEL_ACCESS_TOKEN = '0jtuGMTolXKvvsQmb3CcAoD9JdkADsDKe+xsICSU9xmIcdyHmAFCTPY3H04nI1DeHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrBWb9QAkPDDLsVCs6Xny+t+6QEVFvx3hVDUTWTe7AxdtQdB04t89/1O/w1cDnyilFU=' # 請填寫
LINE_CHANNEL_SECRET = '74df866d9f3f4c47f3d5e86d67fcb673'
# --- 本機設定 END ---

TAIPEI_TZ = pytz.timezone('Asia/Taipei')
UTC_TZ = pytz.UTC

jobstores = {'default': SQLAlchemyJobStore(url=DATABASE_URL)}
executors = {'default': ThreadPoolExecutor(max_workers=5)}
job_defaults = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30}
scheduler_lock = threading.Lock()
scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone=TAIPEI_TZ)

def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully.")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

try:
    init_db()
    safe_start_scheduler()
    logger.info("Application initialized successfully")
except Exception as e:
    logger.error(f"Initialization failed: {e}")
    exit(1)

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

def send_reminder(event_id):
    try:
        with app.app_context():
            event = get_event(event_id)
            if not event:
                logger.warning(f"send_reminder: 找不到 event_id {event_id}，嘗試從排程器中移除。")
                if scheduler.get_job(f"reminder_{event_id}"): scheduler.remove_job(f"reminder_{event_id}")
                if scheduler.get_job(f"recurring_{event_id}"): scheduler.remove_job(f"recurring_{event_id}")
                return

            if not event.is_recurring and event.reminder_sent:
                logger.warning(f"send_reminder: event_id {event_id} 已發送，跳過。")
                return

            destination_id = event.target_id
            display_name = event.target_display_name
            event_content = event.event_content

            # --- 選擇樣板 ---
            if event.priority_level > 0:
                # 重要提醒
                from features.reminder import PRIORITY_RULES
                color = PRIORITY_RULES[event.priority_level]['color']
                icon = "🔴" if event.priority_level == 3 else "🟡" if event.priority_level == 2 else "🟢"
                template = ButtonsTemplate(
                    text=f"{icon} 重要提醒！\n\n@{display_name}\n記得要「{event_content}」！\n(如果不確認，我會繼續提醒)",
                    actions=[PostbackTemplateAction(label="收到，停止提醒", data=f"action=confirm_reminder&id={event_id}")]
                )
            elif not event.is_recurring:
                # 普通一次性
                event_dt = event.event_datetime.astimezone(TAIPEI_TZ)
                time_info = f"在 {event_dt.strftime('%Y/%m/%d %H:%M')} "
                template = ButtonsTemplate(
                    text=f"⏰ 提醒！\n\n@{display_name}\n記得{time_info}要「{event_content}」喔！",
                    actions=[
                        PostbackTemplateAction(label="確認收到", data=f"action=confirm_reminder&id={event_id}"),
                        PostbackTemplateAction(label="延後5分鐘", data=f"action=snooze_reminder&id={event_id}&minutes=5"),
                        DatetimePickerTemplateAction(label="自訂延後", data=f"action=snooze_custom&id={event_id}", mode="datetime")
                    ]
                )
            else:
                # 週期性
                time_info = ""
                template = ButtonsTemplate(
                    text=f"⏰ 提醒！\n\n@{display_name}\n記得{time_info}要「{event_content}」喔！",
                    actions=[
                        PostbackTemplateAction(label="OK", data=f"action=confirm_reminder&id={event_id}")
                    ]
                )

            template_message = TemplateSendMessage(alt_text=f"提醒：{event_content}", template=template)
            line_bot_api.push_message(destination_id, template_message)
            logger.info(f"成功發送提醒 for event_id: {event_id}")

            # --- 處理後續動作 ---
            if not event.is_recurring:
                if event.priority_level > 0 and event.remaining_repeats > 0:
                    # 重要提醒：重試
                    from features.reminder import PRIORITY_RULES
                    from db import decrease_remaining_repeats
                    decrease_remaining_repeats(event_id)
                    interval = PRIORITY_RULES[event.priority_level]['interval']
                    next_time = datetime.now(TAIPEI_TZ) + timedelta(minutes=interval)
                    safe_add_job(send_reminder, next_time, [event_id], f'reminder_{event_id}')
                    logger.info(f"重要提醒：已設定 {interval} 分鐘後重試。")
                else:
                    # 普通或次數用盡：標記完成並移除
                    mark_reminder_sent(event_id)
                    if scheduler.get_job(f"reminder_{event_id}"): scheduler.remove_job(f"reminder_{event_id}")
                    if event.priority_level > 0:
                         from db import delete_event_by_id
                         delete_event_by_id(event_id, event.creator_user_id)

    except Exception as e:
        logger.error(f"Error in send_reminder for event_id {event_id}: {e}", exc_info=True)

def safe_add_job(func, run_date, args, job_id):
    try:
        with scheduler_lock:
            if not scheduler.running: safe_start_scheduler()
            run_date_utc = run_date.astimezone(UTC_TZ)
            scheduler.add_job(func, 'date', run_date=run_date_utc, args=args, id=job_id, replace_existing=True)
            return True
    except Exception as e:
        logger.error(f"Error scheduling job {job_id}: {e}", exc_info=True)
        return False

def send_help_message(reply_token):
    help_text = """--- 提醒功能 ---
提醒 [誰] [日期] [時間] [事件]
重要提醒 [誰] [日期] [時間] [事件]
週期提醒：設定每日/每週重複提醒。
提醒清單：查看與管理所有提醒。

--- 地點功能 ---
地點：透過按鈕管理您的地點記錄。

--- 通用指令 ---
取消：中斷目前所有操作。
"""
    line_bot_api.reply_message(reply_token, TextSendMessage(text=help_text))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        logger.error(f"Error in callback handler: {e}", exc_info=True)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    # 取得來源類型: 'user', 'group', or 'room'
    source_type = event.source.type

    # 【重點】這裡開始 try，對應最後面的 except
    try:
        now_in_taipei = datetime.now(TAIPEI_TZ)

        # 1. 優先處理【取消】指令
        if text == '取消':
            if user_id in user_states:
                del user_states[user_id]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="好的，已取消目前操作。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有進行中的操作喔！"))
            return

        # 2. 處理【使用者狀態】(進行中的流程)
        if user_id in user_states:
            state_action = user_states[user_id].get('action')
            if state_action == 'awaiting_loc_name':
                location.handle_save_location_command(event, line_bot_api, user_states)
                return
            elif state_action == 'awaiting_recurring_content':
                recurring_reminder.handle_content_input(event, line_bot_api, user_states, scheduler, send_reminder, TAIPEI_TZ)
                return
            elif state_action == 'setting_priority':
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請點擊上方按鈕選擇重要程度。"))
                return
            elif state_action == 'setting_priority_time':
                 line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請點擊上方按鈕選擇時間。"))
                 return

        # 3. 處理【固定指令】
        if text == '提醒清單':
            reminder.handle_list_reminders(event, line_bot_api)
            return
        elif text.startswith('重要提醒'):
            reminder.handle_priority_reminder_command(event, line_bot_api, user_states, TAIPEI_TZ)
            return
        elif text.startswith('提醒'):
            reminder.handle_reminder_command(event, line_bot_api, TAIPEI_TZ, now_in_taipei)
            return
        elif text == '週期提醒':
            recurring_reminder.start_flow(event, line_bot_api, user_states)
            return
        elif text.startswith("刪除提醒ID:"):
            reminder.handle_delete_reminder_command(event, line_bot_api, scheduler)
            return
        elif text.startswith('刪除地點：'):
            location.handle_delete_location_command(event, line_bot_api)
            return
        elif text.startswith('找地點'):
            location.handle_find_location_command(event, line_bot_api)
            return
        elif text == '地點清單' or text.lower() == '地點':
            location.handle_list_locations_command(event, line_bot_api)
            return
        elif text.lower() in ['help', '說明', '幫助']:
            send_help_message(event.reply_token)
            return

        # --- 4. AI 智慧解析區塊 ---
        # 條件：訊息長度 > 1 且不是上面那些指令
        if len(text) > 1: 
            # 這裡使用一個內部的 try，避免 AI 錯誤影響主程式
            try:
                current_time_str = now_in_taipei.strftime('%Y-%m-%d %H:%M:%S')
                ai_result = parse_natural_language(text, current_time_str)

                if ai_result:
                    parsed_dt_str = ai_result['event_datetime']
                    parsed_content = ai_result['event_content']
                    
                    naive_dt = datetime.strptime(parsed_dt_str, "%Y-%m-%d %H:%M")
                    event_dt = TAIPEI_TZ.localize(naive_dt)

                    if event_dt <= now_in_taipei:
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="😅 AI 幫你算出來的時間已經過了，請再說一次。"))
                        return

                    # 顯示名稱
                    try:
                        profile = line_bot_api.get_profile(user_id)
                        display_name = profile.display_name
                    except:
                        display_name = "您"
                    
                    # 寫入資料庫
                    event_id = add_event(
                        creator_user_id=user_id,
                        target_id=user_id, # 預設 AI 建立的提醒都是私訊自己
                        target_type='user',
                        display_name=display_name,
                        content=parsed_content,
                        event_datetime=event_dt,
                        is_recurring=0
                    )

                    if event_id:
                        from features.reminder import QuickReply, QuickReplyButton, PostbackAction
                        quick_reply = QuickReply(items=[
                            QuickReplyButton(action=PostbackAction(label="10分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=10")),
                            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
                        ])
                        
                        reply_text = f"🤖 AI 設定成功！\n時間：{event_dt.strftime('%Y/%m/%d %H:%M')}\n事項：{parsed_content}"
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
                        return
            except Exception as e:
                logger.error(f"AI Logic Error: {e}")
                # AI 失敗就繼續往下走
        
        # --- 5. 最終防線 (解決群組太吵) ---
        if source_type == 'user':
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🤔 我聽不太懂，您可以試著說：「明天早上九點提醒我開會」或是輸入「說明」查看指令。"))
        else:
            # 群組裡聽不懂就安靜
            return

    # 【重點】這裡的 except 必須跟最上面的 try 對齊
    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        try:
            # 只有私訊才回報錯誤，避免群組洗頻
            if source_type == 'user':
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 處理訊息時發生錯誤，請聯繫開發者。"))
        except:
            pass

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    try:
        location.handle_location_message(event, line_bot_api, user_states)
    except Exception as e:
        logger.error(f"Error in handle_location_message: {e}", exc_info=True)

@handler.add(PostbackEvent)
def handle_postback(event):
    try:
        data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
        action = data.get('action', '')
        user_id = event.source.user_id
        
        if action == 'cancel':
            if user_id in user_states: del user_states[user_id]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="操作已取消。"))
        elif action.startswith('loc_'):
            location.handle_location_postback(event, line_bot_api, user_states)
        elif action in ['set_reminder', 'confirm_reminder', 'snooze_reminder', 'snooze_custom', 'set_priority', 'set_priority_time', 'delete_reminder_prompt', 'delete_single', 'refresh_manage_panel']:
            reminder.handle_reminder_postback(event, line_bot_api, scheduler, send_reminder, safe_add_job, TAIPEI_TZ, user_states)
        elif action in ['toggle_weekday', 'set_recurring_time']:
            recurring_reminder.handle_postback(event, line_bot_api, user_states)
    except Exception as e:
        logger.error(f"Error in handle_postback: {e}", exc_info=True)
        
@app.route("/health")
def health_check():
    return {"status": "healthy", "scheduler_running": scheduler.running}

@app.route("/")
def index():
    return "LINE Bot Reminder Service is running!"

# ---------------------------------
# 主程式進入點 (移除 multiprocessing)
# ---------------------------------
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)