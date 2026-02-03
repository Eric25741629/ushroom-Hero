import torch
import torch.nn as nn
import torch.nn.functional as F

resize_size = (64, 64) 

class SimpleCNN(nn.Module):
    def __init__(self, num_classes):
        super(SimpleCNN, self).__init__()
        self.conv1 = nn.Conv2d(3, 16, 3, padding=1)
        self.pool  = nn.MaxPool2d(2, 2)
        self.conv2 = nn.Conv2d(16, 32, 3, padding=1)
        self.conv3 = nn.Conv2d(32, 64, 3, padding=1)

        # 64x64 -> pool -> 32x32 -> pool -> 16x16 -> pool -> 8x8
        h = resize_size[0] // (2**3)  # = 8
        w = resize_size[1] // (2**3)  # = 8
        self.fc1 = nn.Linear(64 * h * w, 128)  # 64*8*8=4096
        self.fc2 = nn.Linear(128, 128)
        self.fc3 = nn.Linear(128, num_classes)

    def forward(self, x):
        x = self.pool(F.relu(self.conv1(x)))  # -> 16x32x32
        x = self.pool(F.relu(self.conv2(x)))  # -> 32x16x16
        x = self.pool(F.relu(self.conv3(x)))  # -> 64x8x8
        x = x.view(x.size(0), -1)
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x
