import cv2
import img_tools
import uiautomator2 as u2
import json

def analyze():
    d = u2.connect('emulator-5556')
    img = d.screenshot(format='opencv')
    
    # 1. 辨識右下角指引 (假設在右下角 1/4 區域)
    h, w = img.shape[:2]
    roi_guide = img[int(h*0.7):, int(w*0.5):]
    guide_result = img_tools.analyze_skill_via_http(roi_guide)
    print("Guide OCR Results:", json.dumps(guide_result, ensure_ascii=False, indent=2))
    
    # 2. 全螢幕 OCR 找關鍵字
    full_result = img_tools.analyze_skill_via_http(img)
    print("Full OCR Results:", json.dumps(full_result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    analyze()
