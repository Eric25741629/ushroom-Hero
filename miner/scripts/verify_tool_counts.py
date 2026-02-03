import cv2
import requests
import base64
import json
import os

OCR_URL = "http://127.0.0.1:5001/ocr"

def verify_full_numbers(image_path):
    img = cv2.imread(image_path)
    
    # 擴大範圍以尋找 4 位數
    # 鑽頭區域 (原本 175:200) -> 擴大為 140:210
    drill_roi = img[910:950, 140:210]
    # 炸彈區域 (原本 385:410) -> 擴大為 350:420
    bomb_roi = img[910:950, 350:420]
    
    print("--- 驗證鑽頭 (預期 992) ---")
    call_and_print(drill_roi, "drill_wide")
    
    print("\n--- 驗證炸彈 (預期 1102) ---")
    call_and_print(bomb_roi, "bomb_wide")

def call_and_print(roi, name):
    cv2.imwrite(f"debug_{name}.png", roi)
    # 二值化處理
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, bin_img = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)
    cv2.imwrite(f"debug_{name}_bin.png", bin_img)
    
    _, buffer = cv2.imencode('.png', bin_img)
    img_base64 = base64.b64encode(buffer).decode('utf-8')
    try:
        response = requests.post(OCR_URL, json={"image": img_base64}, timeout=5)
        print(f"辨識結果: {response.json().get('ocr_results', [{}])[0].get('text', 'N/A')}")
    except Exception as e:
        print(f"失敗: {e}")

if __name__ == "__main__":
    verify_full_numbers("debug_img/MuMu-20260130-201408-661.png")
