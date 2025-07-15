# db.py (更新版本 - 支援群組提醒)
import os
import time
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, DateTime

# 使用 SQLite 資料庫
DATABASE_URL = "sqlite:///./reminders.db"

# 建立資料庫引擎
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 定義事件的資料庫模型 (ORM Model)
class Event(Base):
    __tablename__ = 'events'

    id = Column(Integer, primary_key=True, index=True)
    creator_user_id = Column(String, nullable=False)
    
    # --- 這就是需要修正的地方 ---
    target_id = Column(String, nullable=False)      # 提醒的目標 ID (可能是 user_id, group_id, or room_id)
    target_type = Column(String, nullable=False)    # 提醒的目標類型 ('user', 'group', or 'room')
    # --- 修正結束 ---

    target_display_name = Column(Text, nullable=False)
    event_content = Column(Text, nullable=False)
    event_datetime = Column(DateTime(timezone=True), nullable=False)
    reminder_time = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Integer, default=0)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

# --- 以下程式碼與之前版本相同，無需修改 ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        print(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

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

def init_db():
    def _init():
        Base.metadata.create_all(bind=engine)
        print("Database tables checked/created.")
    
    try:
        safe_db_operation(_init)
    except Exception as e:
        print(f"Error creating database tables: {e}")
        raise

def cleanup_db():
    try:
        engine.dispose()
        print("Database connections cleaned up.")
    except Exception as e:
        print(f"Error cleaning up database connections: {e}")