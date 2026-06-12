filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/providers/base.py"
with open(filepath, "r") as f:
    content = f.read()

new_content = """        if summary:
            lines.append(f"      Summary: {summary}")
        
        dom_colors = example.get("dominant_colors", "[]")
        if isinstance(dom_colors, str) and dom_colors != "[]":
            import json
            try:
                dc = json.loads(dom_colors)
                if dc:
                    lines.append(f"      Dominant Colors: {', '.join(dc)}")
            except:
                pass
        elif isinstance(dom_colors, list) and dom_colors:
            lines.append(f"      Dominant Colors: {', '.join(dom_colors)}")
            
        zones = []
        if "zone_deep_shadows" in example:
            zds = float(example.get("zone_deep_shadows", 0)) * 100
            zs = float(example.get("zone_shadows", 0)) * 100
            zm = float(example.get("zone_midtones", 0)) * 100
            zh = float(example.get("zone_highlights", 0)) * 100
            zbh = float(example.get("zone_bright_highlights", 0)) * 100
            if any([zds, zs, zm, zh, zbh]):
                lines.append(f"      Luminance Zones: DeepShadows={zds:.0f}%, Shadows={zs:.0f}%, Midtones={zm:.0f}%, Highlights={zh:.0f}%, BrightHighlights={zbh:.0f}%")
        
        if compact:"""

content = content.replace(
    """        if summary:
            lines.append(f"      Summary: {summary}")
        if compact:""",
    new_content,
)

with open(filepath, "w") as f:
    f.write(content)
print("Updated base.py")
