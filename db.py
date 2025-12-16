# db.py (修正版 - 確保導出 DATABASE_URL)

import os
import time
from datetime import datetime
from sqlalchemy import create_engine, func, Column, Integer, String, Text, TIMESTAMP, DateTime, Float
from sqlalchemy.orm import sessionmaker, declarative_base

# --- 資料庫設定區塊 START ---

# 1. 您的 Neon 連線字串
NEON_URL = "postgresql://neondb_owner:npg_1F3LyGaPClmO@ep-holy-bird-a1zdn8yc-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

# 2. 決定使用的 URL (暫存變數)
if os.environ.get('DATABASE_URL'):
    # Render 雲端環境
    _url = os.environ.get('DATABASE_URL')
elif NEON_URL:
    # 本機開發 (有填 Neon)
    _url = NEON_URL
else:
    # 本機開發 (沒填 Neon)
    _url = "sqlite:///./reminders.db"

# 3. 修正 Postgres 的網址開頭
if _url and _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

# 4. 【關鍵修改】將最終結果賦值給 DATABASE_URL，讓 app.py 可以 import
DATABASE_URL = _url

# 5. 建立資料庫引擎
try:
    if "sqlite" in DATABASE_URL:
        engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
        print(f"⚠️ 使用本地資料庫: {DATABASE_URL}")
    else:
        engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,   # 每次連線前先檢查，死了就重連 (解決 SSL closed 錯誤)
            pool_recycle=300,     # 每 300 秒(5分鐘) 自動回收連線，防止被雲端強制切斷
            pool_size=5,
            max_overflow=10
        )
except Exception as e:
    print(f"❌ 資料庫設定錯誤: {e}")
    # 發生錯誤時的保底
    DATABASE_URL = "sqlite:///./reminders.db"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# --- 資料庫設定區塊 END ---

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------
# 資料庫模型 (ORM Models)
# ---------------------------------

class Event(Base):
    __tablename__ = 'events'
    id = Column(Integer, primary_key=True, index=True)
    creator_user_id = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    target_type = Column(String, nullable=False)
    target_display_name = Column(Text, nullable=False)
    event_content = Column(Text, nullable=False)
    event_datetime = Column(DateTime(timezone=True), nullable=True)
    reminder_time = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Integer, default=0)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    is_recurring = Column(Integer, default=0, nullable=False)
    recurrence_rule = Column(String, nullable=True)
    next_run_time = Column(DateTime(timezone=True), nullable=True, index=True)
    priority_level = Column(Integer, default=0)
    remaining_repeats = Column(Integer, default=0)

    def __repr__(self):
        return f"<Event(id={self.id}, content='{self.event_content}')>"

class Location(Base):
    __tablename__ = 'locations'
    id = Column(Integer, primary_key=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    address = Column(String)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Location(name='{self.name}', user_id='{self.user_id}')>"
        
class Memory(Base):
    __tablename__ = 'memories'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    keyword = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    
    def __repr__(self):
        return f"<Memory(keyword='{self.keyword}', user_id='{self.user_id}')>"
        
class UserCard(Base):
    __tablename__ = 'user_cards'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    card_name = Column(String, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<UserCard(name='{self.card_name}', user_id='{self.user_id}')>"


# ---------------------------------
# 核心資料庫函式
# ---------------------------------

def init_db():
    def _init():
        Base.metadata.create_all(bind=engine)
        print("Database tables checked/created.")
    safe_db_operation(_init)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def cleanup_db():
    try:
        engine.dispose()
        print("Database connections cleaned up.")
    except Exception as e:
        print(f"Error cleaning up database connections: {e}")

def safe_db_operation(operation, max_retries=3):
    for attempt in range(max_retries):
        try:
            return operation()
        except Exception as e:
            print(f"Database operation failed (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(1)
    return None

# ---------------------------------
# 地點功能相關的資料庫函式
# ---------------------------------
        
def add_location(user_id, name, address, latitude, longitude):
    def _add():
        db = next(get_db())
        try:
            existing_location = db.query(Location).filter_by(user_id=user_id, name=name).first()
            if existing_location: return f"名稱重複: 您已記錄過名為 '{name}' 的地點。"
            new_loc = Location(user_id=user_id, name=name, address=address, latitude=latitude, longitude=longitude)
            db.add(new_loc)
            db.commit()
            return "成功"
        finally:
            db.close()
    return safe_db_operation(_add)

def get_location_by_name(user_id, name):
    def _get():
        db = next(get_db())
        try:
            return db.query(Location).filter_by(user_id=user_id, name=name).first()
        finally:
            db.close()
    return safe_db_operation(_get)

def get_all_locations_by_user(user_id):
    def _get_all():
        db = next(get_db())
        try:
            return db.query(Location).filter_by(user_id=user_id).order_by(Location.name).all()
        finally:
            db.close()
    return safe_db_operation(_get_all)
    
def delete_location_by_name(user_id, name):
    def _delete():
        db = next(get_db())
        try:
            location_to_delete = db.query(Location).filter_by(user_id=user_id, name=name).first()
            if location_to_delete:
                db.delete(location_to_delete)
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_delete)

# ---------------------------------
# 提醒功能相關的資料庫函式
# ---------------------------------

def add_event(creator_user_id, target_id, target_type, display_name, content, event_datetime, is_recurring=0, recurrence_rule=None, next_run_time=None, priority_level=0, remaining_repeats=0):
    def _add_event():
        db = next(get_db())
        try:
            new_event = Event(
                creator_user_id=creator_user_id, target_id=target_id, target_type=target_type,
                target_display_name=display_name, event_content=content, event_datetime=event_datetime,
                is_recurring=is_recurring, recurrence_rule=recurrence_rule, next_run_time=next_run_time,
                priority_level=priority_level, remaining_repeats=remaining_repeats
            )
            db.add(new_event)
            db.commit()
            db.refresh(new_event)
            return new_event.id
        finally:
            db.close()
    return safe_db_operation(_add_event)

def get_event(event_id):
    def _get():
        db = next(get_db())
        try:
            return db.query(Event).filter(Event.id == event_id).first()
        finally:
            db.close()
    return safe_db_operation(_get)

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

def decrease_remaining_repeats(event_id):
    def _decrease():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event and event.remaining_repeats > 0:
                event.remaining_repeats -= 1
                db.commit()
                return event.remaining_repeats
            return 0
        finally:
            db.close()
    return safe_db_operation(_decrease)

def get_all_events_by_user(user_id):
    """獲取某個使用者建立的所有提醒 (包含一次性與週期性)"""
    def _get_all():
        db = next(get_db())
        try:
            return db.query(Event).filter(Event.creator_user_id == user_id).order_by(Event.event_datetime.asc()).all()
        finally:
            db.close()
    return safe_db_operation(_get_all)

def delete_event_by_id(event_id, user_id):
    """根據 Event ID 刪除提醒，並驗證操作者是否為本人"""
    def _delete():
        db = next(get_db())
        try:
            event_to_delete = db.query(Event).filter(Event.id == event_id, Event.creator_user_id == user_id).first()
            if event_to_delete:
                is_recurring = event_to_delete.is_recurring
                db.delete(event_to_delete)
                db.commit()
                return {"status": "success", "is_recurring": is_recurring}
            return {"status": "not_found"}
        finally:
            db.close()
    return safe_db_operation(_delete)
    
def update_event_snooze(event_id, reminder_dt, new_content):
    """延後專用：同時更新提醒時間與內容"""
    def _update():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.reminder_time = reminder_dt
                event.event_content = new_content  # 更新內容，加上 (延)
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_update)
    
def update_event_content(event_id, new_content):
    """更新提醒內容"""
    def _update():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.event_content = new_content
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_update)

def reschedule_event_time(event_id, new_datetime):
    """徹底修改提醒時間 (非延後，而是改期)"""
    def _update():
        db = next(get_db())
        try:
            event = db.query(Event).filter(Event.id == event_id).first()
            if event:
                event.event_datetime = new_datetime # 更新原始時間
                event.reminder_time = new_datetime  # 更新提醒時間
                event.reminder_sent = 0             # 重置發送狀態
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_update)
    
def save_memory(user_id, keyword, content):
    """儲存記憶 (如果關鍵字已存在則更新)"""
    def _save():
        db = next(get_db())
        try:
            # 先找找看有沒有舊的
            existing = db.query(Memory).filter(
                Memory.user_id == user_id, 
                Memory.keyword == keyword
            ).first()
            
            if existing:
                existing.content = content # 更新
                action = "更新"
            else:
                new_mem = Memory(user_id=user_id, keyword=keyword, content=content)
                db.add(new_mem)
                action = "新增"
            
            db.commit()
            return action
        finally:
            db.close()
    return safe_db_operation(_save)

def get_memory(user_id, keyword):
    """查詢單筆記憶"""
    def _get():
        db = next(get_db())
        try:
            # 支援模糊搜尋 (選擇性)
            # 這裡示範精確搜尋，比較不會搜出太多雜訊
            return db.query(Memory).filter(
                Memory.user_id == user_id, 
                Memory.keyword.ilike(f"%{keyword}%")
            ).first()
        finally:
            db.close()
    return safe_db_operation(_get)

def delete_memory(user_id, keyword):
    """刪除記憶"""
    def _delete():
        db = next(get_db())
        try:
            mem = db.query(Memory).filter(
                Memory.user_id == user_id, 
                Memory.keyword == keyword
            ).first()
            if mem:
                db.delete(mem)
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_delete)

def get_all_memories(user_id):
    """列出所有關鍵字"""
    def _get_all():
        db = next(get_db())
        try:
            return db.query(Memory).filter(Memory.user_id == user_id).all()
        finally:
            db.close()
    return safe_db_operation(_get_all)
    
def search_memories_by_keyword(user_id, keyword):
    """模糊搜尋：回傳所有符合的記憶列表"""
    def _search():
        db = next(get_db())
        try:
            # 使用 ilike 做模糊搜尋，回傳所有符合的結果 (.all())
            return db.query(Memory).filter(
                Memory.user_id == user_id, 
                Memory.keyword.ilike(f"%{keyword}%") 
            ).all()
        finally:
            db.close()
    return safe_db_operation(_search)

def get_memory_by_id(memory_id):
    """根據 ID 精準獲取單筆記憶 (按鈕回傳用)"""
    def _get():
        db = next(get_db())
        try:
            return db.query(Memory).filter(Memory.id == memory_id).first()
        finally:
            db.close()
    return safe_db_operation(_get)
    
# ---------------------------------
# 【新增】信用卡功能函式
# ---------------------------------

def add_user_card(user_id, card_name):
    def _add():
        db = next(get_db())
        try:
            exists = db.query(UserCard).filter(
                UserCard.user_id == user_id, 
                UserCard.card_name == card_name
            ).first()
            if exists: return "已存在"
            
            new_card = UserCard(user_id=user_id, card_name=card_name)
            db.add(new_card)
            db.commit()
            return "成功"
        finally:
            db.close()
    return safe_db_operation(_add)

def get_user_cards(user_id):
    def _get():
        db = next(get_db())
        try:
            cards = db.query(UserCard).filter(UserCard.user_id == user_id).all()
            return [c.card_name for c in cards]
        finally:
            db.close()
    return safe_db_operation(_get)

def delete_user_card(user_id, card_name):
    def _delete():
        db = next(get_db())
        try:
            card = db.query(UserCard).filter(
                UserCard.user_id == user_id, 
                UserCard.card_name == card_name
            ).first()
            if card:
                db.delete(card)
                db.commit()
                return True
            return False
        finally:
            db.close()
    return safe_db_operation(_delete)