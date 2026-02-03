"""
檢查資料集的分類信心度,找出可能有問題的樣本
使用訓練好的 CNN 模型進行檢查
"""
import os
import shutil
from pathlib import Path
from typing import Dict, List, Tuple
import argparse

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision import transforms
from torchvision.datasets import ImageFolder
import numpy as np
from tqdm import tqdm

from simplecnn import SimpleCNN, resize_size


def parse_args():
    parser = argparse.ArgumentParser(description='使用 CNN 模型檢查資料集中信心度低的樣本')
    parser.add_argument('--dataset-dir', type=str, 
                       default=r'A:\菇勇者全自動掛機\dataset\low_confidence',
                       help='資料集根目錄(包含類別子資料夾)或單一類別資料夾')
    parser.add_argument('--model-path', type=str,
                       default='checkpoints/best.pth',
                       help='訓練好的模型檔案路徑')
    parser.add_argument('--threshold', type=float, default=0.95,
                       help='信心度閾值,低於此值的樣本會被標記(用於統計)')
    parser.add_argument('--move-threshold', type=float, default=0.8,
                       help='移動閾值,只有信心度高於此值的樣本才會被移動')
    parser.add_argument('--output-dir', type=str,
                       default=r'A:\菇勇者全自動掛機\dataset\low_confidence',
                       help='輸出高信心度樣本的目錄(按預測類別分類)')
    parser.add_argument('--error-dir', type=str,
                       default='dataset/error',
                       help='輸出預測錯誤樣本的目錄')
    parser.add_argument('--move-files', action='store_true',
                       help='是否移動(而非複製)低信心度和錯誤檔案')
    parser.add_argument('--batch-size', type=int, default=32,
                       help='批次大小')
    parser.add_argument('--device', type=str, 
                       default='cuda' if torch.cuda.is_available() else 'cpu',
                       help='使用的裝置')
    return parser.parse_args()


def make_transforms():
    """創建與訓練時相同的轉換"""
    sz = resize_size
    return transforms.Compose([
        transforms.Resize(sz),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ])


def load_model(model_path: str, device: torch.device) -> Tuple[SimpleCNN, List[str]]:
    """載入訓練好的模型"""
    checkpoint = torch.load(model_path, map_location=device)
    
    classes = checkpoint.get('classes', [])
    num_classes = len(classes)
    
    model = SimpleCNN(num_classes=num_classes)
    model.load_state_dict(checkpoint['model_state'])
    model.to(device)
    model.eval()
    
    print(f"已載入模型: {model_path}")
    print(f"類別數: {num_classes}")
    print(f"類別: {classes}")
    
    return model, classes


def detect_dataset_type(dataset_dir: Path) -> Tuple[bool, str]:
    """
    自動檢測資料夾類型（快速版本，只檢查前幾個項目）
    
    Returns:
        (is_single_class, class_name)
        - is_single_class: True 表示單一類別資料夾
        - class_name: 如果是單一類別,返回類別名稱;否則為空字串
    """
    # 使用 os.scandir 快速檢查（只檢查前幾個項目）
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
    
    has_images = False
    has_subdirs = False
    check_count = 0
    max_check = 20  # 只檢查前 20 個項目
    
    try:
        with os.scandir(dataset_dir) as entries:
            for entry in entries:
                if check_count >= max_check:
                    break
                
                try:
                    # 使用 entry.is_file() 和 entry.is_dir() 更快
                    if entry.is_file(follow_symlinks=False):
                        ext = os.path.splitext(entry.name)[1].lower()
                        if ext in image_extensions:
                            has_images = True
                            if has_subdirs:  # 如果已經找到子資料夾，可以提前退出
                                break
                    elif entry.is_dir(follow_symlinks=False):
                        has_subdirs = True
                        if has_images:  # 如果已經找到圖片，可以提前退出
                            break
                except OSError:
                    # 跳過無法訪問的項目
                    pass
                
                check_count += 1
    except Exception as e:
        print(f"檢測資料夾類型時發生錯誤: {e}")
        # 發生錯誤時，假設是多類別資料夾
        return False, ""
    
    # 如果有圖片檔案,視為單一類別資料夾
    if has_images:
        class_name = dataset_dir.name
        print(f"檢測到單一類別資料夾: {class_name}")
        return True, class_name
    # 如果只有子資料夾,視為多類別資料夾
    elif has_subdirs:
        print(f"檢測到多類別資料夾")
        return False, ""
    else:
        print(f"警告: 資料夾內沒有圖片也沒有子資料夾（或資料夾為空）")
        return False, ""


def check_dataset_with_model(
    model: SimpleCNN,
    dataset: ImageFolder,
    classes: List[str],
    device: torch.device,
    batch_size: int,
    threshold: float
) -> Tuple[List[Dict], List[Dict]]:
    """
    使用模型檢查整個資料集
    
    Returns:
        (低信心度樣本列表, 所有樣本列表)
    """
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, num_workers=2)
    
    low_conf_samples = []
    all_samples = []
    
    with torch.no_grad():
        for batch_idx, (images, targets) in enumerate(tqdm(loader, desc="檢查資料集")):
            images = images.to(device)
            targets = targets.to(device)
            
            # 前向傳播
            outputs = model(images)
            probs = F.softmax(outputs, dim=1)
            
            # 獲取最高信心度和預測類別
            confidences, preds = torch.max(probs, dim=1)
            
            # 處理批次中的每個樣本
            for i in range(len(images)):
                idx = batch_idx * batch_size + i
                if idx >= len(dataset.samples):
                    break
                
                img_path, true_label_idx = dataset.samples[idx]
                pred_label_idx = preds[i].item()
                confidence = confidences[i].item()
                
                true_label = classes[true_label_idx]
                pred_label = classes[pred_label_idx]
                
                sample_info = {
                    'path': img_path,
                    'filename': Path(img_path).name,
                    'true_label': true_label,
                    'pred_label': pred_label,
                    'confidence': confidence,
                    'correct': pred_label == true_label,
                    'probs': probs[i].cpu().numpy()  # 所有類別的機率
                }
                
                all_samples.append(sample_info)
                
                # 如果信心度低於閾值或預測錯誤
                if confidence < threshold or not sample_info['correct']:
                    low_conf_samples.append(sample_info)
    
    return low_conf_samples, all_samples


def check_single_class_folder(
    model: SimpleCNN,
    folder_path: Path,
    true_class_name: str,
    model_classes: List[str],
    device: torch.device,
    batch_size: int,
    threshold: float,
    move_threshold: float = 0.95,
    output_dir: Path = None,
    move_files: bool = False
) -> Tuple[List[Dict], List[Dict]]:
    """
    檢查單一類別資料夾中的所有圖片（使用批次處理加速，支援即時移動）
    
    Args:
        folder_path: 包含圖片的資料夾路徑
        true_class_name: 這個資料夾代表的真實類別名稱
        model_classes: 模型訓練時的類別列表
        move_threshold: 移動閾值，信心度 >= 此值的樣本會被立即移動
        output_dir: 輸出目錄（用於移動高信心度樣本）
        move_files: 是否立即移動檔案
        
    Returns:
        (低信心度樣本列表, 所有樣本列表)
    """
    from PIL import Image
    
    # 快速獲取所有圖片檔案（使用 os.scandir 更快）
    print(f"掃描資料夾: {folder_path}")
    image_extensions = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}
    image_files = []
    
    try:
        with os.scandir(folder_path) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    ext = os.path.splitext(entry.name)[1].lower()
                    if ext in image_extensions:
                        image_files.append(entry.path)
    except Exception as e:
        print(f"掃描資料夾時發生錯誤: {e}")
        return [], []
    
    print(f"找到 {len(image_files)} 個圖片檔案")
    
    if len(image_files) == 0:
        return [], []
    
    transform = make_transforms()
    low_conf_samples = []
    all_samples = []
    
    # 統計計數器
    moved_count = 0
    low_conf_count = 0
    
    # 確認真實類別在模型類別中
    if true_class_name not in model_classes:
        print(f"警告: 類別 '{true_class_name}' 不在模型訓練的類別中!")
        print(f"模型類別: {model_classes}")
    
    # 如果需要移動檔案，準備輸出目錄
    if move_files and output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"即時移動模式: 信心度 >= {move_threshold} 的樣本將立即移動到 {output_dir}")
    
    model.eval()
    
    # 批次處理生成器
    def batch_generator(files, batch_size):
        """生成批次的圖片"""
        for i in range(0, len(files), batch_size):
            yield files[i:i + batch_size]
    
    with torch.no_grad():
        pbar = tqdm(total=len(image_files), desc=f"檢查 {true_class_name}")
        
        for batch_files in batch_generator(image_files, batch_size):
            batch_images = []
            batch_paths = []
            
            # 載入批次中的所有圖片
            for img_path in batch_files:
                try:
                    image = Image.open(img_path).convert('RGB')
                    image_tensor = transform(image)
                    batch_images.append(image_tensor)
                    batch_paths.append(img_path)
                except Exception as e:
                    print(f"\n載入圖片失敗: {Path(img_path).name}")
                    pbar.update(1)
                    continue
            
            if not batch_images:
                continue
            
            # 批次前向傳播
            try:
                batch_tensor = torch.stack(batch_images).to(device)
                outputs = model(batch_tensor)
                probs = F.softmax(outputs, dim=1)
                
                # 獲取預測結果
                confidences, pred_indices = torch.max(probs, dim=1)
                
                # 處理批次結果
                for i, img_path in enumerate(batch_paths):
                    confidence = confidences[i].item()
                    pred_label = model_classes[pred_indices[i].item()]
                    
                    sample_info = {
                        'path': img_path,
                        'filename': Path(img_path).name,
                        'true_label': true_class_name,
                        'pred_label': pred_label,
                        'confidence': confidence,
                        'correct': pred_label == true_class_name,
                        'probs': probs[i].cpu().numpy()
                    }
                    
                    all_samples.append(sample_info)
                    
                    # 判斷是否需要立即移動（信心度 >= move_threshold）
                    if move_files and output_dir and confidence >= move_threshold:
                        try:
                            # 在 output_dir 下創建預測類別的子資料夾
                            subdir = output_dir / pred_label
                            subdir.mkdir(parents=True, exist_ok=True)
                            
                            # 移動檔案
                            src_path = Path(img_path)
                            dst_path = subdir / src_path.name
                            
                            # 如果目標檔案已存在，加上序號避免覆蓋
                            counter = 1
                            while dst_path.exists():
                                dst_path = subdir / f"{src_path.stem}_{counter}{src_path.suffix}"
                                counter += 1
                            
                            shutil.move(str(src_path), str(dst_path))
                            moved_count += 1
                        except Exception as e:
                            print(f"\n移動檔案失敗 {Path(img_path).name}: {e}")
                    
                    # 統計低信心度樣本
                    if confidence < threshold or not sample_info['correct']:
                        low_conf_samples.append(sample_info)
                        low_conf_count += 1
                    
                    pbar.update(1)
            except Exception as e:
                print(f"\n批次處理失敗: {e}")
                pbar.update(len(batch_files))
                continue
        
        pbar.close()
    
    # 顯示移動統計
    if move_files and output_dir:
        print(f"\n已立即移動 {moved_count} 個高信心度樣本 (>= {move_threshold})")
        print(f"保留 {low_conf_count} 個低信心度樣本在原位")
    
    return low_conf_samples, all_samples


def print_statistics(low_conf_samples: List[Dict], all_samples: List[Dict]):
    """印出統計資訊"""
    total = len(all_samples)
    low_conf_count = len(low_conf_samples)
    
    print(f"\n{'='*80}")
    print(f"統計結果")
    print(f"{'='*80}")
    print(f"總樣本數: {total}")
    
    if total == 0:
        print("警告: 沒有找到任何樣本!")
        return
    
    print(f"低信心度樣本數: {low_conf_count} ({low_conf_count/total*100:.2f}%)")
    
    # 統計預測錯誤的樣本
    wrong_predictions = [s for s in all_samples if not s['correct']]
    print(f"預測錯誤樣本數: {len(wrong_predictions)} ({len(wrong_predictions)/total*100:.2f}%)")
    
    # 按類別統計
    print(f"\n按真實類別統計低信心度樣本:")
    from collections import defaultdict
    class_stats = defaultdict(list)
    
    for sample in low_conf_samples:
        class_stats[sample['true_label']].append(sample)
    
    for class_name in sorted(class_stats.keys()):
        samples = class_stats[class_name]
        avg_conf = np.mean([s['confidence'] for s in samples])
        wrong = sum(1 for s in samples if not s['correct'])
        print(f"  {class_name:20s}: {len(samples):4d} 個低信心度樣本 "
              f"(平均信心度: {avg_conf:.4f}, 錯誤: {wrong})")
    
    # 列出最低信心度的樣本
    print(f"\n最低信心度的 20 個樣本:")
    sorted_samples = sorted(low_conf_samples, key=lambda x: x['confidence'])
    for i, sample in enumerate(sorted_samples[:20], 1):
        status = "✗" if not sample['correct'] else "✓"
        print(f"  {i:2d}. {status} {sample['filename']:40s} "
              f"真實={sample['true_label']:20s} "
              f"預測={sample['pred_label']:20s} "
              f"信心度={sample['confidence']:.4f}")


def move_problem_samples(
    low_conf_samples: List[Dict], 
    all_samples: List[Dict],
    low_conf_dir: Path,
    error_dir: Path,
    threshold: float = 0.95,
    single_class_mode: bool = False,
    source_dir: Path = None
):
    """
    移動高信心度樣本到 output-dir 下按預測類別分類
    
    Args:
        threshold: 信心度閾值，只有高於此值的樣本才會被移動
        low_conf_dir: 輸出目錄 (output-dir)
        single_class_mode: 如果為 True,則在源資料夾內創建子資料夾分類
        source_dir: 源資料夾路徑(單一類別模式時使用)
    """
    # 篩選高信心度樣本：信心度 >= threshold
    high_conf_samples = [s for s in all_samples if s['confidence'] >= threshold]
    low_conf_samples_count = [s for s in all_samples if s['confidence'] < threshold]
    
    print(f"\n移動高信心度樣本:")
    print(f"  信心度 >= {threshold} 的樣本: {len(high_conf_samples)} 個 (將被移動)")
    print(f"  信心度 < {threshold} 的樣本: {len(low_conf_samples_count)} 個 (保留在原位)")
    
    if not high_conf_samples:
        print(f"  沒有信心度 >= {threshold} 的樣本需要移動")
        return
    
    # 移動高信心度樣本到 output-dir/預測類別/
    low_conf_dir.mkdir(parents=True, exist_ok=True)
    
    for sample in tqdm(high_conf_samples, desc="移動高信心度樣本"):
        pred_label = sample['pred_label']
        
        # 在 output-dir 下創建預測類別的子資料夾
        subdir = low_conf_dir / pred_label
        subdir.mkdir(parents=True, exist_ok=True)
        
        # 保持原檔名移動
        src_path = Path(sample['path'])
        dst_path = subdir / src_path.name
        
        # 如果目標檔案已存在,加上序號避免覆蓋
        counter = 1
        while dst_path.exists():
            dst_path = subdir / f"{src_path.stem}_{counter}{src_path.suffix}"
            counter += 1
        
        # 移動檔案
        shutil.move(str(src_path), str(dst_path))
    
    print(f"\n✓ 已移動 {len(high_conf_samples)} 個高信心度樣本到: {low_conf_dir}")
    print(f"✓ {len(low_conf_samples_count)} 個低信心度樣本保留在原位")


def save_report(low_conf_samples: List[Dict], all_samples: List[Dict], output_dir: Path):
    """儲存詳細報告"""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / 'report.txt'
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("=" * 80 + "\n")
        f.write("資料集信心度檢查報告 (使用 CNN 模型)\n")
        f.write("=" * 80 + "\n\n")
        
        f.write(f"總樣本數: {len(all_samples)}\n")
        f.write(f"低信心度樣本數: {len(low_conf_samples)}\n")
        f.write(f"預測錯誤樣本數: {sum(1 for s in all_samples if not s['correct'])}\n\n")
        
        f.write("低信心度樣本列表:\n")
        f.write("-" * 80 + "\n")
        
        for sample in sorted(low_conf_samples, key=lambda x: x['confidence']):
            status = "錯誤" if not sample['correct'] else "正確"
            f.write(f"{sample['filename']}\n")
            f.write(f"  真實類別: {sample['true_label']}\n")
            f.write(f"  預測類別: {sample['pred_label']}\n")
            f.write(f"  信心度: {sample['confidence']:.4f}\n")
            f.write(f"  預測狀態: {status}\n")
            f.write(f"  路徑: {sample['path']}\n")
            f.write("-" * 80 + "\n")
    
    print(f"詳細報告已儲存至: {report_path}")


def main():
    args = parse_args()
    
    dataset_dir = Path(args.dataset_dir)
    low_conf_dir = Path(args.output_dir)
    error_dir = Path(args.error_dir)
    model_path = Path(args.model_path)
    
    if not dataset_dir.exists():
        print(f"錯誤: 資料集目錄不存在: {dataset_dir}")
        return
    
    if not model_path.exists():
        print(f"錯誤: 模型檔案不存在: {model_path}")
        return
    
    # 設定裝置
    device = torch.device(args.device)
    print(f"使用裝置: {device}")
    
    # 載入模型
    model, classes = load_model(str(model_path), device)
    
    # 自動檢測資料夾類型
    is_single_class, class_name = detect_dataset_type(dataset_dir)
    
    if is_single_class:
        # 單一類別資料夾模式
        print(f"\n單一類別模式: {class_name}")
        print(f"  資料夾路徑: {dataset_dir}")
        print(f"  信心度閾值: {args.threshold}")
        print(f"  移動閾值: {args.move_threshold}")
        
        # 檢查單一類別資料夾（支援即時移動）
        low_conf_samples, all_samples = check_single_class_folder(
            model, dataset_dir, class_name, classes, 
            device, args.batch_size, args.threshold,
            move_threshold=args.move_threshold,
            output_dir=low_conf_dir,
            move_files=args.move_files
        )
    else:
        # 標準多類別資料夾模式
        transform = make_transforms()
        dataset = ImageFolder(root=str(dataset_dir), transform=transform)
        
        print(f"\n資料集資訊:")
        print(f"  路徑: {dataset_dir}")
        print(f"  類別數: {len(dataset.classes)}")
        print(f"  總樣本數: {len(dataset)}")
        print(f"  信心度閾值: {args.threshold}")
        
        # 檢查資料集
        low_conf_samples, all_samples = check_dataset_with_model(
            model, dataset, classes, device, args.batch_size, args.threshold
        )
    
    # 顯示統計資訊
    print_statistics(low_conf_samples, all_samples)
    
    # 儲存報告
    save_report(low_conf_samples, all_samples, low_conf_dir)
    
    # 如果是單一類別模式且已經即時移動，就不需要再次移動
    if is_single_class and args.move_files:
        print(f"\n✓ 檔案已在檢查過程中即時移動")
    elif args.move_files:
        # 多類別模式：移動檔案
        move_problem_samples(
            low_conf_samples, all_samples, low_conf_dir, error_dir, 
            args.move_threshold, 
            single_class_mode=is_single_class,
            source_dir=dataset_dir if is_single_class else None
        )
    else:
        # 預覽會被移動的數量
        high_conf_count = sum(1 for s in all_samples if s['confidence'] >= args.move_threshold)
        low_conf_count = sum(1 for s in all_samples if s['confidence'] < args.move_threshold)
        
        print(f"\n提示: 使用 --move-files 參數可將高信心度樣本即時移動並分類")
        print(f"  - {high_conf_count} 個高信心度樣本 (>= {args.move_threshold}) → {low_conf_dir} (按預測類別分類)")
        print(f"  - {low_conf_count} 個低信心度樣本 (< {args.move_threshold}) → 保留在原位")
    
    print(f"\n完成!")


if __name__ == '__main__':
    main()
