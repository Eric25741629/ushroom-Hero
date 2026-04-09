import argparse
import json
import os
from pathlib import Path
import time
from collections import Counter

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split, WeightedRandomSampler
from torchvision import transforms
from torchvision.datasets import ImageFolder
import numpy as np

from simplecnn import SimpleCNN, resize_size


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / 'dataset' / 'mines'


def parse_args():
    p = argparse.ArgumentParser(description='Train SimpleCNN on mines dataset')
    p.add_argument('--data-dir', type=str, default=str(DEFAULT_DATA_DIR),
                   help='root of dataset with class subfolders')
    p.add_argument('--epochs', type=int, default=10)
    p.add_argument('--batch-size', type=int, default=32)
    p.add_argument('--lr', type=float, default=1e-3)
    p.add_argument('--val-split', type=float, default=0.3)
    p.add_argument('--save-dir', type=str, default='checkpoints')
    p.add_argument('--num-workers', type=int, default=3)
    p.add_argument('--device', type=str, default='cuda' if torch.cuda.is_available() else 'cpu')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--use-weighted-loss', action='store_true', default=False,
                   help='使用加權損失函數來平衡類別')
    p.add_argument('--use-weighted-sampler', action='store_true', default=False,
                   help='使用加權採樣器來平衡訓練批次')
    return p.parse_args()


def make_transforms(is_train=False):
    """
    建立資料轉換管道
    is_train=True: 包含資料增強(平移、翻轉)
    is_train=False: 僅包含基本轉換(用於驗證)
    """
    sz = resize_size
    
    if is_train:
        # 訓練集使用資料增強
        return transforms.Compose([
            transforms.Resize(sz),
            transforms.RandomHorizontalFlip(p=0.5),  # 隨機水平翻轉
            transforms.RandomAffine(
                degrees=0,           # 不旋轉
                translate=(0.1, 0.1),  # 最多平移 10%
                scale=None,          # 不縮放
                shear=None           # 不剪切
            ),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])
    else:
        # 驗證集不使用資料增強
        return transforms.Compose([
            transforms.Resize(sz),
            transforms.ToTensor(),
            transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
        ])


def compute_class_weights(dataset, device):
    """計算類別權重,用於加權損失函數"""
    # 統計每個類別的樣本數
    targets = [label for _, label in dataset.samples]
    class_counts = Counter(targets)
    
    num_classes = len(dataset.classes)
    counts = [class_counts[i] for i in range(num_classes)]
    
    # 使用反比例權重: weight = 1 / count
    # 或使用平方根反比例: weight = 1 / sqrt(count)
    total_samples = sum(counts)
    weights = [total_samples / count for count in counts]
    
    # 正規化權重
    weight_sum = sum(weights)
    weights = [w / weight_sum * num_classes for w in weights]
    
    print("\n類別樣本分布:")
    for i, cls in enumerate(dataset.classes):
        print(f"  {cls:20s}: {counts[i]:4d} 樣本, 權重: {weights[i]:.4f}")
    
    return torch.tensor(weights, dtype=torch.float32).to(device)


def make_weighted_sampler(dataset, indices):
    """為訓練集創建加權採樣器,確保每個批次中類別平衡"""
    # 獲取訓練集中的標籤
    targets = [dataset.samples[idx][1] for idx in indices]
    class_counts = Counter(targets)
    
    # 計算每個樣本的權重(類別越少,權重越高)
    weights = [1.0 / class_counts[t] for t in targets]
    
    sampler = WeightedRandomSampler(
        weights=weights,
        num_samples=len(weights),
        replacement=True  # 允許重複採樣
    )
    
    return sampler


def save_checkpoint(state, save_path: Path):
    save_path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(state, str(save_path))


def train_one_epoch(model, loader, criterion, optimizer, device):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0
    for images, targets in loader:
        images = images.to(device)
        targets = targets.to(device)

        outputs = model(images)
        loss = criterion(outputs, targets)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        running_loss += loss.item() * images.size(0)
        _, preds = outputs.max(1)
        correct += (preds == targets).sum().item()
        total += images.size(0)

    avg_loss = running_loss / total if total else 0.0
    acc = correct / total if total else 0.0
    return avg_loss, acc


def validate(model, loader, criterion, device, class_names=None):
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0
    
    # 用於計算每個類別的準確率
    class_correct = {}
    class_total = {}
    
    with torch.no_grad():
        for images, targets in loader:
            images = images.to(device)
            targets = targets.to(device)
            outputs = model(images)
            loss = criterion(outputs, targets)

            running_loss += loss.item() * images.size(0)
            _, preds = outputs.max(1)
            correct += (preds == targets).sum().item()
            total += images.size(0)
            
            # 統計每個類別的準確率
            for t, p in zip(targets.cpu().numpy(), preds.cpu().numpy()):
                if t not in class_total:
                    class_total[t] = 0
                    class_correct[t] = 0
                class_total[t] += 1
                if t == p:
                    class_correct[t] += 1

    avg_loss = running_loss / total if total else 0.0
    acc = correct / total if total else 0.0
    
    # 計算每個類別的準確率
    class_acc = {}
    if class_names:
        for cls_idx in class_total:
            cls_name = class_names[cls_idx]
            cls_acc = class_correct[cls_idx] / class_total[cls_idx]
            class_acc[cls_name] = (cls_acc, class_total[cls_idx])
    
    return avg_loss, acc, class_acc


def main():
    args = parse_args()

    torch.manual_seed(args.seed)

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        raise SystemExit(f'data directory not found: {data_dir}')

    # 創建訓練集和驗證集的轉換(訓練集包含資料增強)
    transform_train = make_transforms(is_train=True)
    transform_val = make_transforms(is_train=False)
    
    # 先用訓練轉換載入完整資料集
    dataset_full = ImageFolder(root=str(data_dir), transform=transform_train)

    num_classes = len(dataset_full.classes)
    if num_classes == 0:
        raise SystemExit('No classes found in dataset root')

    # 每個類別按 7:3 比例劃分訓練集和驗證集
    train_ratio = 0.7  # 70% 訓練, 30% 驗證
    min_samples_per_class = 5  # 每個類別至少需要這麼多樣本
    
    # 按類別分組樣本索引
    class_indices = {i: [] for i in range(num_classes)}
    for idx, (_, label) in enumerate(dataset_full.samples):
        class_indices[label].append(idx)
    
    # 為每個類別選擇訓練和驗證樣本
    train_indices = []
    val_indices = []
    
    print(f"\n每個類別按 {train_ratio:.0%}:{1-train_ratio:.0%} 比例劃分訓練/驗證集:")
    for class_idx, indices in class_indices.items():
        class_name = dataset_full.classes[class_idx]
        total = len(indices)    
        
        # 打亂該類別的索引
        np.random.shuffle(indices)
        
        if total < min_samples_per_class:
            # 樣本太少，警告但仍然使用
            n_train = max(1, int(total * train_ratio))
            n_val = total - n_train
            train_indices.extend(indices[:n_train])
            val_indices.extend(indices[n_train:])
            print(f"  {class_name:20s}: {n_train:3d} 訓練, {n_val:3d} 驗證 (總共 {total:3d}) ⚠⚠ 樣本不足")
        else:
            # 正常劃分: 7:3 比例
            n_train = int(total * train_ratio)
            n_val = total - n_train
            
            # 確保驗證集至少有1個樣本
            if n_val == 0:
                n_val = 1
                n_train = total - 1
            
            train_indices.extend(indices[:n_train])
            val_indices.extend(indices[n_train:n_train + n_val])
            print(f"  {class_name:20s}: {n_train:3d} 訓練, {n_val:3d} 驗證 (總共 {total:3d}) ✓")
    
    train_size = len(train_indices)
    val_size = len(val_indices)
    
    if train_size == 0:
        raise SystemExit('No training samples!')
    
    if val_size == 0:
        raise SystemExit('No validation samples!')
    
    # 使用 Subset 創建訓練集和驗證集
    from torch.utils.data import Subset
    train_set = Subset(dataset_full, train_indices)
    
    # 為驗證集建立獨立的資料集(不使用資料增強)
    dataset_val = ImageFolder(root=str(data_dir), transform=transform_val)
    val_set = Subset(dataset_val, val_indices)

    # 創建訓練集的加權採樣器(如果啟用)
    train_sampler = None
    shuffle_train = True
    if args.use_weighted_sampler:
        train_sampler = make_weighted_sampler(dataset_full, train_indices)
        shuffle_train = False  # 使用 sampler 時不能同時 shuffle
        print("\n✓ 使用 WeightedRandomSampler 平衡訓練批次")

    train_loader = DataLoader(train_set, batch_size=args.batch_size, 
                              shuffle=shuffle_train, sampler=train_sampler,
                              num_workers=args.num_workers, pin_memory=True)
    val_loader = DataLoader(val_set, batch_size=args.batch_size, shuffle=False,
                            num_workers=args.num_workers, pin_memory=True)

    device = torch.device(args.device)
    model = SimpleCNN(num_classes=num_classes).to(device)

    # 計算類別權重並創建加權損失函數
    if args.use_weighted_loss:
        class_weights = compute_class_weights(dataset_full, device)
        criterion = nn.CrossEntropyLoss(weight=class_weights)
        print("\n✓ 使用加權損失函數 (Weighted CrossEntropyLoss)")
    else:
        criterion = nn.CrossEntropyLoss()
        print("\n使用標準損失函數 (CrossEntropyLoss)")
    
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    best_val_acc = 0.0

    print(f'Found {num_classes} classes: {dataset_full.classes}')
    print(f'Train size: {train_size}, Val size: {val_size}')
    print(f'Device: {device}, epochs: {args.epochs}, batch_size: {args.batch_size}')
    print(f'資料增強: 訓練集使用平移與翻轉, 驗證集不使用')

    for epoch in range(1, args.epochs + 1):
        t0 = time.time()
        train_loss, train_acc = train_one_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc, class_acc = validate(model, val_loader, criterion, device, dataset_full.classes)
        t1 = time.time()

        print(f'\nEpoch {epoch}/{args.epochs}  time={t1-t0:.1f}s')
        print(f'  train_loss={train_loss:.4f} train_acc={train_acc:.4f}')
        print(f'  val_loss={val_loss:.4f} val_acc={val_acc:.4f}')
        
        # 顯示每個類別的驗證準確率
        if class_acc:
            print(f'  各類別驗證準確率:')
            for cls_name, (acc_val, count) in sorted(class_acc.items()):
                print(f'    {cls_name:20s}: {acc_val:6.2%} ({count:3d} 樣本)')

        # save last
        ckpt = {
            'epoch': epoch,
            'model_state': model.state_dict(),
            'optimizer_state': optimizer.state_dict(),
            'args': vars(args),
            'classes': dataset_full.classes
        }
        save_checkpoint(ckpt, save_dir / f'checkpoint_epoch{epoch}.pth')

        # save best
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            save_checkpoint(ckpt, save_dir / 'best.pth')
            print(f'  ✓ 儲存新的最佳模型 (val_acc={val_acc:.4f})')

    # final metadata
    meta = {
        'classes': dataset_full.classes,
        'num_classes': num_classes,
        'args': vars(args)
    }
    with open(save_dir / 'metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print('Training finished. Checkpoints saved to', save_dir)


if __name__ == '__main__':
    main()
