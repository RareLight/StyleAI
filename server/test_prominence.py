import os
import sys

sys.path.append(os.path.join(os.getcwd(), "server", "src"))
from services import style_grouping as sg


def test_issues():
    # 1. Architecture showing landscape elements
    # Even if building is first, if landscape is in top 5, it should be landscape.
    tags1 = ["building", "architecture", "city", "sky", "landscape"]
    res1 = sg._primary_genre_with_keywords(tags1, [])
    print("Arch test:", res1)

    # 2. Pet portraits as wildlife
    # If primary is nature and both wildlife and dog are present, dog (portrait) should win.
    tags2 = ["nature", "wildlife", "dog", "animal"]
    res2 = sg._primary_genre_with_keywords(tags2, [])
    print("Pet nature test:", res2)

    # 3. Medium focal length shots of flowers/nature as landscape
    # If primary is nature and landscape is in top 12, it should NOT override nature anymore.
    tags3 = [
        "nature",
        "tree",
        "forest",
        "outdoors",
        "grass",
        "sun",
        "leaf",
        "landscape",
    ]
    res3 = sg._primary_genre_with_keywords(tags3, [])
    print("Nature landscape test:", res3)

    # 4. Event/sport as portrait
    # If person is first (primary portrait) but sports/action is in top 12 (e.g. tag 7), it should override portrait to action.
    tags4 = ["person", "people", "man", "woman", "crowd", "stadium", "sports", "action"]
    res4 = sg._primary_genre_with_keywords(tags4, [])
    print("Sports portrait test:", res4)


if __name__ == "__main__":
    test_issues()
