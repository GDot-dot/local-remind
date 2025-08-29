# app.py (最終重構版 - 總機模式)

import os
import threading
from datetime import datetime, timedelta
from flask import Flask, request, abort
import logging
import atexit

# 官方 Line Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, PostbackEvent,
    LocationMessage, ConfirmTemplate, PostbackTemplateAction, TemplateSendMessage
)

# 排程與日期工具
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
import pytz

# 從我們自訂的 db 模組匯入
from db import init_db, cleanup_db, DATABASE_URL, get_event, mark_reminder_sent

# --- 從 features 模組匯入功能函式 ---
from features import reminder, location

# ---------------------------------
# 初始化設定
# ---------------------------------
app = Flask(__name__)
user_states = {}
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 本機設定 START ---
LINE_CHANNEL_ACCESS_TOKEN = '0jtuGMTolXKvvsQmb3CcAoD9JdkADsDKe+xsICSU9xmIcdyHmAFCTPY3H04nI1DeHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrBWb9QAkPDDLsVCs6Xny+t+6QEVFvx3hVDUTWTe7AxdtQdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = '74df866d9f3f4c47f3d5e86d67fcb673'
# --- 本機設定 END ---

TAIPEI_TZ = pytz.timezone('Asia/Taipei')
UTC_TZ = pytz.UTC

jobstores = {'default': SQLAlchemyJobStore(url=DATABASE_URL)}
executors = {'default': ThreadPoolExecutor(max_workers=5)}
job_defaults = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30}
scheduler_lock = threading.Lock()
scheduler = BackgroundScheduler(
    jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone=UTC_TZ
)

def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully")
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

# ---------------------------------
# 輔助函式 (排程、通用)
# ---------------------------------
def send_reminder(event_id):
    """這個函式需要 app_context，所以保留在主檔案"""
    try:
        with app.app_context():
            event = get_event(event_id)
            if not event or event.reminder_sent: return
            destination_id, display_name, event_content = event.target_id, event.target_display_name, event.event_content
            event_dt = event.event_datetime.astimezone(TAIPEI_TZ)
            confirm_template = ConfirmTemplate(
                text=f"⏰ 提醒！\n\n@{display_name}\n記得在 {event_dt.strftime('%Y/%m/%d %H:%M')} 要「{event_content}」喔！",
                actions=[
                    PostbackTemplateAction(label="確認收到", data=f"action=confirm_reminder&id={event_id}"),
                    PostbackTemplateAction(label="延後5分鐘", data=f"action=snooze_reminder&id={event_id}&minutes=5")
                ]
            )
            template_message = TemplateSendMessage(alt_text=f"提醒：{event_content}", template=confirm_template)
            line_bot_api.push_message(destination_id, template_message)
            mark_reminder_sent(event_id)
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
格式：提醒 [誰] [日期] [時間] [事件]
(注意：誰和日期之間必須有空格)
範例：提醒 我 明天 10:30 開會

--- 地點功能 ---
請輸入「地點」或「地點清單」
即可透過按鈕管理您的地點記錄。

--- 通用指令 ---
在任何操作過程中，可隨時輸入「取消」
來中斷目前操作。
"""
    line_bot_api.reply_message(reply_token, TextSendMessage(text=help_text))


# ---------------------------------
# Webhook 路由與核心處理邏輯
# ---------------------------------
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

    try:
        if text == '取消':
            if user_id in user_states:
                del user_states[user_id]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="好的，已取消目前操作。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有進行中的操作喔！"))
            return

        if user_id in user_states and user_states[user_id]['action'] == 'awaiting_loc_name':
            location.handle_save_location_command(event, line_bot_api, user_states)
            return

        if text.startswith('提醒'):
            reminder.handle_reminder_command(event, line_bot_api, TAIPEI_TZ)
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
    try:
        location.handle_location_message(event, line_bot_api, user_states)
    except Exception as e:
        logger.error(f"Error in handle_location_message: {e}", exc_info=True)

@handler.add(PostbackEvent)
def handle_postback(event):
    """總機：根據 action 關鍵字分派 Postback 事件"""
    try:
        data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
        action = data.get('action', '')
        user_id = event.source.user_id
        
        if action == 'cancel':
            if user_id in user_states: del user_states[user_id]
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="操作已取消。"))

        elif action.startswith('loc_'):
            location.handle_location_postback(event, line_bot_api, user_states)
        
        elif action in ['set_reminder', 'confirm_reminder', 'snooze_reminder']:
            reminder.handle_reminder_postback(event, line_bot_api, send_reminder, safe_add_job, TAIPEI_TZ)

    except Exception as e:
        logger.error(f"Error in handle_postback: {e}", exc_info=True)

# ---------------------------------
# 健康檢查與清理
# ---------------------------------
@app.route("/health")
def health_check():
    return {"status": "healthy", "scheduler_running": scheduler.running}

@app.route("/")
def index():
    return "LINE Bot Reminder Service is running!"

def cleanup():
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler shut down successfully")
    cleanup_db()

atexit.register(cleanup)

# ---------------------------------
# 主程式進入點
# ---------------------------------
if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=True)