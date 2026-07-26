import os
import sys
sys.path.append(os.path.join(os.getcwd(), 'src'))
from services import style_grouping as sg

tags = ["building", "architecture", "city", "sky", "landscape"]
primary_mapped = sg._get_broad_genre(tags[0])
print("primary_mapped:", primary_mapped)
top_mapped_arch = {sg._get_broad_genre(t) for t in tags[:5]}
if "scene_landscape" in top_mapped_arch or "scene_nature" in top_mapped_arch:
    tier_order_subjects = ["scene_landscape", "scene_nature"]
else:
    tier_order_subjects = []

top_vision_tags = tags[:6]
found = None
for target_genre in tier_order_subjects:
    print(f"Checking target_genre: {target_genre}")
    for t in top_vision_tags:
        t_lower = t.lower()
        if (
            sg._get_broad_genre(t) == target_genre
            or sg._get_broad_genre(t_lower) == target_genre
            or sg._BROAD_GENRE_MAP.get(t_lower) == target_genre
            or sg._BROAD_GENRE_MAP.get(t) == target_genre
        ):
            print(f"  Match found for tag '{t}'!")
            found = target_genre
            break
        mapped = sg._KEYWORD_TO_GENRE.get(t_lower)
        if mapped and sg._get_broad_genre(mapped) == target_genre:
            print(f"  Mapped match found for tag '{t}'!")
            found = target_genre
            break
    if found:
        break
print("Loop finished, found:", found)
