# reschedule_jobs.py (重新註冊舊提醒到排程器)

from app import app, scheduler, send_reminder, TAIPEI_TZ
from db import get_db, Event
from datetime import datetime

def restore_jobs():
    print("--- 開始修復排程任務 ---")
    
    # 必須在 app context 下操作
    with app.app_context():
        db = next(get_db())
        try:
            # 1. 找出所有【未發送】且【時間在未來】的一次性提醒
            now = datetime.now(TAIPEI_TZ)
            pending_events = db.query(Event).filter(
                Event.reminder_sent == 0,
                Event.is_recurring == 0,
                Event.event_datetime > now
            ).all()

            print(f"找到 {len(pending_events)} 個未完成的一次性提醒。")

            for event in pending_events:
                job_id = f"reminder_{event.id}"
                
                # 檢查排程器裡是否已經有這個任務
                if not scheduler.get_job(job_id):
                    print(f"  + 正在重新排程: ID {event.id} - {event.event_content}")
                    
                    # 重新加入排程
                    run_date = event.event_datetime.astimezone(TAIPEI_TZ)
                    scheduler.add_job(
                        send_reminder, 
                        'date', 
                        run_date=run_date, 
                        args=[event.id], 
                        id=job_id,
                        replace_existing=True
                    )
                else:
                    print(f"  - 跳過: ID {event.id} (排程器中已存在)")

            # 2. 找出所有【週期性提醒】(這些永遠需要被排程)
            recurring_events = db.query(Event).filter(Event.is_recurring == 1).all()
            print(f"找到 {len(recurring_events)} 個週期性提醒。")

            for event in recurring_events:
                job_id = f"recurring_{event.id}"
                if not scheduler.get_job(job_id):
                    print(f"  + 正在重新排程週期任務: ID {event.id} - {event.event_content}")
                    
                    # 解析規則
                    try:
                        rule_parts = event.recurrence_rule.split('|')
                        days_code = rule_parts[0]
                        time_str = rule_parts[1]
                        hour, minute = time_str.split(':')
                        
                        scheduler.add_job(
                            send_reminder,
                            trigger='cron',
                            args=[event.id],
                            id=job_id,
                            day_of_week=days_code.lower(),
                            hour=int(hour),
                            minute=int(minute),
                            timezone=TAIPEI_TZ,
                            replace_existing=True
                        )
                    except Exception as e:
                        print(f"    ! 錯誤: 無法解析規則 {event.recurrence_rule}: {e}")

            print("\n✅ 修復完成！所有舊提醒都已重新加入排程。")

        except Exception as e:
            print(f"❌ 發生錯誤: {e}")
        finally:
            db.close()

if __name__ == "__main__":
    # 確保排程器已啟動 (雖然這裡只是添加任務，但最好是在 app 上下文中)
    restore_jobs()