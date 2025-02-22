
import pytesseract
from PIL import Image
import uiautomator2 as u2
from paddleocr import PaddleOCR, draw_ocr
from matplotlib import pyplot as plt
import cv2  # opencv
import os

d = u2.connect('emulator-5554')

img = d.screenshot(format='pillow', filename="test.png")
ocr_model = PaddleOCR(lang='chinese_cht', use_gpu=False)
img_path = 'test.png'
result = ocr_model.ocr(img_path, cls=True)
