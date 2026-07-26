import os
import sys

sys.path.append(os.path.join(os.getcwd(), "src"))
from services import style_grouping as sg

tags = ["building", "architecture", "city", "sky", "landscape"]
print("primary_mapped:", sg._get_broad_genre(tags[0]))
top_mapped_arch = {sg._get_broad_genre(t) for t in tags[:5]}
print("top_mapped_arch:", top_mapped_arch)
if "scene_landscape" in top_mapped_arch:
    print("Has landscape in top 5")
else:
    print("NO landscape in top 5")
