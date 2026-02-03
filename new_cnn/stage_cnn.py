import logging
import torch
import cnn_model

    
def load_cnn_model(model_path, num_classes=10):
    logging.warning("即將廢棄")
    # 載入模型
    model = cnn_model.SimpleCNN(num_classes=num_classes)
    model.load_state_dict(torch.load(model_path))
    model.eval()  # 設定為評估模式
    return model