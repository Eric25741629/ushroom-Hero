# SimpleCNN training

訓練腳本 train.py，會使用 `simplecnn.SimpleCNN` 讀取資料夾 `A:\\菇勇者全自動掛機\\dataset\\mines` 下所有子資料夾作為類別 (ImageFolder 格式)，並進行訓練。

快速開始:

1. 安裝需求 (在適當的 Python 環境中)：

   pip install -r requirements.txt

2. 執行訓練 (範例):

   python train.py --epochs 5 --batch-size 32

常見參數:

- --data-dir: 資料根目錄（預設為 A:\\菇勇者全自動掛機\\dataset\\mines）
- --epochs: 訓練 epochs
- --batch-size: 批次大小
- --lr: 學習率

輸出:

- checkpoints/*.pth
- checkpoints/metadata.json
