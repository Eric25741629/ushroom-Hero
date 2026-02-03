"""
流式資料集處理模組
實現記憶體友好的資料集載入方式，避免一次性載入所有檔案
"""
import os
import gc
import psutil
from pathlib import Path
from typing import List, Tuple, Dict, Iterator, Optional, Union
from PIL import Image
import torch
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
import numpy as np
from tqdm import tqdm


class MemoryMonitor:
    """記憶體使用監控器"""
    
    def __init__(self):
        self.process = psutil.Process()
        self.peak_memory = 0
        
    def get_memory_usage(self) -> Tuple[float, float]:
        """獲取當前記憶體使用量 (MB)
        
        Returns:
            (使用的記憶體MB, 系統總記憶體MB)
        """
        memory_info = self.process.memory_info()
        used_mb = memory_info.rss / 1024 / 1024
        
        # 更新峰值記憶體
        self.peak_memory = max(self.peak_memory, used_mb)
        
        # 系統總記憶體
        system_memory = psutil.virtual_memory()
        total_mb = system_memory.total / 1024 / 1024
        
        return used_mb, total_mb
    
    def get_memory_percent(self) -> float:
        """獲取記憶體使用百分比"""
        used, total = self.get_memory_usage()
        return (used / total) * 100
    
    def format_memory(self, mb: float) -> str:
        """格式化記憶體大小顯示"""
        if mb < 1024:
            return f"{mb:.1f} MB"
        else:
            return f"{mb/1024:.2f} GB"
    
    def print_status(self, prefix: str = ""):
        """印出當前記憶體狀態"""
        used, total = self.get_memory_usage()
        percent = (used / total) * 100
        print(f"{prefix}記憶體使用: {self.format_memory(used)} / {self.format_memory(total)} ({percent:.1f}%)")
        print(f"{prefix}峰值記憶體: {self.format_memory(self.peak_memory)}")
    
    def cleanup(self):
        """清理記憶體"""
        gc.collect()
        torch.cuda.empty_cache() if torch.cuda.is_available() else None


class StreamingImageDataset(Dataset):
    """流式圖片資料集
    
    不會一次性載入所有圖片到記憶體，而是在需要時才載入
    """
    
    def __init__(
        self, 
        image_paths: List[str], 
        labels: List[int],
        transform: Optional[transforms.Compose] = None,
        class_names: Optional[List[str]] = None
    ):
        """
        Args:
            image_paths: 圖片檔案路徑列表
            labels: 對應的標籤列表
            transform: 圖片轉換處理
            class_names: 類別名稱列表
        """
        assert len(image_paths) == len(labels), "圖片數量與標籤數量不符"
        
        self.image_paths = image_paths
        self.labels = labels
        self.transform = transform
        self.class_names = class_names or [str(i) for i in range(max(labels) + 1)]
        
    def __len__(self) -> int:
        return len(self.image_paths)
    
    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, int, str]:
        """
        Returns:
            (image_tensor, label, image_path)
        """
        img_path = self.image_paths[idx]
        label = self.labels[idx]
        
        try:
            # 即時載入圖片
            image = Image.open(img_path).convert('RGB')
            
            if self.transform:
                image = self.transform(image)
            else:
                # 預設轉換
                image = transforms.ToTensor()(image)
            
            return image, label, img_path
            
        except Exception as e:
            print(f"載入圖片失敗: {img_path}, 錯誤: {e}")
            # 回傳空白圖片避免程式崩潰
            if self.transform:
                dummy_image = Image.new('RGB', (64, 64), (128, 128, 128))
                image = self.transform(dummy_image)
            else:
                image = torch.zeros(3, 64, 64)
            return image, label, img_path


class StreamingDatasetBuilder:
    """流式資料集建構器"""
    
    @staticmethod
    def from_folder_structure(
        root_dir: Union[str, Path],
        transform: Optional[transforms.Compose] = None,
        image_extensions: Tuple[str, ...] = ('.png', '.jpg', '.jpeg', '.bmp', '.gif')
    ) -> StreamingImageDataset:
        """從資料夾結構建立流式資料集
        
        支援兩種結構:
        1. 多類別: root_dir/class1/, root_dir/class2/, ...
        2. 單一類別: root_dir/ (直接包含圖片)
        
        Args:
            root_dir: 根目錄路徑
            transform: 圖片轉換處理
            image_extensions: 支援的圖片副檔名
            
        Returns:
            StreamingImageDataset
        """
        root_path = Path(root_dir)
        
        # 使用生成器檢查是否為單一類別資料夾（只檢查前10個項目）
        def check_has_images_generator():
            try:
                for i, f in enumerate(root_path.iterdir()):
                    if i >= 10:  # 只檢查前10個項目
                        break
                    try:
                        if f.is_file() and f.suffix.lower() in image_extensions:
                            return True
                    except (OSError, PermissionError):
                        continue
            except (OSError, PermissionError):
                pass
            return False
        
        has_images = check_has_images_generator()
        
        image_paths = []
        labels = []
        class_names = []
        
        if has_images:
            # 單一類別模式 - 使用生成器逐個處理
            class_names = [root_path.name]
            print(f"掃描單一類別資料夾: {root_path.name}")
            try:
                for img_file in root_path.iterdir():
                    try:
                        if img_file.is_file() and img_file.suffix.lower() in image_extensions:
                            image_paths.append(str(img_file))
                            labels.append(0)
                    except (OSError, PermissionError):
                        continue
            except (OSError, PermissionError) as e:
                print(f"警告：讀取資料夾時發生錯誤: {e}")
        else:
            # 多類別模式 - 使用生成器逐個處理類別資料夾
            print(f"掃描多類別資料夾結構...")
            try:
                class_dirs = []
                for d in root_path.iterdir():
                    try:
                        if d.is_dir():
                            class_dirs.append(d)
                    except (OSError, PermissionError):
                        continue
                
                class_dirs.sort()  # 確保類別順序一致
                class_names = [d.name for d in class_dirs]
                
                for class_idx, class_dir in enumerate(class_dirs):
                    print(f"  掃描類別 [{class_idx+1}/{len(class_dirs)}]: {class_dir.name}")
                    try:
                        for img_file in class_dir.iterdir():
                            try:
                                if img_file.is_file() and img_file.suffix.lower() in image_extensions:
                                    image_paths.append(str(img_file))
                                    labels.append(class_idx)
                            except (OSError, PermissionError):
                                continue
                    except (OSError, PermissionError) as e:
                        print(f"    警告：無法讀取類別資料夾 {class_dir.name}: {e}")
                        continue
            except (OSError, PermissionError) as e:
                print(f"錯誤：無法讀取根目錄 {root_path}: {e}")
        
        print(f"建立流式資料集: {len(image_paths)} 個圖片, {len(class_names)} 個類別")
        print(f"類別: {class_names}")
        
        return StreamingImageDataset(
            image_paths=image_paths,
            labels=labels,
            transform=transform,
            class_names=class_names
        )
    
    @staticmethod
    def from_image_list(
        image_paths: List[str],
        class_name: str,
        transform: Optional[transforms.Compose] = None
    ) -> StreamingImageDataset:
        """從圖片路徑列表建立單一類別的流式資料集"""
        labels = [0] * len(image_paths)
        class_names = [class_name]
        
        return StreamingImageDataset(
            image_paths=image_paths,
            labels=labels,
            transform=transform,
            class_names=class_names
        )


class BatchProcessor:
    """批次處理器，支援動態調整批次大小以控制記憶體使用"""
    
    def __init__(
        self, 
        initial_batch_size: int = 32,
        max_memory_percent: float = 80.0,
        min_batch_size: int = 1
    ):
        """
        Args:
            initial_batch_size: 初始批次大小
            max_memory_percent: 記憶體使用上限百分比
            min_batch_size: 最小批次大小
        """
        self.current_batch_size = initial_batch_size
        self.max_memory_percent = max_memory_percent
        self.min_batch_size = min_batch_size
        self.memory_monitor = MemoryMonitor()
        
    def adjust_batch_size(self) -> bool:
        """根據記憶體使用情況調整批次大小
        
        Returns:
            True if batch size was adjusted
        """
        memory_percent = self.memory_monitor.get_memory_percent()
        
        if memory_percent > self.max_memory_percent:
            # 記憶體使用過高，減少批次大小
            new_size = max(self.min_batch_size, self.current_batch_size // 2)
            if new_size != self.current_batch_size:
                print(f"記憶體使用過高 ({memory_percent:.1f}%)，減少批次大小: {self.current_batch_size} → {new_size}")
                self.current_batch_size = new_size
                self.memory_monitor.cleanup()
                return True
        elif memory_percent < self.max_memory_percent * 0.5:
            # 記憶體使用較低，可以增加批次大小
            new_size = min(64, self.current_batch_size * 2)
            if new_size != self.current_batch_size:
                print(f"記憶體使用較低 ({memory_percent:.1f}%)，增加批次大小: {self.current_batch_size} → {new_size}")
                self.current_batch_size = new_size
                return True
        
        return False
    
    def get_dataloader(
        self, 
        dataset: StreamingImageDataset,
        shuffle: bool = False,
        num_workers: int = 2
    ) -> DataLoader:
        """創建 DataLoader"""
        return DataLoader(
            dataset,
            batch_size=self.current_batch_size,
            shuffle=shuffle,
            num_workers=num_workers,
            pin_memory=torch.cuda.is_available()
        )
    
    def process_in_batches(
        self,
        dataset: StreamingImageDataset,
        process_fn,
        desc: str = "處理資料",
        **kwargs
    ) -> Iterator:
        """以批次方式處理資料集
        
        Args:
            dataset: 要處理的資料集
            process_fn: 處理函數 process_fn(batch_images, batch_labels, batch_paths)
            desc: 進度條描述
            **kwargs: 傳給 process_fn 的額外參數
        """
        processed_count = 0
        total_samples = len(dataset)
        
        with tqdm(total=total_samples, desc=desc) as pbar:
            while processed_count < total_samples:
                # 計算剩餘樣本數量
                remaining = total_samples - processed_count
                actual_batch_size = min(self.current_batch_size, remaining)
                
                # 創建當前批次的子資料集
                start_idx = processed_count
                end_idx = min(start_idx + actual_batch_size, total_samples)
                
                batch_paths = dataset.image_paths[start_idx:end_idx]
                batch_labels = dataset.labels[start_idx:end_idx]
                
                current_batch_dataset = StreamingImageDataset(
                    batch_paths,
                    batch_labels, 
                    dataset.transform,
                    dataset.class_names
                )
                
                # 創建 DataLoader (批次大小為1，因為我們已經手動分批)
                dataloader = DataLoader(
                    current_batch_dataset,
                    batch_size=actual_batch_size,
                    shuffle=False,
                    num_workers=0,  # 減少workers避免記憶體問題
                    pin_memory=False
                )
                
                try:
                    # 處理當前批次
                    for images, labels, paths in dataloader:
                        # 處理批次
                        yield from process_fn(images, labels, paths, **kwargs)
                        
                        processed_count += len(images)
                        pbar.update(len(images))
                        
                        # 檢查記憶體使用並調整批次大小
                        if processed_count % (self.current_batch_size * 5) == 0:
                            old_batch_size = self.current_batch_size
                            if self.adjust_batch_size():
                                print(f"\n批次 {processed_count//old_batch_size}: 調整批次大小 {old_batch_size} → {self.current_batch_size}")
                        
                        # 定期清理記憶體
                        if processed_count % (self.current_batch_size * 10) == 0:
                            self.memory_monitor.cleanup()
                            
                except Exception as e:
                    print(f"\n處理批次時發生錯誤: {e}")
                    # 遇到錯誤時減少批次大小繼續處理
                    if self.current_batch_size > self.min_batch_size:
                        self.current_batch_size = max(self.min_batch_size, self.current_batch_size // 2)
                        print(f"減少批次大小至 {self.current_batch_size} 並繼續處理")
                    else:
                        # 無法繼續處理，跳過當前樣本
                        processed_count += 1
                        pbar.update(1)
                        continue