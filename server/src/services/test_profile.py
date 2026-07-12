import re


def _clean_profile_name(profile: str) -> str:
    if not profile:
        return "Default"

    # Check if it's HDR
    is_hdr = bool(re.search(r"(?i)\s*\+?\s*HDR\b", profile))

    # Strip HDR and version tags for base normalization
    p_clean = re.sub(r"(?i)\s*\+?\s*HDR\b", "", profile).strip()
    p_clean = re.sub(r"\s*\(v\d+\)", "", p_clean).strip()
    p_clean = re.sub(r"\s+", " ", p_clean)

    # Title case it for consistency unless it's an acronym
    # Simple title casing:
    p_clean = p_clean.title()

    # Special cases for common acronyms
    p_clean = p_clean.replace("V2", "v2")  # wait, we stripped v2

    if is_hdr:
        p_clean += " + HDR"

    return p_clean


print(_clean_profile_name("Adobe Standard (v2)"))
print(_clean_profile_name("adobe standard"))
print(_clean_profile_name("Adobe Standard + HDR"))
