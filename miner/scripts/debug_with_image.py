import cv2
import os
import sys
import torch
from miner.models.classifier import ClassifierCNN, load_cnn_model
from miner.planning.smart_planner import plan_smart
from miner.mining_service import get_visual_board

def debug_image(image_path: str):
    if not os.path.exists(image_path):
        print(f"找不到圖片: {image_path}")
        return

    print(f"正在載入模型與圖片: {image_path}...")
    
    # 1. 載入模型 (模擬 new_main 的行為)
    # 注意：這裡假設您的模型在 miner/models/checkpoints/best.pth
    # 如果 new_main 用的是根目錄的 model，這裡可能要改
    # new_main: oracle_cnn_model, oracle_classes, resolved_device = load_miner_cnn_model()
    # load_miner_cnn_model 其實就是 miner.models.classifier.load_cnn_model
    
    model, classes, device = load_cnn_model() # 使用預設路徑
    
    clf = ClassifierCNN(model=model, classes=classes, device=device)
    
    # 2. 讀取圖片並辨識
    img = cv2.imread(image_path)
    board, confidences = clf.classify_board(img, save_samples=False)
    
    print("\n[AI 眼中的盤面]")
    print(get_visual_board(board))
    
    # 3. 執行規劃
    # 假設鎬子充足，有鑽頭炸彈
    items = {'drill': 10, 'bomb': 10}
    shovels = 100
    
    print(f"\n[開始規劃] 鎬子: {shovels}, 道具: {items}")
    plan = plan_smart(board, shovels=shovels, items=items)
    
    print("\n[規劃結果]")
    if plan['ok']:
        print(f"訊息: {plan.get('message')}")
        steps = plan.get('steps', [])
        if not steps:
            print("⚠️ 警告: 回傳了空步驟 (No steps)")
        for i, step in enumerate(steps):
            print(f"Step {i+1}: {step}")
            
        print(f"預估成本: {plan.get('total_cost')}")
        print(f"剩餘礦物: {plan.get('remaining_pits')}")
        print(f"第七層通: {plan.get('floor7_open')}")
    else:
        print(f"❌ 規劃失敗: {plan.get('message')}")

if __name__ == "__main__":
    # 使用方式: python -m miner.scripts.debug_with_image <圖片路徑>
    if len(sys.argv) > 1:
        debug_image(sys.argv[1])
    else:
        # 預設找一張圖來測 (如果有)
        print("請提供圖片路徑，例如: python -m miner.scripts.debug_with_image debug_full.jpg")
