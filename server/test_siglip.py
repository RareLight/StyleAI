import sys
import torch
from PIL import Image
import os
import io

sys.path.append("src")
import sys; sys.path.append("src"); from config import get_torch_device
from server_lifecycle import get_model, get_processor, _load_vision_model

# Load model
_load_vision_model()
model = get_model()
processor = get_processor()

# Create dummy image
img = Image.new('RGB', (512, 512), color = 'red')

tensors = [processor(img)]
chunk = torch.stack(tensors).to(get_torch_device())

print("Running forward pass...")
try:
    image_features = model.encode_image(chunk)
    print("Success! Output shape:", image_features.shape)
except Exception as e:
    print("Failed:", e)
