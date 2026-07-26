import config
config.DB_PATH = "/Users/anna/Pictures/Lightroom Classic/Rare Light Photography/styleai.db"
from services.style_grouping import _KEYWORD_TO_GENRE, _BROAD_GENRE_MAP, _get_broad_genre

def new_vision_model_tags_logic(content_tags, priors):
    if not content_tags:
        return None
        
    def get_index_of_genre(target_genre, tags):
        for i, t in enumerate(tags):
            t_lower = t.lower()
            if (_get_broad_genre(t) == target_genre or 
                _get_broad_genre(t_lower) == target_genre or 
                _BROAD_GENRE_MAP.get(t_lower) == target_genre or 
                _BROAD_GENRE_MAP.get(t) == target_genre):
                return i
            mapped = _KEYWORD_TO_GENRE.get(t_lower)
            if mapped and _get_broad_genre(mapped) == target_genre:
                return i
        return -1
        
    def has_pet(tags):
        pet_keywords = {"dog", "cat", "pet", "domestic", "puppy", "kitten", "feline", "canine"}
        return any(t.lower() in pet_keywords for t in tags)

    def has_macro(tags):
        macro_keywords = {"macro", "close up", "insect", "bug", "spider", "fly", "butterfly", "flower"}
        return any(t.lower() in macro_keywords for t in tags)

    # 1. ARTWORK FILTER
    artwork_keywords = {"cartoon", "illustration", "anime", "drawing", "painting", "artwork", "sketch", "comic", "art"}
    top_5_tags = {t.lower() for t in content_tags[:5]}
    if artwork_keywords.intersection(top_5_tags):
        return "scene_unknown"

    primary_mapped = _get_broad_genre(content_tags[0])
    
    # Pre-calculate indices
    idx_macro = get_index_of_genre("scene_macro", content_tags[:5])
    has_macro_kws = has_macro(content_tags[:5])
    idx_portrait = get_index_of_genre("scene_portrait", content_tags[:5])
    has_pet_top10 = has_pet(content_tags[:10])
    idx_event = get_index_of_genre("scene_event", content_tags[:8])
    idx_action = get_index_of_genre("scene_action", content_tags[:8])
    idx_street = get_index_of_genre("scene_street", content_tags[:8])
    idx_arch = get_index_of_genre("scene_architecture", content_tags[:4])
    idx_wildlife = get_index_of_genre("scene_wildlife", content_tags[:6])
    idx_landscape = get_index_of_genre("scene_landscape", content_tags[:5])

    # 2. OVERRIDING DOMINANT SUBJECTS
    
    if has_pet_top10:
        return "scene_portrait"

    if idx_macro >= 0 or (has_macro_kws and primary_mapped in ("scene_nature", "scene_wildlife", "scene_unknown")):
        return "scene_macro"

    # Action / Event / Street OVERRIDE Portrait if they are present!
    if idx_action >= 0:
        return "scene_action"
    if idx_event >= 0:
        return "scene_event"
    if idx_street >= 0:
        return "scene_street"

    # Portrait
    if primary_mapped == "scene_portrait" or idx_portrait >= 0:
        # Check if the "portrait" tag was just "people" or "crowd" without a clear portrait focus
        if idx_portrait == -1 or idx_portrait > 3:
            if "event" in content_tags[:8] or "crowd" in content_tags[:8]:
                return "scene_event"
        return "scene_portrait"

    # Wildlife
    if idx_wildlife >= 0:
        return "scene_wildlife"

    # Architecture
    if idx_arch >= 0:
        return "scene_architecture"
        
    # Landscape
    if idx_landscape >= 0:
        return "scene_landscape"

    # Nature fallback
    if primary_mapped == "scene_nature":
        if priors and priors.get("scene_macro", 0.0) > 0:
            return "scene_macro"
        return "scene_nature"

    # Night fallback
    if primary_mapped == "scene_night":
        return "scene_night"

    # Default to primary mapped if it's canonical
    canonical_regimes = {
        "scene_portrait", "scene_landscape", "scene_architecture", "scene_studio",
        "scene_night", "scene_astrophotography", "scene_wildlife", "scene_action",
        "scene_event", "scene_street", "scene_macro", "scene_nature", "scene_food",
        "scene_exterior", "scene_interior"
    }
    if primary_mapped in canonical_regimes:
        return primary_mapped

    for t in content_tags[:6]:
        if t in canonical_regimes:
            return t
        mapped_t = _get_broad_genre(t)
        if mapped_t in canonical_regimes:
            return mapped_t

    return "scene_unknown"

def run():
    print("Tags 1 (distant bird):", new_vision_model_tags_logic(["nature", "outdoors", "grass", "tree", "plant", "landscape", "sky", "cloud", "animal", "bird", "wildlife", "fly"], None))
    print("Tags 2 (event w/ people first):", new_vision_model_tags_logic(["people", "crowd", "man", "woman", "event"], None))
    print("Tags 3 (landscape w/ architecture):", new_vision_model_tags_logic(["landscape", "mountain", "sky", "cloud", "building", "house", "architecture"], None))
    print("Tags 4 (pet portrait):", new_vision_model_tags_logic(["animal", "dog", "pet", "indoor", "mammal"], None))
    print("Tags 5 (cartoon dog):", new_vision_model_tags_logic(["illustration", "drawing", "cartoon", "animal", "dog"], None))
    print("Tags 6 (macro insect):", new_vision_model_tags_logic(["animal", "insect", "wildlife", "macro", "bug", "fly"], None))
    print("Tags 7 (portrait):", new_vision_model_tags_logic(["person", "woman", "face", "portrait", "indoor", "studio"], None))
    print("Tags 8 (street):", new_vision_model_tags_logic(["street", "city", "outdoor", "urban", "people", "walking"], None))

if __name__ == "__main__":
    run()
