import sys, os
sys.path.append(os.path.join(os.getcwd(), 'server', 'src'))
os.environ["DB_PATH"] = "/Users/anna/Pictures/Lightroom/styleai.db"
import config
config.DB_PATH = "/Users/anna/Pictures/Lightroom/styleai.db"

from services.style_upgrades import get_style_upgrade_recommendations

recs = get_style_upgrade_recommendations()
print('Results length:', len(recs['styles']))
for s in recs['styles']:
    print(s['style_name'], 'recs count:', len(s['recommended_photo_ids']))
