
filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/providers/base.py"
with open(filepath, "r") as f:
    content = f.read()

content = content.replace(
    "    histogram_signature: dict[str, Any] | None = None\n",
    "    histogram_signature: dict[str, Any] | None = None\n    dominant_colors: list[str] | None = None\n    luminance_zones: dict[str, float] | None = None\n",
)

content = content.replace(
    '        if getattr(request, "histogram_signature", None):',
    """        if getattr(request, "dominant_colors", None):
            context_additions.append(f"Image dominant colors (HEX): {', '.join(request.dominant_colors)}")
        if getattr(request, "luminance_zones", None):
            lz = request.luminance_zones
            context_additions.append(f"Image luminance zones: DeepShadows={lz.get('zone_deep_shadows',0)*100:.0f}%, Shadows={lz.get('zone_shadows',0)*100:.0f}%, Midtones={lz.get('zone_midtones',0)*100:.0f}%, Highlights={lz.get('zone_highlights',0)*100:.0f}%, BrightHighlights={lz.get('zone_bright_highlights',0)*100:.0f}%")
        if getattr(request, "histogram_signature", None):""",
)

with open(filepath, "w") as f:
    f.write(content)
print("Updated base.py request and prompt")
