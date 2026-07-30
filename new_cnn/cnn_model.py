import os
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.transforms as transforms
from torchvision import datasets, models
from typing_extensions import Literal

from utils.torch_runtime import inference_slot
from utils.usage_tracker import trace_classifier, trace_model_load

ClassName_cnn_model = Literal[
    'announcement',
    'family',
    'lock',
    'main',
    'mission',
    'homeplace',
    'other_login',
    'park_manager',
    'reward',
    'reward_get'
    ]

resize_size = (128, 128)  # 原始圖片 540x960，可依需求調整


def img_loader(image):
    # 讀取圖片並進行預處理
    transform = transforms.Compose([
        transforms.Resize(resize_size),
        transforms.ToTensor(),
    ])
    image = transform(image)
    # 將圖片轉換為Tensor格式
    image = image.unsqueeze(0)  # 增加一個維度，batch size為1
    return image

@trace_classifier("legacy_stage_cnn")
def predict_image(model, image) -> ClassName_cnn_model:
    # 將圖片轉換為Tensor格式
    class_names = [
    'announcement',
    'family',
    'lock',
    'main',
    'mission',
    'homeplace',
    'other_login',
    'park_manager',
    'reward',
    'reward_get'
        ]

    image = img_loader(image)

    # 將輸入張量移到模型所在裝置（與 miner/models/classifier.py 同一寫法）。
    dev = next(model.parameters()).device
    image = image.to(dev)

    # inference_slot() 序列化共用模型的 forward，讓多裝置同時推論時排隊而非
    # 一起擠爆 GPU（分流）；inference_mode 省去 autograd graph 開銷。
    with inference_slot(), torch.inference_mode():
        result = model(image)
    # 使用softmax獲取預測結果
    _, predicted = torch.max(result, 1)

    return class_names[predicted.item()]
class SimpleCNN(nn.Module):
    def __init__(self, num_classes):
        super(SimpleCNN, self).__init__()
        self.conv1 = nn.Conv2d(3, 16, 3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(16, 32, 3, padding=1)
        self.fc1 = nn.Linear(32 * (128//4) * (128//4), 128)
        self.fc2 = nn.Linear(128, num_classes)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))  # -> 16x64x64
        x = self.pool(F.relu(self.conv2(x)))  # -> 32x32x32
        x = x.view(x.size(0), -1)             # 展平成向量
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return x
@trace_model_load("legacy_stage_cnn")
def load_cnn_model(model_path, num_classes=10):
    # 載入模型
    # 預設 CPU：此分支目標是降低 GPU 使用量，故不預設 cuda。
    device = "cpu"
    model = SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path, map_location=device))
    model.to(device)
    model.eval()  # 設定為評估模式
    return model
