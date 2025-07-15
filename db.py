# db.py (本機 SQLite 版本)
import os
import time
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, DateTime

# --- 本機修改 START ---
# 使用 SQLite 資料庫，它會在本機建立一個名為 reminders.db 的檔案
DATABASE_URL = "sqlite:///./reminders.db"
# --- 本機修改 END ---

# 建立資料庫引擎
# connect_args 是 SQLite 在多線程環境下安全執行所必需的
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
    target_user_id = Column(String, nullable=False)
    target_display_name = Column(Text, nullable=False)
    event_content = Column(Text, nullable=False)
    event_datetime = Column(DateTime(timezone=True), nullable=False)
    reminder_time = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Integer, default=0)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

# 提供一個安全的資料庫 session 函式
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

# 安全的資料庫操作函式
def safe_db_operation(operation, max_retries=3):
    """執行資料庫操作，包含重試機制"""
    for attempt in range(max_retries):
        try:
            return operation()
        except Exception as e:
            print(f"Database operation failed (attempt {attempt + 1}): {e}")
            if attempt == max_retries - 1:
                raise
            time.sleep(1)
    return None # 加上這行避免 mypy 警告

# 初始化資料庫表格的函式
def init_db():
    def _init():
        Base.metadata.create_all(bind=engine)
        print("Database tables checked/created.")
    
    try:
        safe_db_operation(_init)
    except Exception as e:
        print(f"Error creating database tables: {e}")
        raise

# 清理資料庫連線的函式
def cleanup_db():
    """清理資料庫連線池"""
    try:
        engine.dispose()
        print("Database connections cleaned up.")
    except Exception as e:
        print(f"Error cleaning up database connections: {e}")