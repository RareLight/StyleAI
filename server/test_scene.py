import sys
sys.path.append('src')
import server_lifecycle
from services.training import compute_scene_tags, _SCENE_PROBES
import open_clip
from PIL import Image
import torch
import torch.nn.functional as F
from config import TORCH_DEVICE

model = server_lifecycle.get_model()
processor = server_lifecycle.get_processor()
tokenizer = server_lifecycle.get_tokenizer()

img = Image.new('RGB', (384, 384), color = (5, 5, 20))
img_input = processor(img).unsqueeze(0).to(TORCH_DEVICE)

with torch.no_grad():
    img_features = model.encode_image(img_input)
    img_features = F.normalize(img_features, p=2, dim=1)

print("Testing tags against a dark blue image")
with torch.no_grad():
    scale = model.logit_scale.exp()
    bias = model.logit_bias
    for tag_name, probe_text in {"dark_blue": "a dark blue image", "sky": "the night sky", "cat": "a cat"}.items():
        tokens = tokenizer([probe_text]).to(TORCH_DEVICE)
        text_features = model.encode_text(tokens)
        text_vec = F.normalize(text_features, p=2, dim=1)
        sim = float((img_features * text_vec).sum().cpu())
        logit = sim * scale.item() + bias.item()
        prob = torch.sigmoid(torch.tensor(logit)).item()
        print(f"{tag_name}: sim={sim:.4f} logit={logit:.4f} prob={prob:.4f}")
