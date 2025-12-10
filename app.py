# app.py (整合所有功能)

import os
import threading
import multiprocessing
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
    PostbackAction, ButtonsTemplate, DatetimePickerTemplateAction
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

# ... (海纜相關函式保持不變) ...
def format_cable_data(data):
    if not data: return "目前沒有偵測到任何海纜事件。"
    formatted_messages = ["【海纜事件最新狀態】"]
    for item in data:
        title, status, description = item.get("事件標題", "N/A"), item.get("狀態", "N/A"), item.get("描述", "N/A")
        timestamps = "\n".join(item.get("時間資訊", []))
        message = (f"\n- - - - - - - - - -\n🔹 標題: {title}\n🔸 狀態: {status}\n📃 描述: {description}\n🕒 時間:\n{timestamps}")
        formatted_messages.append(message)
    return "\n".join(formatted_messages)

def scraper_process_target(queue):
    from features import scraper
    try:
        logging.basicConfig(level=logging.INFO)
        logger.info("子进程：開始執行爬蟲...")
        data = scraper.scrape_cable_map_info_robust()
        logger.info(f"子进程：爬蟲執行完畢，得到資料: {'有' if data else '無'}")
        queue.put(data)
    except Exception as e:
        logger.error(f"子进程：爬蟲執行時發生嚴重錯誤: {e}", exc_info=True)
        queue.put(None)

def run_scraper_with_timeout(timeout=60):
    q = multiprocessing.Queue()
    p = multiprocessing.Process(target=scraper_process_target, args=(q,))
    p.start()
    p.join(timeout)
    if p.is_alive():
        logger.warning(f"警告：爬蟲进程執行超過 {timeout} 秒，將被強制終止。")
        p.terminate()
        p.join()
        return None
    if not q.empty(): return q.get()
    else: return None

def check_for_cable_updates():
    # ... (暫不使用)
    pass

def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                # scheduler.add_job(...) # 海纜自動排程已暫停
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
                    actions=[PostbackTemplateAction(label="OK", data=f"action=confirm_reminder&id={event_id}")]
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
                         # 重要提醒次數用盡後，自動刪除資料庫紀錄
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

--- 資訊查詢 ---
海纜狀態：手動查詢最新狀態。

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

    try:
        now_in_taipei = datetime.now(TAIPEI_TZ)

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
            # --- 新增：處理重要提醒的狀態 ---
            elif state_action == 'setting_priority':
                # 這部分其實不需要文字輸入，而是等待 Postback
                # 但如果使用者輸入了文字，可以提示他去按按鈕
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請點擊上方按鈕選擇重要程度。"))
                return
            elif state_action == 'setting_priority_time':
                 line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請點擊上方按鈕選擇時間。"))
                 return

        # --- 指令分流 ---
        if text == '提醒清單':
            reminder.handle_list_reminders(event, line_bot_api)
        elif text.startswith('重要提醒'): # 新增
            reminder.handle_priority_reminder_command(event, line_bot_api, user_states, TAIPEI_TZ)
        elif text.startswith('提醒'):
            reminder.handle_reminder_command(event, line_bot_api, TAIPEI_TZ, now_in_taipei)
        elif text == '週期提醒':
            recurring_reminder.start_flow(event, line_bot_api, user_states)
        elif text.startswith("刪除提醒ID:"):
            reminder.handle_delete_reminder_command(event, line_bot_api, scheduler)
        elif text == '海纜狀態':
            handle_cable_command(event)
        elif text == '訂閱海纜通知':
            source = event.source
            sub_id = getattr(source, f'{source.type}_id', None)
            if sub_id:
                result = add_cable_subscriber(sub_id, source.type)
                reply_text = {"success": "✅ 成功訂閱！", "already_subscribed": "ℹ️ 您已經訂閱過了！"}.get(result, "❌ 訂閱失敗")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
        elif text == '取消訂閱海纜通知':
            source = event.source
            sub_id = getattr(source, f'{source.type}_id', None)
            if sub_id:
                result = remove_cable_subscriber(sub_id)
                reply_text = {"success": "✅ 已取消訂閱。", "not_found": "ℹ️ 您尚未訂閱。"}.get(result, "❌ 操作失敗")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))
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
    # (內容同前)
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
        elif action == 'delete_reminder_prompt':
            # 注意：這裡的邏輯已經包含在 reminder.handle_reminder_postback 中了
            reminder.handle_reminder_postback(event, line_bot_api, scheduler, send_reminder, safe_add_job, TAIPEI_TZ, user_states)

        elif action in ['set_reminder', 'confirm_reminder', 'snooze_reminder', 'snooze_custom', 'set_priority', 'set_priority_time', 'delete_reminder_prompt', 'delete_single', 'refresh_manage_panel']:
            # 統一交給 reminder.py 處理，注意多傳了 user_states
            reminder.handle_reminder_postback(event, line_bot_api, scheduler, send_reminder, safe_add_job, TAIPEI_TZ, user_states)
        elif action in ['toggle_weekday', 'set_recurring_time']:
            recurring_reminder.handle_postback(event, line_bot_api, user_states)
    except Exception as e:
        logger.error(f"Error in handle_postback: {e}", exc_info=True)

def scrape_and_push(source_id, scraper_function):
    # (內容同前)
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
    # (內容同前)
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
    multiprocessing.freeze_support()
    app.run(host='0.0.0.0', port=5000, debug=False)