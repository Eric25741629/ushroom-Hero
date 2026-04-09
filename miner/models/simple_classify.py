"""
最簡單的圖片分類器：讀取圖片 → 判斷類別 → 移動到對應資料夾
"""
import os
import shutil
from pathlib import Path
import argparse

import torch
import torch.nn.functional as F
from torchvision import transforms
from PIL import Image
from tqdm import tqdm

from simplecnn import SimpleCNN, resize_size


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOW_CONFIDENCE_DIR = PROJECT_ROOT / 'miner' / 'dataset' / 'low_confidence'


def main():
    # 參數設定
    parser = argparse.ArgumentParser(description='簡單圖片分類移動工具')
    parser.add_argument('--input-dir', type=str, 
                       default=str(DEFAULT_LOW_CONFIDENCE_DIR),
                       help='輸入資料夾（包含要分類的圖片）')
    parser.add_argument('--output-dir', type=str,
                       default=str(DEFAULT_LOW_CONFIDENCE_DIR),
                       help='輸出資料夾（會在此建立類別子資料夾）')
    parser.add_argument('--model-path', type=str,
                       default='checkpoints/best.pth',
                       help='模型檔案路徑')
    parser.add_argument('--threshold', type=float, default=0.95,
                       help='信心度閾值（低於此值不移動）')
    parser.add_argument('--device', type=str, 
                       default='cuda' if torch.cuda.is_available() else 'cpu',
                       help='使用的裝置 (cuda/cpu)')
    args = parser.parse_args()
    
    # 檢查路徑
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    model_path = Path(args.model_path)
    
    if not input_dir.exists():
        print(f"錯誤: 輸入資料夾不存在: {input_dir}")
        return
    
    if not model_path.exists():
        print(f"錯誤: 模型檔案不存在: {model_path}")
        return
    
    # 設定裝置
    device = torch.device(args.device)
    print(f"使用裝置: {device}")
    
    # 載入模型
    print(f"載入模型: {model_path}")
    checkpoint = torch.load(model_path, map_location=device)
    classes = checkpoint.get('classes', [])
    num_classes = len(classes)
    
    model = SimpleCNN(num_classes=num_classes)
    model.load_state_dict(checkpoint['model_state'])
    model.to(device)
    model.eval()
    
    print(f"類別數: {num_classes}")
    print(f"類別: {classes}")
    
    # 圖片轉換
    transform = transforms.Compose([
        transforms.Resize(resize_size),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])
    
    # 獲取所有圖片
    print(f"\n掃描資料夾: {input_dir}")
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
    image_files = []
    
    try:
        with os.scandir(input_dir) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in image_extensions:
                        image_files.append(entry.path)
    except Exception as e:
        print(f"掃描資料夾時發生錯誤: {e}")
        return
    
    print(f"找到 {len(image_files)} 個圖片檔案")
    
    if len(image_files) == 0:
        print("沒有圖片需要處理")
        return
    
    # 準備輸出資料夾
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 統計
    moved_count = 0
    skipped_count = 0
    
    print(f"\n開始分類...")
    print(f"信心度閾值: {args.threshold}")
    
    # 逐一處理圖片
    with torch.no_grad():
        for img_path in tqdm(image_files, desc="分類中"):
            try:
                # 載入圖片
                image = Image.open(img_path).convert('RGB')
                image_tensor = transform(image).unsqueeze(0).to(device)
                
                # 預測
                output = model(image_tensor)
                probs = F.softmax(output, dim=1)
                confidence, pred_idx = torch.max(probs, dim=1)
                
                confidence = confidence.item()
                pred_label = classes[pred_idx.item()]
                
                # 判斷是否移動
                if confidence >= args.threshold:
                    # 創建類別資料夾
                    class_dir = output_dir / pred_label
                    class_dir.mkdir(parents=True, exist_ok=True)
                    
                    # 移動檔案
                    src_path = Path(img_path)
                    dst_path = class_dir / src_path.name
                    
                    # 避免檔名衝突
                    counter = 1
                    while dst_path.exists():
                        dst_path = class_dir / f"{src_path.stem}_{counter}{src_path.suffix}"
                        counter += 1
                    
                    shutil.move(str(src_path), str(dst_path))
                    moved_count += 1
                else:
                    skipped_count += 1
                    
            except Exception as e:
                print(f"\n處理失敗 {Path(img_path).name}: {e}")
                skipped_count += 1
                continue
    
    # 顯示結果
    print(f"\n完成!")
    print(f"已移動: {moved_count} 個圖片")
    print(f"跳過: {skipped_count} 個圖片 (信心度 < {args.threshold})")
    print(f"輸出位置: {output_dir}")


if __name__ == '__main__':
    main()
