import cv2
import numpy as np
import os

def detect_blue_p(image_input):
    """
    偵測畫面中是否有藍色 'P' 圖示 (代表正在停車)
    
    Args:
        image_input: 可以是圖片路徑 (str) 或是 cv2 讀取的圖片 (numpy array)
    
    Returns:
        bool: 是否偵測到藍色 P
        image: 標記了偵測結果的圖片 (用於除錯)
    """
    
    # 1. 處理輸入圖片
    if isinstance(image_input, str):
        if not os.path.exists(image_input):
            print(f"錯誤: 找不到檔案 {image_input}")
            return False, None
        img = cv2.imread(image_input)
    else:
        img = image_input.copy() # 複製一份以免修改到原圖

    if img is None:
        return False, None

    # 2. 轉換為 HSV 顏色空間
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    # 3. 定義藍色 P 的 HSV 範圍
    # 注意: OpenCV 的 H 範圍是 0-179, S 和 V 是 0-255
    # 這裡設定一個較寬的藍色/青色範圍，您可能需要根據實際截圖微調
    # 預估藍色 P 的顏色大約在 H: 100-120 之間
    lower_blue = np.array([90, 120, 150])  # 下限: 色相90, 高飽和度, 高亮度
    upper_blue = np.array([130, 255, 255]) # 上限: 色相130

    # 4. 產生遮罩 (Mask)
    mask = cv2.inRange(hsv, lower_blue, upper_blue)

    # 5. 形態學運算 (去雜訊)
    # 侵蝕掉小白點
    mask = cv2.erode(mask, None, iterations=1)
    # 膨脹讓目標區域更明顯
    mask = cv2.dilate(mask, None, iterations=2)

    # 6. 尋找輪廓
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found_p = False
    
    # 7. 遍歷輪廓並篩選
    for contour in contours:
        area = cv2.contourArea(contour)
        
        # 設定面積過濾條件 (根據解析度不同，這裡的值可能要調整)
        # P 圖示通常不會太小，也不會大到佔滿螢幕
        if 200 < area < 5000: 
            x, y, w, h = cv2.boundingRect(contour)
            
            # 計算長寬比，圓形的 P 圖示長寬比應該接近 1
            aspect_ratio = float(w) / h
            if 0.7 < aspect_ratio < 1.3:
                # 畫出框框標記找到的位置
                cv2.rectangle(img, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(img, "Parking P", (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                found_p = True

    # 除錯用：您可以儲存 mask 看看是否有抓到正確的白色區域
    # cv2.imwrite('debug_mask.jpg', mask)
    
    return found_p, img

# --- 使用範例 ---
if __name__ == "__main__":
    # 這裡您可以放入您的截圖路徑進行測試
    # 例如: image_path = "screenshot.jpg"
    
    # 模擬讀取一張圖片 (或是連接 uiautomator2 截圖)
    # import uiautomator2 as u2
    # d = u2.connect()
    # img = d.screenshot(format='opencv')
    
    # 假設我們讀取目錄下的一張測試圖片 (請自行更換為您的截圖)
    test_img_path = "park.jpg" 
    
    if os.path.exists(test_img_path):
        result, debug_img = detect_blue_p(test_img_path)
        print(f"偵測結果: {'正在停車中 (發現藍色P)' if result else '未發現停車狀態'}")
        
        if debug_img is not None:
            cv2.imwrite("debug_result.jpg", debug_img)
            print("已儲存標記結果為 debug_result.jpg")
    else:
        print("請修改程式碼中的 test_img_path 為您的截圖路徑以進行測試。")
