import os
import threading
from datetime import datetime, timedelta
from flask import Flask, request, abort
import logging
import pytz

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, PostbackEvent,
    LocationMessage, TemplateSendMessage, ButtonsTemplate,
    PostbackTemplateAction, DatetimePickerTemplateAction
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor

# 匯入自定義模組
from db import *
from db import DATABASE_URL
from features import reminder, location, recurring_reminder
from features.ai_parser import parse_natural_language 

# =========== 🔎 開機檢查 ===========
print("="*50)
print("🚀 系統啟動，正在檢查環境變數...")
if "DATABASE_URL" in os.environ:
    print("✅ DATABASE_URL: 存在")
else:
    print("❌ DATABASE_URL: 消失了！")
print("="*50)
# =================================

app = Flask(__name__)
user_states = {}
logging.basicConfig(level=logging.INFO)
logging.getLogger('apscheduler').setLevel(logging.DEBUG) 
logger = logging.getLogger(__name__)

# --- 本機設定 (請確認 Fly.io Secrets 已設定，這裡僅為 fallback) ---
LINE_CHANNEL_ACCESS_TOKEN = '0jtuGMTolXKvvsQmb3CcAoD9JdkADsDKe+xsICSU9xmIcdyHmAFCTPY3H04nI1DeHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrBWb9QAkPDDLsVCs6Xny+t+6QEVFvx3hVDUTWTe7AxdtQdB04t89/1O/w1cDnyilFU=' # 請填寫
LINE_CHANNEL_SECRET = '74df866d9f3f4c47f3d5e86d67fcb673'

TAIPEI_TZ = pytz.timezone('Asia/Taipei')
UTC_TZ = pytz.UTC

# 排程器設定 (包含斷線重連機制)
jobstores = {
    'default': SQLAlchemyJobStore(
        url=DATABASE_URL,
        engine_options={
            "pool_pre_ping": True,
            "pool_recycle": 300
        }
    )
}
executors = {'default': ThreadPoolExecutor(max_workers=5)}
job_defaults = {'coalesce': True, 'max_instances': 1, 'misfire_grace_time': 30}
scheduler_lock = threading.Lock()
scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors, job_defaults=job_defaults, timezone=TAIPEI_TZ)

def restore_jobs():
    """從資料庫讀取舊任務並重新排程"""
    with app.app_context():
        from db import get_db, Event
        db = next(get_db())
        try:
            logger.info("♻️ 正在檢查並修復排程任務...")
            # 1. 週期性提醒
            recurring_events = db.query(Event).filter(Event.is_recurring == 1).all()
            # 2. 未發送的一次性提醒
            now = datetime.now(TAIPEI_TZ)
            future_events = db.query(Event).filter(
                Event.reminder_sent == 0,
                Event.is_recurring == 0,
                Event.reminder_time > now 
            ).all()

            all_events = recurring_events + future_events
            restored_count = 0

            for event in all_events:
                job_id = f"recurring_{event.id}" if event.is_recurring else f"reminder_{event.id}"
                if not scheduler.get_job(job_id):
                    try:
                        if event.is_recurring:
                            rule_parts = event.recurrence_rule.split('|')
                            days_code = rule_parts[0].lower()
                            time_parts = rule_parts[1].split(':')
                            scheduler.add_job(
                                send_reminder, trigger='cron', args=[event.id], id=job_id,
                                day_of_week=days_code, hour=int(time_parts[0]), minute=int(time_parts[1]),
                                timezone=TAIPEI_TZ, replace_existing=True
                            )
                        else:
                            run_date = event.reminder_time.astimezone(TAIPEI_TZ)
                            scheduler.add_job(
                                send_reminder, 'date', run_date=run_date, args=[event.id], id=job_id,
                                replace_existing=True
                            )
                        restored_count += 1
                    except Exception as e:
                        logger.error(f"  ! 修復 ID {event.id} 失敗: {e}")
            logger.info(f"✅ 排程修復完成！共重新註冊 {restored_count} 個任務。")
        except Exception as e:
            logger.error(f"❌ 排程修復錯誤: {e}")
        finally:
            db.close()

def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully.")
                threading.Thread(target=restore_jobs).start()
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
                if scheduler.get_job(f"reminder_{event_id}"): scheduler.remove_job(f"reminder_{event_id}")
                return

            if not event.is_recurring and event.reminder_sent:
                return

            destination_id = event.target_id
            display_name = event.target_display_name
            event_content = event.event_content

            if event.priority_level > 0:
                from features.reminder import PRIORITY_RULES
                icon = "🔴" if event.priority_level == 3 else "🟡" if event.priority_level == 2 else "🟢"
                template = ButtonsTemplate(
                    text=f"{icon} 重要提醒！\n\n@{display_name}\n記得要「{event_content}」！",
                    actions=[PostbackTemplateAction(label="收到，停止提醒", data=f"action=confirm_reminder&id={event_id}")]
                )
            elif not event.is_recurring:
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
                template = ButtonsTemplate(
                    text=f"⏰ 提醒！\n\n@{display_name}\n記得要「{event_content}」喔！",
                    actions=[PostbackTemplateAction(label="OK", data=f"action=confirm_reminder&id={event_id}")]
                )

            line_bot_api.push_message(destination_id, TemplateSendMessage(alt_text=f"提醒：{event_content}", template=template))
            logger.info(f"成功發送提醒 for event_id: {event_id}")

            if not event.is_recurring:
                if event.priority_level > 0 and event.remaining_repeats > 0:
                    from features.reminder import PRIORITY_RULES
                    decrease_remaining_repeats(event_id)
                    interval = PRIORITY_RULES[event.priority_level]['interval']
                    next_time = datetime.now(TAIPEI_TZ) + timedelta(minutes=interval)
                    safe_add_job(send_reminder, next_time, [event_id], f'reminder_{event_id}')
                else:
                    mark_reminder_sent(event_id)
                    if scheduler.get_job(f"reminder_{event_id}"): scheduler.remove_job(f"reminder_{event_id}")
                    if event.priority_level > 0:
                         delete_event_by_id(event_id, event.creator_user_id)

    except Exception as e:
        logger.error(f"Error in send_reminder: {e}", exc_info=True)

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
    help_text = "--- 提醒功能 ---\n提醒 [時間] [事項]\n重要提醒 [時間] [事項]\n週期提醒\n提醒清單\n\n--- 地點功能 ---\n地點\n找地點 [名稱]\n\n--- 其他 ---\n取消"
    line_bot_api.reply_message(reply_token, TextSendMessage(text=help_text))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    except Exception as e: logger.error(f"Callback error: {e}", exc_info=True)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    user_id = event.source.user_id
    source_type = event.source.type

    try:
        now_in_taipei = datetime.now(TAIPEI_TZ)

        # 1. 優先處理【取消】
        if text == '取消':
            if user_id in user_states:
                del user_states[user_id]
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="好的，已取消目前操作。"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有進行中的操作喔！"))
            return

        # 2. 處理【使用者狀態】
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
                 
            # --- 編輯內容處理 ---
            elif state_action == 'awaiting_edit_content':
                event_id = user_states[user_id].get('event_id')
                original_content = user_states[user_id].get('original_content')
                
                if text.startswith('+') or text.startswith('＋'):
                    append_text = text[1:].strip()
                    new_content = f"{original_content} ({append_text})"
                    mode_msg = "補充"
                else:
                    new_content = text
                    mode_msg = "修改"
                
                from db import update_event_content
                if update_event_content(event_id, new_content):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已{mode_msg}內容為：\n{new_content}"))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 更新失敗，找不到該提醒。"))
                
                del user_states[user_id]
                return
            # ----------------

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

        # 4. AI 解析
        time_keywords = ['明天', '後天', '今天', '下週', '下周', '禮拜', '星期', '點', '分', '早上', '下午', '晚上', '中午', '半', '提醒', '幫我', '記得', '後', '買']
        is_potential_reminder = any(k in text for k in time_keywords) or any(char.isdigit() for char in text)

        if len(text) > 1 and is_potential_reminder: 
            try:
                current_time_str = now_in_taipei.strftime('%Y-%m-%d %H:%M:%S')
                ai_result = parse_natural_language(text, current_time_str)

                if ai_result:
                    parsed_dt_str = ai_result['event_datetime']
                    parsed_content = ai_result['event_content']
                    naive_dt = datetime.strptime(parsed_dt_str, "%Y-%m-%d %H:%M")
                    event_dt = TAIPEI_TZ.localize(naive_dt)

                    if event_dt <= now_in_taipei:
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="😅 AI 算出的時間已經過了，請再說一次。"))
                        return

                    try:
                        profile = line_bot_api.get_profile(user_id)
                        display_name = profile.display_name
                    except:
                        display_name = "您"
                    
                    target_id = user_id
                    if source_type == 'group': target_id = event.source.group_id
                    elif source_type == 'room': target_id = event.source.room_id
                    
                    event_id = add_event(creator_user_id=user_id, target_id=target_id, target_type=source_type, display_name=display_name, content=parsed_content, event_datetime=event_dt, is_recurring=0)

                    if event_id:
                        from features.reminder import QuickReply, QuickReplyButton, PostbackAction
                        quick_reply = QuickReply(items=[
                            QuickReplyButton(action=PostbackAction(label="10分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=10")),
                            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
                            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
                            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
                        ])
                        reply_text = f"🤖 AI 設定成功！\n\n時間：{event_dt.strftime('%Y/%m/%d %H:%M')}\n事項：{parsed_content}\n\n要提早提醒嗎？"
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text, quick_reply=quick_reply))
                        return
            except Exception as e:
                logger.error(f"AI Logic Error: {e}")
        
        # 5. 最終防線
        if source_type == 'user':
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🤔 我聽不太懂，請輸入「說明」查看指令。"))

    except Exception as e:
        logger.error(f"Error in handle_message: {e}", exc_info=True)
        try:
            if source_type == 'user': line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 系統錯誤，請聯繫開發者。"))
        except: pass

@handler.add(MessageEvent, message=LocationMessage)
def handle_location_message(event):
    location.handle_location_message(event, line_bot_api, user_states)

@handler.add(PostbackEvent)
def handle_postback(event):
    data = dict(x.split('=', 1) for x in event.postback.data.split('&'))
    action = data.get('action', '')
    user_id = event.source.user_id
    
    if action == 'cancel':
        if user_id in user_states: del user_states[user_id]
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="操作已取消。"))
    elif action.startswith('loc_'):
        location.handle_location_postback(event, line_bot_api, user_states)
    elif action in ['set_reminder', 'confirm_reminder', 'snooze_reminder', 'snooze_custom', 'set_priority', 'set_priority_time', 'delete_reminder_prompt', 'delete_single', 'refresh_manage_panel', 'edit_prompt', 'edit_content_start', 'edit_time_confirm']:
        # 記得加入 'edit_prompt' 等新的 action 到這裡
        reminder.handle_reminder_postback(event, line_bot_api, scheduler, send_reminder, safe_add_job, TAIPEI_TZ, user_states)
    elif action in ['toggle_weekday', 'set_recurring_time']:
        recurring_reminder.handle_postback(event, line_bot_api, user_states)

@app.route("/health")
def health_check(): return {"status": "healthy", "scheduler_running": scheduler.running}

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)