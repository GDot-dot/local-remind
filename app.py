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
from features import reminder, location, recurring_reminder, memory, credit_card

# =========== 🔎 抓鬼大隊：開機檢查 (插入在最上面) ===========
print("="*50)
print("🚀 系統啟動，正在檢查環境變數...")
all_keys = list(os.environ.keys())
print(f"🔑 目前系統內有的變數名稱: {all_keys}")

# 檢查 DATABASE_URL (對照組)
if "DATABASE_URL" in os.environ:
    print("✅ DATABASE_URL: 存在")
else:
    print("❌ DATABASE_URL: 消失了！")

# 檢查 GOOGLE_API_KEY (實驗組)
target_key = "GOOGLE_API_KEY"
if target_key in os.environ:
    val = os.environ[target_key]
    print(f"✅ {target_key}: 存在！(長度: {len(val)})")
else:
    print(f"❌ {target_key}: 嚴重錯誤！找不到此變數！")
    
    # 模糊搜尋：看看有沒有長得很像的
    for k in all_keys:
        if "GOOGLE" in k:
            print(f"⚠️ 發現疑似變數: '{k}' (長度: {len(k)}) <- 請檢查是否有空白鍵")

print("="*50)
# ========================================================





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
    """
    從資料庫讀取所有「週期性提醒」與「未發送的一次性提醒」，
    並將它們重新加入排程器。
    """
    with app.app_context():
        # 為了避免循環引用，這裡才 import db
        from db import get_db, Event
        
        db = next(get_db())
        try:
            logger.info("♻️ 正在檢查並修復排程任務...")
            
            # 1. 找出所有【週期性提醒】(這些永遠需要被排程)
            recurring_events = db.query(Event).filter(Event.is_recurring == 1).all()
            
            # 2. 找出所有【未發送】且【時間在未來】的一次性提醒
            now = datetime.now(TAIPEI_TZ)
            future_events = db.query(Event).filter(
                Event.reminder_sent == 0,
                Event.is_recurring == 0,
                Event.reminder_time > now # 注意：這裡是檢查 reminder_time
            ).all()

            all_events = recurring_events + future_events
            restored_count = 0

            for event in all_events:
                # 根據事件類型決定 Job ID
                job_id = f"recurring_{event.id}" if event.is_recurring else f"reminder_{event.id}"
                
                # 如果排程器裡還沒有這個任務，就加進去
                if not scheduler.get_job(job_id):
                    try:
                        if event.is_recurring:
                            # 解析週期規則 (例如: "MON,WED|23:00")
                            rule_parts = event.recurrence_rule.split('|')
                            days_code = rule_parts[0].lower() # mon,wed
                            time_parts = rule_parts[1].split(':')
                            hour = int(time_parts[0])
                            minute = int(time_parts[1])
                            
                            scheduler.add_job(
                                send_reminder,
                                trigger='cron',
                                args=[event.id],
                                id=job_id,
                                day_of_week=days_code,
                                hour=hour,
                                minute=minute,
                                timezone=TAIPEI_TZ,
                                replace_existing=True
                            )
                        else:
                            # 一次性提醒
                            run_date = event.reminder_time.astimezone(TAIPEI_TZ)
                            scheduler.add_job(
                                send_reminder, 
                                'date', 
                                run_date=run_date, 
                                args=[event.id], 
                                id=job_id,
                                replace_existing=True
                            )
                        
                        restored_count += 1
                        logger.info(f"  + 成功修復排程: ID {event.id} ({event.event_content})")
                    except Exception as e:
                        logger.error(f"  ! 修復 ID {event.id} 失敗: {e}")
            
            logger.info(f"✅ 排程修復完成！共重新註冊 {restored_count} 個任務。")

        except Exception as e:
            logger.error(f"❌ 排程修復過程發生錯誤: {e}")
        finally:
            db.close()

def safe_start_scheduler():
    with scheduler_lock:
        try:
            if not scheduler.running:
                scheduler.start()
                logger.info("Scheduler started successfully.")
                
                # 【關鍵修改】啟動後，立刻執行一次修復任務
                # 使用 Thread 避免卡住 Web Server 啟動
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
找地點 [名稱]：查詢已儲存的地點。

--- 記憶功能 ---
記住 [關鍵字] [內容]：儲存重要資訊。
查詢 [關鍵字]：叫出儲存的內容。
忘記 [關鍵字]：刪除該筆記憶。
記憶清單：查看所有已記住的關鍵字。

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
             # --- 【新增】編輯內容的狀態處理 ---
            elif state_action == 'awaiting_edit_content':
                event_id = user_states[user_id].get('event_id')
                original_content = user_states[user_id].get('original_content')
                
                # 判斷是「補充」還是「覆蓋」
                if text.startswith('+') or text.startswith('＋'):
                    # 補充模式：去掉加號，接在後面
                    append_text = text[1:].strip()
                    new_content = f"{original_content} ({append_text})"
                    mode_msg = "補充"
                else:
                    # 覆蓋模式
                    new_content = text
                    mode_msg = "修改"
                
                # 執行更新 (確保 update_event_content 有從 db 匯入，或是直接在這裡 import)
                from db import update_event_content 
                if update_event_content(event_id, new_content):
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已{mode_msg}內容為：\n{new_content}"))
                else:
                    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 更新失敗，找不到該提醒。"))
                
                # 清除狀態
                del user_states[user_id]
                return
                
        if text.startswith('新增卡片'):
            card_name = text.replace('新增卡片', '').strip()
            if not card_name:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 請輸入卡片名稱。\n範例：新增卡片 國泰CUBE"))
                return
            
            result = add_user_card(user_id, card_name)
            if result == "成功":
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已新增卡片：{card_name}"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"⚠️ 新增失敗：{result} (可能已存在)"))
            return

        elif text.startswith('刪除卡片'):
            card_name = text.replace('刪除卡片', '').strip()
            if delete_user_card(user_id, card_name):
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🗑️ 已刪除卡片：{card_name}"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"❌ 找不到卡片：{card_name}"))
            return

        elif text == '我的卡包':
            cards = get_user_cards(user_id)
            if cards:
                cards_str = "\n".join([f"💳 {c}" for c in cards])
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"您的信用卡：\n{cards_str}"))
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="您還沒有設定任何信用卡喔！\n請輸入：新增卡片 [名稱]"))
            return

        # --- 【新增】刷卡回饋查詢 ---
        elif text.startswith('刷 '):
            merchant = text[2:].strip() # 去掉前面的 "刷 "
            if not merchant: return
            
            # 為了避免使用者等待太久以為當機，可以先回傳一個 Loading 動畫或是文字
            # 但 LINE Reply Token 只能用一次，所以我們直接讓它轉圈圈等待 AI 回覆
            # 若要優化體驗，建議未來可以用 Push Message 做「查詢中...」的效果
            
            try:
                # 呼叫 features/credit_card.py 裡的分析函式
                analysis_result = credit_card.analyze_best_card(user_id, merchant)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=analysis_result))
            except Exception as e:
                logger.error(f"Credit Card Analysis Error: {e}")
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 分析失敗，請稍後再試。"))
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
        elif text.startswith('記住') or text.startswith('查詢') or text.startswith('忘記') or text == '記憶清單':
            memory.handle_memory_command(event, line_bot_api)
            return
        elif text.lower() in ['help', '說明', '幫助']:
            send_help_message(event.reply_token)
            return

        # --- 4. AI 智慧解析區塊 ---
        # 條件：訊息長度 > 1 且不是上面那些指令
        time_keywords = [
            '明天', '後天', '今天', '下週', '下周', '禮拜', '星期', 
            '點', '分', '早上', '下午', '晚上', '中午', '半', 
            '提醒', '幫我', '記得', '後'
        ]
        
        # 判斷邏輯：
        # 1. 長度要大於 1
        # 2. 必須包含至少一個時間關鍵字 (或者包含數字)
        is_potential_reminder = any(k in text for k in time_keywords) or any(char.isdigit() for char in text)

        # 【修改】加上 is_potential_reminder 判斷，沒關鍵字就不問 AI
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
                        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="😅 AI 幫你算出來的時間已經過了，請再說一次。"))
                        return

                    # 顯示名稱
                    try:
                        profile = line_bot_api.get_profile(user_id)
                        display_name = profile.display_name
                    except:
                        display_name = "您"
                    
                    target_id = user_id # 預設為個人
                    if source_type == 'group':
                        target_id = event.source.group_id
                    elif source_type == 'room':
                        target_id = event.source.room_id
                    
                    # 寫入資料庫
                    event_id = add_event(
                        creator_user_id=user_id,
                        target_id=target_id,      # <--- 改用判斷後的 ID
                        target_type=source_type,  # <--- 改用來源類型 (group/user)
                        display_name=display_name,
                        content=parsed_content,
                        event_datetime=event_dt,
                        is_recurring=0
                    )

                    if event_id:
                        # 跳出確認按鈕 (已更新為完整選項)
                        from features.reminder import QuickReply, QuickReplyButton, PostbackAction
                        quick_reply = QuickReply(items=[
                            QuickReplyButton(action=PostbackAction(label="10分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=10")),
                            QuickReplyButton(action=PostbackAction(label="30分鐘前", data=f"action=set_reminder&id={event_id}&type=minute&val=30")),
                            QuickReplyButton(action=PostbackAction(label="1天前", data=f"action=set_reminder&id={event_id}&type=day&val=1")),
                            QuickReplyButton(action=PostbackAction(label="不提醒", data=f"action=set_reminder&id={event_id}&type=none")),
                        ])
                        
                        reply_text = f"🤖 AI 設定提醒成功！\n\n時間：{event_dt.strftime('%Y/%m/%d %H:%M')}\n事項：{parsed_content}\n\n要提早提醒嗎？"
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
        elif action in ['set_reminder', 'confirm_reminder', 'snooze_reminder', 'snooze_custom', 'set_priority', 'set_priority_time', 'delete_reminder_prompt', 'delete_single', 'refresh_manage_panel', 'edit_prompt', 'edit_content_start', 'edit_time_confirm']:
            reminder.handle_reminder_postback(event, line_bot_api, scheduler, send_reminder, safe_add_job, TAIPEI_TZ, user_states)
        elif action in ['toggle_weekday', 'set_recurring_time']:
            recurring_reminder.handle_postback(event, line_bot_api, user_states)
        elif action == 'view_memory':
            memory.handle_memory_postback(event, line_bot_api)
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