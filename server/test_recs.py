import logging
import config

config.DB_PATH = "/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from services import style_catalog
from services import style_upgrades
from services import style_grouping

def run():
    style_catalog._ensure_initialized()
    style_grouping.clear_semantic_genre_cache()
    style_upgrades.invalidate_upgrade_recommendations_cache()
    recs = style_upgrades.get_style_upgrade_recommendations(limit=20)
    
    for s in recs.get("styles", []):
        name = s.get("style_name")
        prof = s.get("camera_profile")
        r = s.get("recommended_photo_ids", [])
        print(f"{name} [{prof}] -> {len(r)} recs")

if __name__ == "__main__":
    run()
