#!/usr/bin/env python3
"""
測試流式資料集處理功能
比較傳統方式與流式處理的記憶體使用情況
"""
import os
import sys
import time
import argparse
from pathlib import Path

# 添加當前目錄到路徑
sys.path.append(str(Path(__file__).parent))

from streaming_dataset import (
    StreamingImageDataset, StreamingDatasetBuilder, 
    BatchProcessor, MemoryMonitor
)
from simplecnn import SimpleCNN
from check_dataset import make_transforms
from config.paths import DATASET_MINES_DIR_STR


def test_memory_usage():
    """測試記憶體使用情況"""
    
    # 檢查是否有測試資料
    test_data_dir = Path(DATASET_MINES_DIR_STR)
    if not test_data_dir.exists():
        print(f"測試資料目錄不存在: {test_data_dir}")
        print("請確保有資料集可供測試")
        return
    
    print("=" * 60)
    print("流式資料集記憶體使用測試")
    print("=" * 60)
    
    # 初始化記憶體監控
    memory_monitor = MemoryMonitor()
    
    print("\n1. 測試流式資料集建立...")
    memory_monitor.print_status("建立前: ")
    
    # 建立轉換
    transform = make_transforms()
    
    # 建立流式資料集
    streaming_dataset = StreamingDatasetBuilder.from_folder_structure(
        test_data_dir, transform=transform
    )
    
    memory_monitor.print_status("建立後: ")
    
    print(f"\n資料集資訊:")
    print(f"  總圖片數: {len(streaming_dataset)}")
    print(f"  類別數: {len(streaming_dataset.class_names)}")
    print(f"  類別: {streaming_dataset.class_names}")
    
    print("\n2. 測試批次處理器...")
    
    # 建立批次處理器
    batch_processor = BatchProcessor(
        initial_batch_size=16,
        max_memory_percent=70.0,
        min_batch_size=1
    )
    
    # 模擬處理函式
    def mock_process_batch(images, labels, paths, **kwargs):
        """模擬圖片處理 (載入但不做運算)"""
        batch_size = len(images)
        # 模擬一些計算
        time.sleep(0.01 * batch_size)  # 模擬處理時間
        return []
    
    print("開始批次處理測試...")
    memory_monitor.print_status("處理前: ")
    
    start_time = time.time()
    processed_count = 0
    
    try:
        for _ in batch_processor.process_in_batches(
            streaming_dataset,
            mock_process_batch,
            desc="測試流式處理"
        ):
            processed_count += 1
            
            # 每處理100個樣本報告一次記憶體
            if processed_count % 100 == 0:
                current_memory, _ = memory_monitor.get_memory_usage()
                print(f"\n處理 {processed_count} 樣本，目前記憶體: {memory_monitor.format_memory(current_memory)}")
                
    except KeyboardInterrupt:
        print("\n測試被使用者中斷")
    except Exception as e:
        print(f"\n測試過程中發生錯誤: {e}")
    
    end_time = time.time()
    
    print(f"\n3. 測試結果:")
    print(f"  處理時間: {end_time - start_time:.2f} 秒")
    print(f"  處理樣本數: {processed_count}")
    memory_monitor.print_status("處理後: ")
    
    if processed_count > 0:
        print(f"  平均處理速度: {processed_count / (end_time - start_time):.2f} 樣本/秒")
    
    print("\n測試完成!")


def compare_methods(dataset_path: str):
    """比較傳統方法與流式方法的記憶體使用"""
    from torchvision.datasets import ImageFolder
    
    dataset_dir = Path(dataset_path)
    if not dataset_dir.exists():
        print(f"資料集目錄不存在: {dataset_dir}")
        return
    
    print("=" * 60)
    print("傳統載入 vs 流式處理 記憶體比較")
    print("=" * 60)
    
    transform = make_transforms()
    memory_monitor = MemoryMonitor()
    
    # 測試傳統方法
    print("\n1. 測試傳統 ImageFolder 載入...")
    memory_monitor.print_status("載入前: ")
    
    try:
        traditional_dataset = ImageFolder(root=str(dataset_dir), transform=transform)
        memory_monitor.print_status("載入後: ")
        print(f"  傳統資料集大小: {len(traditional_dataset)} 樣本")
        
        # 清理記憶體
        del traditional_dataset
        memory_monitor.cleanup()
        
    except Exception as e:
        print(f"傳統方法載入失敗: {e}")
    
    # 測試流式方法
    print("\n2. 測試流式資料集...")
    memory_monitor.print_status("建立前: ")
    
    streaming_dataset = StreamingDatasetBuilder.from_folder_structure(
        dataset_dir, transform=transform
    )
    memory_monitor.print_status("建立後: ")
    print(f"  流式資料集大小: {len(streaming_dataset)} 樣本")
    
    print(f"\n記憶體使用比較:")
    memory_monitor.print_status("最終: ")


def main():
    parser = argparse.ArgumentParser(description='測試流式資料集功能')
    parser.add_argument('--test-type', choices=['memory', 'compare'], default='memory',
                       help='測試類型: memory=記憶體測試, compare=方法比較')
    parser.add_argument('--dataset-path', type=str, 
                       default=DATASET_MINES_DIR_STR,
                       help='測試用資料集路徑')
    
    args = parser.parse_args()
    
    if args.test_type == 'memory':
        test_memory_usage()
    elif args.test_type == 'compare':
        compare_methods(args.dataset_path)


if __name__ == '__main__':
    main()