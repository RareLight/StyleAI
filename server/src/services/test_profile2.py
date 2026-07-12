import re


def _profile_name(camera_profile: str | None) -> str:
    if not camera_profile:
        return "Default"

    profile = camera_profile.strip()
    is_hdr = bool(re.search(r"(?i)\s*\+?\s*HDR\b", profile))

    p_clean = re.sub(r"(?i)\s*\+?\s*HDR\b", "", profile).strip()
    p_clean = re.sub(r"(?i)\s*\(v\d+\)", "", p_clean).strip()
    p_clean = re.sub(r"\s+", " ", p_clean)

    if p_clean.islower() or p_clean.isupper():
        p_clean = p_clean.title()

    if is_hdr:
        p_clean += " + HDR"

    return p_clean or "Default"


print(_profile_name("Adobe Standard (v2)"))
print(_profile_name("adobe standard"))
print(_profile_name("ADOBE STANDARD"))
print(_profile_name("VSCO Film"))
print(_profile_name("Adobe Standard + HDR"))
