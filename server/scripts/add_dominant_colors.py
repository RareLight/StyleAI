import re

filepath = "/Users/anna/Documents/Coding/StyleAI/server/src/services/training.py"
with open(filepath, "r") as f:
    content = f.read()

new_func = """
# ---------------------------------------------------------------------------
# Dominant Color Palette Extraction
# ---------------------------------------------------------------------------

def compute_dominant_colors(image_bytes: bytes, n_colors: int = 5) -> list[str]:
    \"\"\"Extract the dominant colors from the image using K-Means clustering.
    Returns a list of HEX color strings.
    \"\"\"
    try:
        from sklearn.cluster import KMeans
        import io
        from PIL import Image
        
        # Load a very small thumbnail for extremely fast clustering
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((100, 100), Image.Resampling.LANCZOS)
        
        # Reshape the image to be a list of pixels
        pixels = np.asarray(image)
        pixels = pixels.reshape(-1, 3)
        
        # Cluster the pixels
        kmeans = KMeans(n_clusters=n_colors, n_init='auto', random_state=42)
        kmeans.fit(pixels)
        
        # Get the colors and convert to hex
        colors = kmeans.cluster_centers_.astype(int)
        
        # Sort by frequency (labels)
        labels = kmeans.labels_
        counts = np.bincount(labels)
        sorted_indices = np.argsort(counts)[::-1]
        
        hex_colors = []
        for idx in sorted_indices:
            r, g, b = colors[idx]
            hex_colors.append(f"#{r:02x}{g:02x}{b:02x}")
            
        return hex_colors
    except Exception as exc:
        logger.warning("compute_dominant_colors failed: %s", exc)
        return []

# ---------------------------------------------------------------------------
"""

# Insert before histogram signature
content = content.replace(
    "# ---------------------------------------------------------------------------\n# Perceptual histogram signature for style grouping\n# ---------------------------------------------------------------------------",
    new_func
    + "# Perceptual histogram signature for style grouping\n# ---------------------------------------------------------------------------",
)

with open(filepath, "w") as f:
    f.write(content)
print("Updated training.py with compute_dominant_colors")
