import time
import json
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

def scrape_cable_map_info_robust():
    """
    使用 Selenium 從 https://smc.peering.tw/ 抓取資料。
    此版本增加了反偵測選項和更大的視窗，以應對更嚴格的網站。
    """
    url = "https://smc.peering.tw/"
    
    # --- 強化設定 ---
    service = Service(ChromeDriverManager().install())
    options = webdriver.ChromeOptions()
    # 1. 使用 headless 模式 (可以在背景執行)
    options.add_argument('--headless') 
    # 2. 強制設定一個大的視窗尺寸，避免響應式設計隱藏元素
    options.add_argument('--window-size=1920,1080')
    # 3. 增加反偵測參數
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
    options.add_argument('--disable-blink-features=AutomationControlled') # 關鍵參數，防止被偵測
    options.add_experimental_option('excludeSwitches', ['enable-automation'])
    options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(service=service, options=options)
    
    cable_data = []

    try:
        print(f"【強化版】正在前往目標網址: {url}")
        driver.get(url)
        
        # 增加等待時間並加入除錯訊息
        timeout = 30 
        wait = WebDriverWait(driver, timeout)
        container_selector = ".incident-list-container"

        # 獲取並印出網頁標題，確認載入的是正確頁面
        time.sleep(3) # 給予頁面初步渲染時間
        print(f"【強化版】成功載入網頁，標題為: '{driver.title}'")

        print(f"【強化版】等待目標元素 '{container_selector}' 載入 (最多 {timeout} 秒)...")
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, container_selector)))
        
        print("【強化版】目標元素已載入，開始抓取資料...")
        
        incident_cards = driver.find_elements(By.CSS_SELECTOR, ".incident-card")

        if not incident_cards:
            print("錯誤：找到了容器，但裡面沒有事件卡片。")
            return None

        for card in incident_cards:
            try:
                title = card.find_element(By.CSS_SELECTOR, ".incident-title").text.strip()
                status = card.find_element(By.CSS_SELECTOR, ".incident-status").text.strip()
                description = card.find_element(By.CSS_SELECTOR, ".incident-description").text.strip()
                timestamps = [ts.text.strip() for ts in card.find_elements(By.CSS_SELECTOR, ".incident-timestamp")]

                row_data = {
                    "事件標題": title,
                    "狀態": status,
                    "描述": description,
                    "時間資訊": timestamps
                }
                cable_data.append(row_data)
            except Exception as e:
                print(f"處理單一卡片時出錯: {e}")
                continue

        return cable_data

    except TimeoutException:
        print(f"\n錯誤：在 {timeout} 秒內找不到目標元素 '{container_selector}'。")
        print("這可能是因為網站的反爬蟲機制，或是網頁結構已變更。")
        print("正在將當前頁面原始碼儲存為 debug.html 以便分析...")
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        print("已儲存 debug.html。")
        return None
    except Exception as e:
        print(f"抓取過程中發生未預期的錯誤: {e}")
        return None
    finally:
        print("【強化版】任務完成，關閉瀏覽器。")
        driver.quit()

