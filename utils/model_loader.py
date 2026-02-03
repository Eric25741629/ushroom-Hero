import torch
import miner.models.simplecnn as simplecnn

def load_oracle_cnn_model(model_path, num_classes=10):
    # 載入模型
    model = simplecnn.SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path))
    model.eval()  # 設定為評估模式
    return model
