import os
import sys

sys.path.append(os.path.join(os.getcwd(), "src"))
from services import style_grouping as sg

print("Arch 1:", sg._primary_genre(["scene_architecture", "scene_exterior"]))
print("Arch 2:", sg._primary_genre(["scene_architecture", "scene_street"]))
print("Meadow:", sg._primary_genre_with_keywords(["nature", "meadow"], []))
print(
    "Mountain:",
    sg._primary_genre_with_keywords(["scene_nature", "mountain", "valley"], []),
)
print("Mountain vista:", sg._primary_genre(["scene_nature", "mountain vista"]))
