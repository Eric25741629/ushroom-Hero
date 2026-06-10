import requests
import cv2
import base64
import os
import glob

def encode_image(img):
    _, buffer = cv2.imencode('.jpg', img)
    return base64.b64encode(buffer).decode('utf-8')

def test_brain(image_path, server_url="http://127.0.0.1:5002/api/analyze_stage"):
    """發送單張圖片測試大腦判定"""
    if not os.path.exists(image_path):
        print(f"找不到檔案: {image_path}")
        return

    img = cv2.imread(image_path)
    img_b64 = encode_image(img)
    
    try:
        resp = requests.post(server_url, json={"image": img_b64}, timeout=5)
        if resp.status_code == 200:
            result = resp.json()
            print(f"檔案: {os.path.basename(image_path)}")
            print(f"  -> 判定結果: {result.get('stage')}")
            print(f"  -> OCR 文字: {result.get('ocr_text')}")
        else:
            print(f"伺服器錯誤: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"連線失敗: {e}")

if __name__ == "__main__":
    # 測試 debug_img 資料夾中的所有 jpg
    test_images = glob.glob("debug_img/*.jpg") + glob.glob("*.jpg")
    
    if not test_images:
        print("請將欲測試的截圖放入目錄下。")
    else:
        for img_path in test_images[:5]: # 先測前 5 張
            test_brain(img_path)
            print("-" * 30)
