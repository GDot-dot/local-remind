# migrate_smart.py (智慧型資料庫遷移工具)

import sqlite3
import os
import shutil
from datetime import datetime

# 設定資料庫檔案名稱
DB_FILE = "reminders.db"
BACKUP_FILE = f"reminders.db.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
TEMP_DB_FILE = "reminders_temp.db"

def get_table_columns(cursor, table_name):
    """獲取指定資料表的欄位名稱列表"""
    cursor.execute(f"PRAGMA table_info({table_name})")
    return [row[1] for row in cursor.fetchall()]

def migrate_data(old_db_path, new_db_path):
    """將資料從舊資料庫遷移到新資料庫"""
    print("--- 開始資料遷移 ---")
    
    # 連接到兩個資料庫
    conn_old = sqlite3.connect(old_db_path)
    conn_new = sqlite3.connect(new_db_path)
    cursor_old = conn_old.cursor()
    cursor_new = conn_new.cursor()

    try:
        # 獲取新資料庫中的所有資料表名稱
        cursor_new.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor_new.fetchall()]

        for table in tables:
            # 檢查舊資料庫中是否有這個表
            cursor_old.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if not cursor_old.fetchone():
                print(f"  [跳過] 資料表 '{table}' 在舊資料庫中不存在 (可能是新功能)。")
                continue

            print(f"  [處理] 正在遷移資料表: {table}...")
            
            # 獲取欄位列表
            old_columns = get_table_columns(cursor_old, table)
            new_columns = get_table_columns(cursor_new, table)
            
            # 找出共同欄位
            common_columns = [col for col in old_columns if col in new_columns]
            
            if not common_columns:
                print(f"    -> 警告：'{table}' 表沒有共同欄位，無法遷移資料。")
                continue

            common_columns_str = ", ".join(common_columns)
            placeholders = ", ".join(["?"] * len(common_columns))
            
            # 從舊表讀取資料
            cursor_old.execute(f"SELECT {common_columns_str} FROM {table}")
            rows = cursor_old.fetchall()
            
            if not rows:
                print(f"    -> 舊表中沒有資料，無需遷移。")
                continue

            # 寫入新表
            cursor_new.executemany(f"INSERT INTO {table} ({common_columns_str}) VALUES ({placeholders})", rows)
            print(f"    -> 成功遷移 {len(rows)} 筆資料。")

        conn_new.commit()
        print("\n✅ 資料遷移完成！")

    except Exception as e:
        print(f"\n❌ 遷移過程中發生錯誤: {e}")
        raise e
    finally:
        conn_old.close()
        conn_new.close()

def run_smart_migration():
    print(f"=== 智慧型資料庫遷移工具 ===")
    print(f"時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if not os.path.exists(DB_FILE):
        print(f"錯誤：找不到資料庫檔案 '{DB_FILE}'。無法進行遷移。")
        return

    # 1. 備份舊資料庫
    try:
        shutil.copyfile(DB_FILE, BACKUP_FILE)
        print(f"1. 已建立備份: {BACKUP_FILE}")
    except Exception as e:
        print(f"備份失敗: {e}")
        return

    # 2. 將現有的 reminders.db 改名為 reminders.db.old，作為資料來源
    OLD_DB_FILE = "reminders.db.old"
    if os.path.exists(OLD_DB_FILE):
        os.remove(OLD_DB_FILE)
    os.rename(DB_FILE, OLD_DB_FILE)
    print(f"2. 已將原資料庫暫存為: {OLD_DB_FILE}")

    # 3. 使用 db.py 建立全新的、結構正確的 reminders.db
    print("3. 正在根據最新的 db.py 建立新資料庫...")
    try:
        # 這裡使用一個小技巧：直接呼叫 init_db 來建立新檔
        # 因為我們已經把舊檔改名了，所以 init_db 會創建一個新的
        from db import init_db, Event, Location, CableState, CableSubscriber # 顯式導入所有模型
        init_db()
        print("   - 新資料庫結構建立成功。")
    except Exception as e:
        print(f"   - 建立新資料庫失敗: {e}")
        # 還原
        os.rename(OLD_DB_FILE, DB_FILE)
        return

    # 4. 執行資料搬運
    try:
        migrate_data(OLD_DB_FILE, DB_FILE)
        
        # 5. 清理
        # os.remove(OLD_DB_FILE) # 您可以選擇是否要自動刪除舊檔，這裡先保留以便檢查
        print(f"5. 遷移結束。舊資料庫暫存於 '{OLD_DB_FILE}'，確認無誤後可手動刪除。")
        print("\n🎉 恭喜！您的資料庫已成功升級並保留了所有舊資料。")
        
    except Exception as e:
        print(f"\n❌ 嚴重錯誤！正在還原資料庫...")
        if os.path.exists(DB_FILE):
            os.remove(DB_FILE)
        os.rename(OLD_DB_FILE, DB_FILE)
        print("   - 資料庫已還原至遷移前的狀態。")

if __name__ == "__main__":
    run_smart_migration()