import torch
import open_clip
from PIL import Image

model, _, preprocess = open_clip.create_model_and_transforms('ViT-B-32', pretrained='openai')
img = Image.new('RGB', (100, 100))
t1 = preprocess(img)
t2 = preprocess(img)

print("T1 shape:", t1.shape)
stack = torch.stack([t1, t2])
print("Stack shape:", stack.shape)

with torch.no_grad():
    features = model.encode_image(stack)
    print("Features shape:", features.shape)
