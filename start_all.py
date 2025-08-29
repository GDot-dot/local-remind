# start_all.py (更新版本 - 解決 ngrok 網域問題與 SDK v3 語法)

import os
import sys
import time
import atexit
import subprocess
from pyngrok import ngrok
from pyngrok.conf import PyngrokConfig
from linebot.v3.messaging import (
    ApiClient,
    MessagingApi,
    Configuration,
    SetWebhookEndpointRequest,
)

# --- 請在此處填寫你的設定 ---
# 1. LINE Bot 憑證 (與 app.py 相同)
LINE_CHANNEL_ACCESS_TOKEN = '0jtuGMTolXKvvsQmb3CcAoD9JdkADsDKe+xsICSU9xmIcdyHmAFCTPY3H04nI1DeHvD/SyMMj3qt/Rw+NEI6DsHk8n7qxJ4siyYKY3QxhrBWb9QAkPDDLsVCs6Xny+t+6QEVFvx3hVDUTWTe7AxdtQdB04t89/1O/w1cDnyilFU='

# 2. 從 ngrok Dashboard (Domains 頁面) 取得你的靜態網域
NGROK_STATIC_DOMAIN = 'krill-strong-conversely.ngrok-free.app' # 例如: 'lion-organic-severely.ngrok-free.app'

# 3. 指定你的 ngrok 設定檔路徑
#    使用 r"..." 可以避免 Windows 路徑中的反斜線問題
NGROK_CONFIG_PATH = r"C:\Users\user\AppData\Local\ngrok\ngrok_remind.yml"

# ----------------------------------------------------

# 檢查設定是否已填寫
if 'YOUR_CHANNEL' in LINE_CHANNEL_ACCESS_TOKEN:
    print("錯誤：請在 start_all.py 中設定你的 'LINE_CHANNEL_ACCESS_TOKEN'。")
    sys.exit(1)
if 'YOUR_NGROK' in NGROK_STATIC_DOMAIN:
    print("錯誤：請在 start_all.py 中設定你的 'NGROK_STATIC_DOMAIN'。")
    print("請登入 https://dashboard.ngrok.com/ 找到你的靜態網域。")
    sys.exit(1)


print("正在啟動 Flask 應用程式...")
# 在背景啟動 Flask app
# 使用 os.devnull 避免 flask 的啟動訊息與本腳本訊息混在一起
flask_process = subprocess.Popen(
    [sys.executable, "app.py"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.STDOUT
)

# 確保程式結束時，所有背景服務也會被關閉
def cleanup():
    print("\n正在關閉所有服務...")
    if flask_process:
        flask_process.terminate()
        print("Flask 應用程式已關閉。")
    ngrok.kill()
    print("ngrok 通道已關閉。")

atexit.register(cleanup)

print("Flask 啟動成功！等待幾秒鐘讓服務穩定...")
time.sleep(3) # 給 Flask 一點時間啟動

try:
    print(f"將使用設定檔: {NGROK_CONFIG_PATH}")
    # 建立一個 PyngrokConfig 物件，並指向你的 .yml 檔案
    pyngrok_config = PyngrokConfig(config_path=NGROK_CONFIG_PATH)
        
    print(f"正在透過靜態網域 '{NGROK_STATIC_DOMAIN}' 建立 ngrok 通道...")
    # 啟動 ngrok，並指定靜態網域
    ngrok_tunnel = ngrok.connect(5000, "http", domain=NGROK_STATIC_DOMAIN,pyngrok_config=pyngrok_config)
    

    # 組合完整的 webhook URL
    webhook_url = f"{ngrok_tunnel.public_url}/callback"
    print(f"ngrok 通道建立成功！公開網址為: {webhook_url}")

    print("正在自動更新 LINE Bot 的 Webhook URL (使用 v3 SDK)...")
    try:
        # --- 使用 v3 SDK 的方式更新 Webhook ---
        configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
        with ApiClient(configuration) as api_client:
            messaging_api = MessagingApi(api_client)
            set_webhook_endpoint_request = SetWebhookEndpointRequest(endpoint=webhook_url)
            messaging_api.set_webhook_endpoint(set_webhook_endpoint_request)
        
        print(f"Webhook URL 已成功更新為: {webhook_url}")
    except Exception as e:
        print(f"更新 Webhook 失敗: {e}")
        # 如果更新失敗，整個程序就沒有意義了，直接退出
        sys.exit(1)

    print("\n--- 所有服務已啟動！ ---")
    print("你的 LINE Bot 現在已經上線。")
    print("你可以開始在 LINE 上與它互動了。")
    print("若要關閉所有服務，請在此視窗按下 Ctrl + C。")

    # 保持主腳本運行，直到被手動中斷
    ngrok_process = ngrok.get_ngrok_process()
    try:
        ngrok_process.proc.wait()
    except KeyboardInterrupt:
        print("\n收到中斷訊號...")

except Exception as e:
    print(f"啟動過程中發生錯誤: {e}")
    sys.exit(1)
finally:
    # 確保無論如何都會執行清理
    cleanup()