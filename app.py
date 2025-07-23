# app.py (本機執行版本 - 最終整合版)

import os
import re
import threading
from datetime import datetime, timedelta
from flask import Flask, request, abort
import logging
import atexit

# 官方 Line Bot SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    QuickReply, QuickReplyButton, PostbackAction, PostbackEvent,
    ConfirmTemplate, TemplateSendMessage, PostbackTemplateAction
)

# 排程與日期工具
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from dateutil.parser import parse
import pytz

# 從我們自訂的 db 模組匯入
from db import init_db, get_db, Event, safe_db_operation, cleanup_db, DATABASE_URL

# ---------------------------------
# 初始化設定
# ---------------------------------
app = Flask(__name__)

# 設定日誌等級
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 本機設定 START ---
# 直接在此處設定你的憑證
LINE_CHANNEL_ACCESS_TOKEN = 'J450DanejGuyYScLjdWl8/MOzCJkJiGg3xyD9EnNSVv2YnbJhjsNctsZ7KLoZuYSHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrDnvJiuQqIN1AMcY5+oC4bRTeNOBPJTCLseJBE2pFmqugdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = '74df866d9f3f4c47f3d5e86d67fcb673'
# --- 本機設定 END ---

# 檢查必要變數
if 'YOUR_CHANNEL_ACCESS_TOKEN' in LINE_CHANNEL_ACCESS_TOKEN:
    logger.error("LINE_CHANNEL_ACCESS_TOKEN is not set in app.py")
    exit(1)
if 'YOUR_CHANNEL_SECRET' in LINE_CHANNEL_SECRET:
    logger.error("LINE_CHANNEL_SECRET is not set in app.py")
    exit(1)

# 設定時區常數
TAIPEI_TZ = pytz.timezone('Asia/Taipei')
UTC_TZ = pytz.UTC

# 排程器設定
jobstores = {'default': SQLAlchemyJobStore(url=DATABASE_URL)}
executors = {'default': ThreadPoolExecutor(max_workers=5)}
job_defaults = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30}
scheduler_lock = threading.Lock()
scheduler = BackgroundScheduler(
    jobstores=jobstores,
    executors=executors,
    job_defaults=job_defaults,
    timezone=UTC_TZ
)

# 安全啟動排程器
def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully with UTC timezone")
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}")

# 初始化
try:
    init_db()
    safe_start_scheduler()
    logger.info("Application initialized successfully")
except Exception as e:
    logger.error(f"Initialization failed: {e}")
    exit(1)

# 初始化 LINE Bot API
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ---------------------------------
# 資料庫輔助函式
# ---------------------------------
def add_event(creator_id, target_id, target_type, display_name, content, event_dt):
    def _add_event():
        db = next(get_db())
        try:
            new_event = Event(
                creator_user_id=creator_id,
                target_id=target_id,
                target_type=target_type,
                target_display_name=display_name,
                event_content=content,
                event_datetime=event_dt
            )
            db.add(new_event)
            db.commit()
            db.refresh(new_event)
            return new_event.id
        finally:
            db.close()
    try:
        return safe_db_operation(_add_event)
    except Exception as e:
        logger.error(f"Failed to add event: {e}")
        return None

def update_reminder_time(event_id, reminder_dt):
    def _update():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_time = reminder_dt
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_update)

def get_event(event_id):
    def _get():
        db = next(get_db())
        try:
            return db.query(Event).filter(Event.id == event_id).first()
        finally:
            db.close()
    return safe_db_operation(_get)

def mark_reminder_sent(event_id):
    def _mark():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_sent = 1
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_mark)

def reset_reminder_sent_status(event_id):
    def _reset():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_sent = 0
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_reset)

# ---------------------------------
# 排程任務
# ---------------------------------
def send_reminder(event_id):
    try:
        with app.app_context():
            logger.info(f"Executing reminder for event_id: {event_id}")
            event = get_event(event_id)
            if not event or event.reminder_sent:
                logger.warning(f"Skipping reminder for event_id {event_id}")
                return

            destination_id = event.target_id
            display_name = event.target_display_name
            event_content = event.event_content
            event_dt = event.event_datetime.astimezone(TAIPEI_TZ)

            logger.info(f"Sending reminder to {event.target_type} ({destination_id}) for event at {event_dt}")

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
            logger.info(f"Reminder sent successfully for event_id: {event_id}")
    except Exception as e:
        logger.error(f"Error in send_reminder for event_id {event_id}: {e}", exc_info=True)

# ---------------------------------
# 安全的排程器操作
# ---------------------------------
def safe_add_job(func, run_date, args, job_id):
    try:
        with scheduler_lock:
            if not scheduler.running:
                safe_start_scheduler()
            
            run_date_utc = run_date.astimezone(UTC_TZ)
            
            scheduler.add_job(func, 'date', run_date=run_date_utc, args=args, id=job_id, replace_existing=True)
            
            taipei_time = run_date_utc.astimezone(TAIPEI_TZ)
            logger.info(f"Successfully scheduled job: {job_id} at {taipei_time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            return True
    except Exception as e:
        logger.error(f"Error scheduling job {job_id}: {e}", exc_info=True)
        return False

# ---------------------------------
# 時間解析輔助函式
# ---------------------------------
def parse_datetime(datetime_str):
    try:
        # 嘗試標準格式
        return parse(datetime_str, yearfirst=False)
    except Exception:
        # 自訂格式解析 (處理 m/d 或 Y/m/d 的簡寫)
        now = datetime.now(TAIPEI_TZ)
        parts = datetime_str.replace('/', '-').split()
        date_part = parts[0]
        time_part = parts[1] if len(parts) > 1 else f"{now.hour}:{now.minute}"
        
        try:
            if date_part.count('-') == 1: # m-d
                date_part = f"{now.year}-{date_part}"
            
            full_dt_str = f"{date_part} {time_part}"
            return datetime.strptime(full_dt_str, '%Y-%m-%d %H:%M')
        except Exception as e:
            logger.error(f"Error parsing datetime '{datetime_str}': {e}")
            return None

# ---------------------------------
# Webhook 路由
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
        abort(500)
    return 'OK'

# ---------------------------------
# 核心訊息處理邏輯
# ---------------------------------
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    try:
        text = event.message.text.strip()
        creator_user_id = event.source.user_id

        source = event.source
        source_type = source.type
        if source_type == 'user':
            destination_id = source.user_id
        elif source_type == 'group':
            destination_id = source.group_id
        elif source_type == 'room':
            destination_id = source.room_id
        else:
            return

        # 新增：去除所有空格以支援無空格輸入
        text_no_space = text.replace(' ', '')
        # 新正則：支援無空格格式，且『誰』只吃到第一個日期前的內容
        match = re.match(r'^提醒(@?[^0-9明後]+)([0-9]{1,4}/[0-9]{1,2}/[0-9]{1,2}|[0-9]{1,2}/[0-9]{1,2}|明天|後天)([0-9]{1,2}:[0-9]{2})?(.+)$', text_no_space)
        if not match:
            if text.lower() in ['help', '說明', '幫助']:
                 help_text = """請使用以下格式：\n提醒 我 2025/07/15 17:20 做某事\n提醒 @某人 7/15 17:20 做某事 (群組內)\n提醒 我 明天 17:20 做某事\n\n支援的時間格式：\n- YYYY/MM/DD HH:MM\n- MM/DD HH:MM\n- 明天 HH:MM\n- 後天 HH:MM\n"""
                 line_bot_api.reply_message(event.reply_token, TextSendMessage(text=help_text))
            return

        who_to_remind_text, date_str, time_str, content = match.groups()
        content = content.strip()

        now_in_taipei = datetime.now(TAIPEI_TZ)
        if date_str == '明天':
            dt = now_in_taipei + timedelta(days=1)
        elif date_str == '後天':
            dt = now_in_taipei + timedelta(days=2)
        else:
            dt = now_in_taipei

        datetime_str = f"{date_str.replace('明天', dt.strftime('%Y/%m/%d')).replace('後天', dt.strftime('%Y/%m/%d'))} {time_str if time_str else ''}".strip()
        
        naive_dt = parse_datetime(datetime_str)
        if not naive_dt:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 時間格式有誤，請檢查後重新輸入。"))
            return
        
        event_dt = TAIPEI_TZ.localize(naive_dt)

        if event_dt <= datetime.now(TAIPEI_TZ):
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 提醒時間不能設定在過去喔！"))
            return

        if who_to_remind_text == '我':
            try:
                # 根據來源類型 (群組/聊天室/個人) 使用不同的 API 來獲取使用者名稱
                if source_type == 'group':
                    profile = line_bot_api.get_group_member_profile(event.source.group_id, creator_user_id)
                    target_display_name = profile.display_name
                elif source_type == 'room':
                    profile = line_bot_api.get_room_member_profile(event.source.room_id, creator_user_id)
                    target_display_name = profile.display_name
                else: # source_type == 'user'
                    profile = line_bot_api.get_profile(creator_user_id)
                    target_display_name = profile.display_name
            except LineBotApiError as e:
                # 如果 API 調用失敗 (例如在群組中，使用者未加機器人好友，或機器人權限不足)，則使用備用名稱
                logger.warning(f"無法獲取使用者 {creator_user_id} 的個人資料於 {source_type}。錯誤: {e}")
                target_display_name = "您"
        else:
            # 如果是指定 @某人 或其他文字，直接使用該文字
            target_display_name = who_to_remind_text

        event_id = add_event(creator_user_id, destination_id, source_type, target_display_name, content, event_dt)
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
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 處理請求時發生錯誤，請稍後再試。"))
        except:
            pass

# ---------------------------------
# Postback 事件處理
# ---------------------------------
@handler.add(PostbackEvent)
def handle_postback(event):
    try:
        data = dict(x.split('=') for x in event.postback.data.split('&'))
        action = data.get('action')
        
        if action == 'set_reminder':
            event_id = int(data['id'])
            reminder_type = data['type']
            
            event_record = get_event(event_id)
            if not event_record:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 找不到該提醒事件。"))
                return

            event_dt = event_record.event_datetime.astimezone(TAIPEI_TZ)
            reminder_dt = None
            
            if reminder_type == 'none':
                reply_msg_text = "✅ 好的，這個事件將不設定提醒。"
            else:
                value = int(data.get('val', 0))
                delta = timedelta(days=value if reminder_type == 'day' else 0,
                                  hours=value if reminder_type == 'hour' else 0,
                                  minutes=value if reminder_type == 'minute' else 0)
                
                if delta:
                    reminder_dt = event_dt - delta
                    if reminder_dt <= datetime.now(TAIPEI_TZ):
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 提醒時間已過，無法設定。"))
                        return
                    
                    if safe_add_job(send_reminder, reminder_dt, [event_id], f'reminder_{event_id}'):
                        reply_msg_text = f"✅ 設定完成！將於 {reminder_dt.strftime('%Y/%m/%d %H:%M')} 提醒您。"
                    else:
                        reply_msg_text = "❌ 設定提醒時發生錯誤。"
                else:
                    reply_msg_text = "❌ 未知的提醒類型。"
            
            if update_reminder_time(event_id, reminder_dt):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_msg_text))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 更新資料庫失敗。"))
        
        elif action == 'confirm_reminder':
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 提醒已確認收到！"))
            
        elif action == 'snooze_reminder':
            event_id = int(data['id'])
            minutes = int(data.get('minutes', 5))
            reset_reminder_sent_status(event_id)
            snooze_time = datetime.now(TAIPEI_TZ) + timedelta(minutes=minutes)
            if safe_add_job(send_reminder, snooze_time, [event_id], f'reminder_{event_id}'):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⏰ 好的，{minutes}分鐘後再次提醒您！"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 延後提醒設定失敗。"))
                
    except Exception as e:
        logger.error(f"Error in handle_postback: {e}", exc_info=True)
        try:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 處理 Postback 時發生錯誤。"))
        except:
            pass

# ---------------------------------
# 健康檢查與根路由
# ---------------------------------
@app.route("/health")
def health_check():
    return {
        "status": "healthy", 
        "scheduler_running": scheduler.running,
        "scheduled_jobs": len(scheduler.get_jobs()) if scheduler.running else 0,
        "current_taipei_time": datetime.now(TAIPEI_TZ).isoformat()
    }

@app.route("/")
def index():
    return "LINE Bot Reminder Service is running!"

# ---------------------------------
# 清理函式
# ---------------------------------
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
    # 為了讓 ngrok 能穩定連接，監聽在 0.0.0.0
    app.run(host='0.0.0.0', port=5000, debug=True)