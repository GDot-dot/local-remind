# db.py (優化連線池版)

import os
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Neon 連線字串
NEON_URL = "postgresql://neondb_owner:YOUR_PASSWORD@ep-holy-bird-a1zdn8yc-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"

# 2. 獲取 Database URL
_url = os.environ.get('DATABASE_URL', NEON_URL)
if _url and _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

DATABASE_URL = _url

# 3. 建立引擎 (關鍵優化：加入連線池設定)
# Render/Neon 的連線常會因閒置被切斷，必須設定 pool_pre_ping=True
engine = create_engine(
    DATABASE_URL,
    pool_size=5,            # 保持 5 個連線
    max_overflow=10,        # 最多額外建立 10 個
    pool_recycle=300,       # 每 5 分鐘回收連線，避免 stale connection
    pool_pre_ping=True,     # 每次拿連線前先 Ping 一下，避免 "Closed connection" 錯誤
    pool_use_lifo=True      # 優先使用剛用過的連線
)

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