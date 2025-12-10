# db.py (整合重要提醒功能)

import os
import time
from datetime import datetime
from sqlalchemy import create_engine, func, Column, Integer, String, Text, TIMESTAMP, DateTime, Float
from sqlalchemy.orm import sessionmaker, declarative_base

# --- 資料庫設定 ---
DATABASE_URL = "sqlite:///./reminders.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
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
    
    # --- 新增：重要提醒欄位 ---
    priority_level = Column(Integer, default=0) # 0=普通, 1=綠, 2=黃, 3=紅
    remaining_repeats = Column(Integer, default=0) # 剩餘重試次數

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

class CableState(Base):
    __tablename__ = 'cable_state'
    id = Column(Integer, primary_key=True)
    last_event_titles = Column(Text, nullable=True)
    last_checked = Column(DateTime, default=datetime.utcnow)

class CableSubscriber(Base):
    __tablename__ = 'cable_subscribers'
    id = Column(Integer, primary_key=True)
    subscriber_id = Column(String, nullable=False, unique=True)
    subscriber_type = Column(String, nullable=False)
    subscribed_at = Column(DateTime, default=datetime.utcnow)

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
    def _get_all():
        db = next(get_db())
        try:
            return db.query(Event).filter(Event.creator_user_id == user_id).order_by(Event.event_datetime.asc()).all()
        finally:
            db.close()
    return safe_db_operation(_get_all)

def delete_event_by_id(event_id, user_id):
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

# ---------------------------------
# 海纜監控相關的資料庫函式
# ---------------------------------
def get_last_cable_state():
    def _get():
        db = next(get_db())
        try:
            state = db.query(CableState).filter_by(id=1).first()
            if not state:
                new_state = CableState(id=1, last_event_titles="")
                db.add(new_state)
                db.commit()
                db.refresh(new_state)
            return state
        finally:
            db.close()
    return safe_db_operation(_get)

def update_last_cable_state(new_titles_str):
    def _update():
        db = next(get_db())
        try:
            state = db.query(CableState).filter_by(id=1).first()
            if state:
                state.last_event_titles = new_titles_str
                state.last_checked = datetime.utcnow()
                db.commit()
            return True
        finally:
            db.close()
    return safe_db_operation(_update)

def add_cable_subscriber(sub_id, sub_type):
    def _add():
        db = next(get_db())
        try:
            existing = db.query(CableSubscriber).filter_by(subscriber_id=sub_id).first()
            if existing: return "already_subscribed"
            new_sub = CableSubscriber(subscriber_id=sub_id, subscriber_type=sub_type)
            db.add(new_sub)
            db.commit()
            return "success"
        finally:
            db.close()
    return safe_db_operation(_add)

def remove_cable_subscriber(sub_id):
    def _remove():
        db = next(get_db())
        try:
            sub_to_delete = db.query(CableSubscriber).filter_by(subscriber_id=sub_id).first()
            if sub_to_delete:
                db.delete(sub_to_delete)
                db.commit()
                return "success"
            return "not_found"
        finally:
            db.close()
    return safe_db_operation(_remove)

def get_all_cable_subscribers():
    def _get_all():
        db = next(get_db())
        try:
            return db.query(CableSubscriber).all()
        finally:
            db.close()
    return safe_db_operation(_get_all)