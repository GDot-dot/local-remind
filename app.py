# app.py (簡化版 - 移除海纜自動通知功能)

import os
import threading
from datetime import datetime, timedelta
from flask import Flask, request, abort
import logging
import atexit
import time

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, PostbackEvent,
    LocationMessage, ConfirmTemplate, PostbackTemplateAction, TemplateSendMessage,
    FlexSendMessage, QuickReply, QuickReplyButton, MessageAction,
    PostbackAction, ButtonsTemplate
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import pytz

from db import *
from features import reminder, location, scraper, recurring_reminder

app = Flask(__name__)
user_states = {}
logging.basicConfig(level=logging.INFO)
logging.getLogger('apscheduler').setLevel(logging.DEBUG) 
logger = logging.getLogger(__name__)

cable_data_cache = None
cache_timestamp = None
scraper_lock = threading.Lock()
CACHE_DURATION_MINUTES = 5

LINE_CHANNEL_ACCESS_TOKEN = '0jtuGMTolXKvvsQmb3CcAoD9JdkADsDKe+xsICSU9xmIcdyHmAFCTPY3H04nI1DeHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrBWb9QAkPDDLsVCs6Xny+t+6QEVFvx3hVDUTWTe7AxdtQdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = '74df866d9f3f4c47f3d5e86d67fcb673'

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
                # --- 不再加入海纜的排程任務 ---
                scheduler.start()
                logger.info("Scheduler started successfully (without cable checker).")
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
    # (此函式內容不變)
    try:
        with app.app_context():
            event = get_event(event_id)
            if not event:
                scheduler.remove_job(f"reminder_{event_id}", ignore_missing=True)
                scheduler.remove_job(f"recurring_{event_id}", ignore_missing=True)
                return
            if not event.is_recurring and event.reminder_sent: return
            destination_id, display_name, event_content = event.target_id, event.target_display_name, event.event_content
            if not event.is_recurring:
                event_dt = event.event_datetime.astimezone(TAIPEI_TZ)
                time_info = f"在 {event_dt.strftime('%Y/%m/%d %H:%M')} "
                template = ConfirmTemplate(
                    text=f"⏰ 提醒！\n\n@{display_name}\n記得{time_info}要「{event_content}」喔！",
                    actions=[
                        PostbackTemplateAction(label="確認收到", data=f"action=confirm_reminder&id={event_id}"),
                        PostbackTemplateAction(label="延後5分鐘", data=f"action=snooze_reminder&id={event_id}&minutes=5")
                    ])
            else:
                time_info = ""
                template = ButtonsTemplate(
                    text=f"⏰ 提醒！\n\n@{display_name}\n記得{time_info}要「{event_content}」喔！",
                    actions=[PostbackTemplateAction(label="OK", data=f"action=confirm_reminder&id={event_id}")])
            template_message = TemplateSendMessage(alt_text=f"提醒：{event_content}", template=template)
            line_bot_api.push_message(destination_id, template_message)
            logger.info(f"成功發送提醒 for event_id: {event_id}")
            if not event.is_recurring:
                mark_reminder_sent(event_id)
                scheduler.remove_job(f"reminder_{event_id}", ignore_missing=True)
                logger.info(f"已從排程器移除一次性任務 reminder_{event_id}")
    except Exception as e:
        logger.error(f"Error in send_reminder for event_id {event_id}: {e}", exc_info=True)

def safe_add_job(func, run_date, args, job_id):
    # (此函式內容不變)
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
    # 更新說明文字，移除訂閱相關指令
    help_text = """--- 提醒功能 ---
提醒 [誰] [日期] [時間] [事件]
週期提醒：設定每日/每週重複提醒。
提醒清單：查看與管理所有提醒。

--- 地點功能 ---
地點：透過按鈕管理您的地點記錄。

--- 資訊查詢 ---
海纜狀態：手動查詢最新狀態。

--- 通用指令 ---
取消：中斷目前所有操作。
"""
    line_bot_api.reply_message(reply_token, TextSendMessage(text=help_text))

@app.route("/callback", methods=['POST'])
def callback():
    # (此函式內容不變)
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
    try:
        if text == '取消':
            if user_id in user_states:
                del user_states[user_id]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="好的，已取消目前操作。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有進行中的操作喔！"))
            return
        if user_id in user_states:
            state_action = user_states[user_id].get('action')
            if state_action == 'awaiting_loc_name':
                location.handle_save_location_command(event, line_bot_api, user_states)
                return
            elif state_action == 'awaiting_recurring_content':
                recurring_reminder.handle_content_input(event, line_bot_api, user_states, scheduler, send_reminder, TAIPEI_TZ)
                return
        
        # --- 簡化後的指令分流 ---
        if text == '提醒清單':
            reminder.handle_list_reminders(event, line_bot_api)
        elif text.startswith('提醒'):
            reminder.handle_reminder_command(event, line_bot_api, TAIPEI_TZ)
        elif text == '週期提醒':
            recurring_reminder.start_flow(event, line_bot_api, user_states)
        elif text.startswith("刪除提醒ID:"):
            reminder.handle_delete_reminder_command(event, line_bot_api, scheduler)
        elif text == '海纜狀態':
            handle_cable_command(event)
        # --- 訂閱指令已被移除 ---
        elif text.startswith('刪除地點：'):
            location.handle_delete_location_command(event, line_bot_api)
        elif text.startswith('找地點'):
            location.handle_find_location_command(event, line_bot_api)
        elif text == '地點清單' or text.lower() == '地點':
            location.handle_list_locations_command(event, line_bot_api)
        elif text.lower() in ['help', '說明', '幫助']:
            send_help_message(event.reply_token)

    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 處理訊息時發生錯誤，請聯繫開發者。"))
        except: pass

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    # (此函式內容不變)
    try:
        location.handle_location_message(event, line_bot_api, user_states)
    except Exception as e:
        logger.error(f"Error in handle_location_message: {e}", exc_info=True)

@handler.add(PostbackEvent)
def handle_postback(event):
    # (此函式內容不變)
    try:
        data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
        action = data.get('action', '')
        user_id = event.source.user_id
        if action == 'cancel':
            if user_id in user_states: del user_states[user_id]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="操作已取消。"))
        elif action.startswith('loc_'):
            location.handle_location_postback(event, line_bot_api, user_states)
        elif action == 'delete_reminder_prompt':
            events = get_all_events_by_user(user_id)
            if not events:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您沒有可刪除的提醒。"))
                return
            items = []
            for e in events:
                label_text = reminder.format_event_for_display(e)[:20]
                action_text = f"刪除提醒ID:{e.id}"
                items.append(QuickReplyButton(action=MessageAction(label=label_text, text=action_text)))
            if len(items) > 12: items = items[:12]
            items.append(QuickReplyButton(action=PostbackAction(label="返回", data="action=cancel")))
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請點擊您想刪除的提醒：", quick_reply=QuickReply(items=items)))
        elif action in ['set_reminder', 'confirm_reminder', 'snooze_reminder']:
            reminder.handle_reminder_postback(event, line_bot_api, scheduler, send_reminder, safe_add_job, TAIPEI_TZ)
        elif action in ['toggle_weekday', 'set_recurring_time']:
            recurring_reminder.handle_postback(event, line_bot_api, user_states)
    except Exception as e:
        logger.error(f"Error in handle_postback: {e}", exc_info=True)

def format_cable_data(data):
    # (此函式內容不變)
    if not data: return "目前沒有偵測到任何海纜事件。"
    formatted_messages = ["【海纜事件最新狀態】"]
    for item in data:
        title, status, description = item.get("事件標題", "N/A"), item.get("狀態", "N/A"), item.get("描述", "N/A")
        timestamps = "\n".join(item.get("時間資訊", []))
        message = (f"\n- - - - - - - - - -\n🔹 標題: {title}\n🔸 狀態: {status}\n📃 描述: {description}\n🕒 時間:\n{timestamps}")
        formatted_messages.append(message)
    return "\n".join(formatted_messages)

def scrape_and_push(source_id, scraper_function):
    # (此函式內容不變)
    global cable_data_cache, cache_timestamp
    try:
        logger.info(f"背景開始執行海纜爬蟲，目標: {source_id}")
        data = scraper_function()
        message_text = format_cable_data(data) if data else "😥 抓取海纜資訊失敗"
        if data:
            cable_data_cache, cache_timestamp = data, datetime.now()
            logger.info("海纜資料快取已更新。")
        line_bot_api.push_message(source_id, TextSendMessage(text=message_text))
    except Exception as e:
        logger.error(f"scrape_and_push 執行失敗: {e}", exc_info=True)
        try: line_bot_api.push_message(source_id, TextSendMessage(text="執行爬蟲時發生內部錯誤。"))
        except: pass
    finally:
        if scraper_lock.locked(): scraper_lock.release()
        logger.info("爬蟲執行緒完成，鎖已釋放。")

def handle_cable_command(event):
    # (此函式內容不變)
    global cable_data_cache, cache_timestamp
    if cable_data_cache and (datetime.now() - cache_timestamp < timedelta(minutes=CACHE_DURATION_MINUTES)):
        logger.info("命中快取")
        message_text = format_cable_data(cable_data_cache)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message_text))
        return
    if not scraper_lock.acquire(blocking=False):
        logger.info("已有爬蟲在執行")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="查詢正在進行中，請稍候。"))
        return
    try:
        logger.info("快取失效，啟動背景爬蟲。")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="正在查詢最新的海纜狀態，請稍候..."))
        source = event.source
        source_id = getattr(source, f'{source.type}_id', None)
        if not source_id:
            logger.warning("無法獲取 source_id")
            scraper_lock.release()
            return
        scraper_thread = threading.Thread(target=scrape_and_push, args=(source_id, scraper.scrape_cable_map_info_robust))
        scraper_thread.start()
    except Exception as e:
        logger.error(f"啟動爬蟲時發生錯誤: {e}", exc_info=True)
        if scraper_lock.locked(): scraper_lock.release()

@app.route("/health")
def health_check():
    # (此函式內容不變)
    return {"status": "healthy", "scheduler_running": scheduler.running}

@app.route("/")
def index():
    # (此函式內容不變)
    return "LINE Bot Reminder Service is running!"

def cleanup():
    # (此函式內容不變)
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down successfully")
    cleanup_db()

atexit.register(cleanup)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)