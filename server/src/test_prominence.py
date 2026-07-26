import config
config.DB_PATH = "/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"
from services.style_grouping import _primary_genre_with_keywords_impl  # noqa: E402

def run():
    print("Tags 4d (animal first, dog 10th):", _primary_genre_with_keywords_impl(["animal", "outdoor", "grass", "tree", "nature", "mammal", "brown", "fur", "snout", "dog", "pet"], []))

if __name__ == "__main__":
    run()
