#!/usr/bin/env python3
import os
import sys
import json
import sqlite3
import random
import re
import io
import base64
import argparse
from datetime import datetime
from PIL import Image

# Insert server/src to sys.path so we can import services.training
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server", "src"))

def parse_args():
    parser = argparse.ArgumentParser(description="Generate AI Training Examples HTML Report")
    parser.add_argument(
        "--catalog-dir",
        type=str,
        default="/Users/anna/Pictures/Lightroom Classic/Rare Light Photography",
        help="Directory of the Lightroom Classic catalog",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "training_report.html"),
        help="Path to save the generated HTML report",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Number of random training examples to sample",
    )
    return parser.parse_args()

def get_db_connections(catalog_dir):
    db_path = os.path.join(catalog_dir, "styleai.db")
    lrcat_path = os.path.join(catalog_dir, "Rare Light Photography.lrcat")
    
    if not os.path.isdir(db_path):
        print(f"Error: Chroma DB directory not found at {db_path}", file=sys.stderr)
        sys.exit(1)
        
    if not os.path.isfile(lrcat_path):
        # Look for any .lrcat file in catalog_dir
        lrcats = [f for f in os.listdir(catalog_dir) if f.endswith(".lrcat") and not f.endswith("-wal") and not f.endswith("-shm")]
        if lrcats:
            lrcat_path = os.path.join(catalog_dir, lrcats[0])
        else:
            print(f"Error: Lightroom Catalog file not found in {catalog_dir}", file=sys.stderr)
            sys.exit(1)
            
    print(f"Found Chroma DB at: {db_path}")
    print(f"Found Lightroom Catalog at: {lrcat_path}")
    return db_path, lrcat_path

def query_lightroom_file_paths(lrcat_path):
    print("Querying file paths from Lightroom Classic catalog...")
    conn = sqlite3.connect(lrcat_path)
    cursor = conn.cursor()
    
    query = """
        SELECT 
            prop.internalValue as global_id,
            rf.absolutePath,
            f.pathFromRoot,
            file.idx_filename
        FROM AgSearchablePhotoProperty prop
        JOIN AgPhotoPropertySpec spec ON prop.propertySpec = spec.id_local
        JOIN Adobe_images i ON prop.photo = i.id_local
        JOIN AgLibraryFile file ON i.rootFile = file.id_local
        JOIN AgLibraryFolder f ON file.folder = f.id_local
        JOIN AgLibraryRootFolder rf ON f.rootFolder = rf.id_local
        WHERE spec.key = 'globalPhotoId'
    """
    
    mapping = {}
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        for global_id, root_path, rel_path, filename in rows:
            if global_id:
                full_path = os.path.join(root_path or "", rel_path or "", filename or "")
                mapping[global_id] = full_path
        print(f"Mapped {len(mapping)} global IDs to local file paths.")
    except Exception as e:
        print(f"Warning: Failed to query Lightroom paths: {e}", file=sys.stderr)
        
    conn.close()
    return mapping

def extract_thumbnail_and_compute_metrics(file_path, max_size=(350, 350)):
    """Extract embedded JPEG and calculate AI metrics dynamically."""
    if not file_path or not os.path.isfile(file_path):
        return None, {}
        
    ext = os.path.splitext(file_path)[1].lower()
    jpeg_bytes = None
    
    # 1. Get raw JPEG bytes
    if ext in [".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"]:
        try:
            with Image.open(file_path) as img:
                img.thumbnail(max_size)
                if img.mode in ["RGBA", "P"]:
                    img = img.convert("RGB")
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=80)
                jpeg_bytes = buffered.getvalue()
        except Exception as e:
            print(f"Direct PIL read failed for {file_path}: {e}", file=sys.stderr)
    else:
        # RAW format binary scan
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            sois = [m.start() for m in re.finditer(b"\xff\xd8\xff", data)]
            candidates = []
            for start in sois:
                end = data.find(b"\xff\xd9", start)
                if end != -1:
                    candidates.append((start, end + 2, end + 2 - start))
            candidates.sort(key=lambda x: x[2], reverse=True)
            
            for start, end, size in candidates:
                if size < 15 * 1024:
                    continue
                try:
                    # Validate it's a valid JPEG image
                    with Image.open(io.BytesIO(data[start:end])) as img:
                        img.verify()
                    jpeg_bytes = data[start:end]
                    break
                except Exception:
                    continue
        except Exception as e:
            print(f"RAW extraction failed for {file_path}: {e}", file=sys.stderr)

    if not jpeg_bytes:
        return None, {}

    # 2. Rescaled base64 thumbnail
    base64_thumb = None
    try:
        with Image.open(io.BytesIO(jpeg_bytes)) as img:
            img.thumbnail(max_size)
            buffered = io.BytesIO()
            img.save(buffered, format="JPEG", quality=80)
            base64_thumb = base64.b64encode(buffered.getvalue()).decode("utf-8")
    except Exception as e:
        print(f"Failed to generate thumbnail: {e}", file=sys.stderr)

    # 3. Compute training services metrics on-the-fly
    metrics = {}
    try:
        from services.training import compute_exposure_metrics, compute_histogram_signature, compute_dominant_colors
        
        metrics["exp"] = compute_exposure_metrics(jpeg_bytes)
        metrics["hist"] = compute_histogram_signature(jpeg_bytes)
        metrics["dominant"] = compute_dominant_colors(jpeg_bytes)
    except Exception as e:
        print(f"Warning: Failed to compute AI metrics dynamically: {e}", file=sys.stderr)
        
    return base64_thumb, metrics

def query_chroma_training_examples(db_path, sample_size):
    import chromadb
    print("Connecting to Chroma DB and listing training examples...")
    client = chromadb.PersistentClient(path=db_path)
    try:
        col = client.get_collection("edit_training")
    except Exception as e:
        print(f"Error: Could not retrieve 'edit_training' collection: {e}", file=sys.stderr)
        sys.exit(1)
        
    total_count = col.count()
    print(f"Total training examples stored: {total_count}")
    
    if total_count == 0:
        print("No training examples found. Cannot generate report.", file=sys.stderr)
        sys.exit(0)
        
    results = col.get(include=["metadatas"])
    ids = results.get("ids", [])
    metadatas = results.get("metadatas", [])
    
    examples = []
    for idx, pid in enumerate(ids):
        meta = dict(metadatas[idx]) if idx < len(metadatas) else {}
        examples.append({
            "photo_id": pid,
            "filename": meta.get("filename", ""),
            "label": meta.get("label", ""),
            "summary": meta.get("summary", ""),
            "captured_at": meta.get("captured_at", ""),
            "focal_length_bucket": meta.get("focal_length_bucket", "unknown"),
            "time_of_day_bucket": meta.get("time_of_day_bucket", "unknown"),
            "camera_make": meta.get("camera_make", ""),
            "camera_model": meta.get("camera_model", ""),
            "camera_profile": meta.get("camera_profile", ""),
            "iso": meta.get("iso"),
            "aperture": meta.get("aperture"),
            "shutter_speed": meta.get("shutter_speed", ""),
            "scene_tags": json.loads(meta.get("scene_tags", "[]")),
            "user_keywords": json.loads(meta.get("user_keywords", "[]")),
            "develop_settings": json.loads(meta.get("develop_settings", "{}")),
            "canonical_settings": json.loads(meta.get("canonical_settings", "{}")),
            "has_embedding": bool(meta.get("has_embedding", False)),
        })
        
    sample_count = min(sample_size, len(examples))
    sampled_examples = random.sample(examples, sample_count)
    print(f"Randomly sampled {sample_count} examples for the report.")
    
    return sampled_examples, total_count, examples

def generate_html_report(sampled, total_count, all_examples, path_mapping, output_path):
    print("Generating rich HTML report with dynamic metrics...")
    
    # Calculate stats for the dashboard
    cameras = {}
    labels = {}
    time_of_days = {}
    focals = {}
    
    for ex in all_examples:
        cam = f"{ex['camera_make'] or ''} {ex['camera_model'] or ''}".strip() or "Unknown Camera"
        cameras[cam] = cameras.get(cam, 0) + 1
        
        lbl = ex["label"] or "Unlabeled"
        labels[lbl] = labels.get(lbl, 0) + 1
        
        tod = ex["time_of_day_bucket"] or "unknown"
        time_of_days[tod] = time_of_days.get(tod, 0) + 1
        
        fl = ex["focal_length_bucket"] or "unknown"
        focals[fl] = focals.get(fl, 0) + 1
        
    sorted_cameras = sorted(cameras.items(), key=lambda x: x[1], reverse=True)[:5]
    sorted_labels = sorted(labels.items(), key=lambda x: x[1], reverse=True)[:5]
    sorted_tod = sorted(time_of_days.items(), key=lambda x: x[1], reverse=True)
    sorted_focals = sorted(focals.items(), key=lambda x: x[1], reverse=True)
    
    # Prepare HTML Content
    html = []
    html.append("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Training Examples Catalog Overview</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-base: #08090d;
            --bg-surface: #10121e;
            --bg-card: #16192a;
            --border-color: #242940;
            --text-primary: #f9fafb;
            --text-secondary: #cbd5e1;
            --text-muted: #64748b;
            --accent-blue: #3b82f6;
            --accent-purple: #8b5cf6;
            --accent-green: #10b981;
            --accent-orange: #f59e0b;
            --accent-gradient: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
            --font-main: 'Plus Jakarta Sans', sans-serif;
            --font-code: 'JetBrains Mono', monospace;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: var(--font-main);
            line-height: 1.5;
            padding: 2.5rem;
        }

        header {
            margin-bottom: 2.5rem;
            position: relative;
        }

        .header-content {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
        }

        h1 {
            font-size: 2.5rem;
            font-weight: 800;
            background: var(--accent-gradient);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.05em;
        }

        .subtitle {
            color: var(--text-secondary);
            font-size: 1.1rem;
            margin-top: 0.5rem;
            font-weight: 400;
        }

        .meta-stamp {
            text-align: right;
            font-size: 0.9rem;
            color: var(--text-muted);
            font-family: var(--font-code);
        }

        /* Dashboard Styles */
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 1.5rem;
            margin-bottom: 3rem;
        }

        .stat-card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: 1rem;
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
            transition: transform 0.2s ease, border-color 0.2s ease;
        }

        .stat-card:hover {
            transform: translateY(-2px);
            border-color: var(--accent-blue);
        }

        .stat-card h3 {
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            margin-bottom: 1rem;
        }

        .stat-value {
            font-size: 2.5rem;
            font-weight: 800;
            color: var(--text-primary);
            line-height: 1;
        }

        .stat-list {
            list-style: none;
            margin-top: 0.5rem;
        }

        .stat-list-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.82rem;
            padding: 0.35rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
        }

        .stat-list-item:last-child {
            border-bottom: none;
        }

        .stat-item-label {
            color: var(--text-secondary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 180px;
        }

        .stat-item-count {
            font-family: var(--font-code);
            font-weight: 600;
            background-color: rgba(255, 255, 255, 0.08);
            padding: 0.1rem 0.5rem;
            border-radius: 0.5rem;
            font-size: 0.72rem;
        }

        /* Grid Layout for Photos */
        .photo-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(420px, 1fr));
            gap: 2.5rem;
        }

        .photo-card {
            background-color: var(--bg-surface);
            border: 1px solid var(--border-color);
            border-radius: 1.25rem;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), border-color 0.3s ease;
        }

        .photo-card:hover {
            transform: translateY(-4px) scale(1.01);
            border-color: rgba(255, 255, 255, 0.2);
        }

        .image-container {
            position: relative;
            width: 100%;
            height: 260px;
            background-color: #0b0c15;
            display: flex;
            align-items: center;
            justify-content: center;
            overflow: hidden;
        }

        .image-container img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }

        .svg-placeholder {
            width: 100%;
            height: 100%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            background: linear-gradient(45deg, #10121d 0%, #1c1e2f 100%);
            color: var(--text-muted);
            text-align: center;
            padding: 2rem;
        }

        .svg-placeholder svg {
            width: 48px;
            height: 48px;
            margin-bottom: 1rem;
            stroke: var(--text-muted);
        }

        .svg-placeholder span {
            font-size: 0.8rem;
            font-family: var(--font-code);
            word-break: break-all;
        }

        .card-badge {
            position: absolute;
            top: 1rem;
            left: 1rem;
            background: var(--accent-gradient);
            color: white;
            padding: 0.35rem 0.85rem;
            border-radius: 2rem;
            font-size: 0.72rem;
            font-weight: 700;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .card-date {
            position: absolute;
            bottom: 1rem;
            right: 1rem;
            background-color: rgba(9, 10, 15, 0.8);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            color: var(--text-primary);
            padding: 0.25rem 0.65rem;
            border-radius: 0.5rem;
            font-size: 0.7rem;
            font-family: var(--font-code);
            border: 1px solid rgba(255, 255, 255, 0.1);
        }

        .card-details {
            padding: 1.5rem;
            display: flex;
            flex-direction: column;
            flex-grow: 1;
            gap: 1.25rem;
        }

        .card-header {
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }

        .photo-title {
            font-size: 1.2rem;
            font-weight: 700;
            color: var(--text-primary);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .photo-summary {
            font-size: 0.85rem;
            color: var(--text-secondary);
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
            height: 2.5rem;
        }

        /* Metadata Pill Containers */
        .pill-container {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
        }

        .pill {
            font-size: 0.7rem;
            padding: 0.2rem 0.5rem;
            border-radius: 0.35rem;
            font-weight: 600;
            text-transform: capitalize;
        }

        .pill-scene {
            background-color: rgba(139, 92, 246, 0.15);
            color: #c084fc;
            border: 1px solid rgba(139, 92, 246, 0.25);
        }

        .pill-keyword {
            background-color: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            border: 1px solid rgba(59, 130, 246, 0.25);
        }

        .pill-exif {
            background-color: rgba(255, 255, 255, 0.05);
            color: var(--text-secondary);
            border: 1px solid rgba(255, 255, 255, 0.1);
            font-family: var(--font-code);
            font-size: 0.72rem;
        }

        .pill-profile {
            background-color: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.25);
            font-size: 0.7rem;
        }

        .pill-vector {
            background-color: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.25);
            font-size: 0.7rem;
            text-transform: uppercase;
            font-family: var(--font-code);
        }

        /* Dominant Colors */
        .color-palette {
            display: flex;
            height: 14px;
            border-radius: 7px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.1);
            margin: 0.15rem 0;
        }

        .color-swatch {
            flex: 1;
            height: 100%;
        }

        /* Visual AI Analytics Section */
        .ai-analytics-box {
            background-color: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-color);
            border-radius: 0.75rem;
            padding: 0.85rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .ai-box-title {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            color: var(--text-muted);
            font-weight: 800;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            padding-bottom: 0.35rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* Tonal Zone Stacked Bar */
        .zone-stacked-bar {
            display: flex;
            height: 10px;
            border-radius: 5px;
            overflow: hidden;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }
        .zone-segment {
            height: 100%;
            transition: opacity 0.2s ease;
        }
        .zone-segment:hover {
            opacity: 0.8;
        }
        .zone-ds { background-color: #0b0c16; }
        .zone-sh { background-color: #3b4260; }
        .zone-md { background-color: #707a9e; }
        .zone-hl { background-color: #c0c9e6; }
        .zone-bh { background-color: #ffffff; }

        /* Metric Grid */
        .metric-grid {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 0.5rem;
            font-size: 0.72rem;
        }
        .metric-item {
            display: flex;
            flex-direction: column;
            background-color: rgba(255, 255, 255, 0.02);
            padding: 0.35rem 0.5rem;
            border-radius: 0.5rem;
            border: 1px solid rgba(255, 255, 255, 0.03);
        }
        .metric-label {
            color: var(--text-muted);
            font-size: 0.65rem;
        }
        .metric-val {
            font-weight: 700;
            color: var(--text-primary);
            font-family: var(--font-code);
        }

        /* SVG Histogram Sparkline */
        .histogram-svg-container {
            width: 100%;
            height: 35px;
            margin-top: 0.2rem;
            display: flex;
            align-items: flex-end;
        }

        /* Develop Sliders Section */
        .develop-settings {
            border-top: 1px solid var(--border-color);
            padding-top: 1rem;
            margin-top: 0.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .settings-grid {
            display: grid;
            grid-template-columns: 1fr;
            gap: 1rem;
        }
        
        @media (min-width: 480px) {
            .settings-grid {
                grid-template-columns: 1fr 1fr;
            }
        }

        .settings-column {
            display: flex;
            flex-direction: column;
            gap: 0.4rem;
        }

        .slider-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 0.72rem;
        }

        .slider-label {
            color: var(--text-secondary);
            width: 80px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .slider-track-container {
            flex-grow: 1;
            margin: 0 0.5rem;
            height: 4px;
            position: relative;
            background-color: rgba(255, 255, 255, 0.06);
            border-radius: 2px;
        }

        .slider-center-tick {
            position: absolute;
            left: 50%;
            top: -2px;
            width: 2px;
            height: 8px;
            background-color: rgba(255, 255, 255, 0.2);
        }

        .slider-fill {
            position: absolute;
            height: 100%;
            border-radius: 2px;
        }

        .slider-fill.negative {
            background-color: #ef4444;
        }

        .slider-fill.positive {
            background-color: #10b981;
        }

        .slider-value {
            font-family: var(--font-code);
            width: 40px;
            text-align: right;
            color: var(--text-primary);
            font-size: 0.72rem;
        }

        footer {
            margin-top: 5rem;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.8rem;
            border-top: 1px solid var(--border-color);
            padding-top: 2rem;
        }
    </style>
</head>
<body>
    <header>
        <div class="header-content">
            <div>
                <h1>AI Training Examples Catalog</h1>
                <p class="subtitle">Rich metadata, exposure metrics, and slider ingestion overview</p>
            </div>
            <div class="meta-stamp">
                <p>Generated: """ + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + """</p>
                <p>Total Examples: """ + str(total_count) + """</p>
                <p>Report Sample: """ + str(len(sampled)) + """</p>
            </div>
        </div>
    </header>

    <!-- Dashboard -->
    <div class="dashboard">
        <div class="stat-card">
            <h3>Total Ingestion</h3>
            <div class="stat-value">""" + f"{total_count:,}" + """</div>
            <p style="font-size:0.82rem; color:var(--text-secondary); margin-top:0.5rem;">
                Style training examples registered in ChromaDB. Vector space matches edit profiles based on similarity.
            </p>
        </div>

        <div class="stat-card">
            <h3>Top Style Labels</h3>
            <ul class="stat-list">""")
            
    for lbl, count in sorted_labels:
        html.append(f"""
                <li class="stat-list-item">
                    <span class="stat-item-label">{lbl}</span>
                    <span class="stat-item-count">{count}</span>
                </li>""")
        
    html.append("""
            </ul>
        </div>

        <div class="stat-card">
            <h3>Top Cameras</h3>
            <ul class="stat-list">""")
            
    for cam, count in sorted_cameras:
        html.append(f"""
                <li class="stat-list-item">
                    <span class="stat-item-label">{cam}</span>
                    <span class="stat-item-count">{count}</span>
                </li>""")
                
    html.append("""
            </ul>
        </div>

        <div class="stat-card">
            <h3>Time of Day & Focal Ranges</h3>
            <ul class="stat-list">""")
            
    for idx in range(min(5, max(len(sorted_tod), len(sorted_focals)))):
        tod_str = ""
        if idx < len(sorted_tod):
            tod_str = f"{sorted_tod[idx][0].replace('_',' ').capitalize()}: {sorted_tod[idx][1]}"
        foc_str = ""
        if idx < len(sorted_focals):
            foc_str = f"{sorted_focals[idx][0].replace('_',' ').capitalize()}: {sorted_focals[idx][1]}"
            
        html.append(f"""
                <li class="stat-list-item" style="border-bottom:none; padding:0.15rem 0;">
                    <span class="stat-item-label" style="font-size:0.8rem;">{tod_str}</span>
                    <span class="stat-item-label" style="font-size:0.8rem; text-align:right;">{foc_str}</span>
                </li>""")
                
    html.append("""
            </ul>
        </div>
    </div>

    <!-- Photo Cards Grid -->
    <div class="photo-grid">""")
    
    # Process and Render Photo Cards
    for i, item in enumerate(sampled):
        global_id = item["photo_id"]
        filename = item["filename"] or f"ID: {global_id[:16]}..."
        label = item["label"] or "STYLE RECORD"
        summary = item["summary"] or "No description provided for this style training example."
        file_path = path_mapping.get(global_id)
        
        # 1. Process thumbnail & metrics dynamically
        base64_thumb, ai_metrics = extract_thumbnail_and_compute_metrics(file_path)
        
        # Format EXIF
        exif_parts = []
        if item["camera_model"]:
            exif_parts.append(item["camera_model"])
        if item["focal_length_bucket"] and item["focal_length_bucket"] != "unknown":
            exif_parts.append(item["focal_length_bucket"].replace("_", " "))
        if item["iso"]:
            exif_parts.append(f"ISO {int(item['iso'])}")
        if item["aperture"]:
            exif_parts.append(f"f/{item['aperture']}")
        if item["shutter_speed"]:
            exif_parts.append(item["shutter_speed"])
            
        # Captured Date
        captured_str = "Unknown Date"
        if item["captured_at"]:
            try:
                dt = datetime.strptime(item["captured_at"], "%Y-%m-%d %H:%M:%S")
                captured_str = dt.strftime("%b %d, %Y %I:%M %p")
            except Exception:
                captured_str = item["captured_at"]
                
        # Card HTML
        html.append(f"""
        <!-- Photo Card {i+1} -->
        <div class="photo-card">
            <div class="image-container">""")
            
        if base64_thumb:
            html.append(f"""
                <img src="data:image/jpeg;base64,{base64_thumb}" alt="{filename}">""")
        else:
            html.append(f"""
                <div class="svg-placeholder">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                        <circle cx="8.5" cy="8.5" r="1.5"></circle>
                        <polyline points="21 15 16 10 5 21"></polyline>
                    </svg>
                    <span>{filename}</span>
                </div>""")
                
        html.append(f"""
                <div class="card-badge">{label}</div>
                <div class="card-date">{captured_str}</div>
            </div>
            
            <div class="card-details">
                <div class="card-header">
                    <div class="photo-title">{filename}</div>
                    <div class="photo-summary">{summary}</div>
                </div>""")
                
        # Tags Container
        html.append("""
                <div class="pill-container">""")
        
        # Profile & Vector state tags
        if item["camera_profile"]:
            html.append(f"""<span class="pill pill-profile">Profile: {item["camera_profile"]}</span>""")
        if item["has_embedding"]:
            html.append("""<span class="pill pill-vector">CLIP READY</span>""")
        else:
            html.append("""<span class="pill pill-exif" style="color:var(--text-muted);">METADATA ONLY</span>""")
            
        # CLIP zero-shot scene tags and user keywords
        for tag in item["scene_tags"]:
            tag_txt = tag.replace("scene_", "").replace("style_", "").replace("_", " ")
            html.append(f"""<span class="pill pill-scene">{tag_txt}</span>""")
        for kw in item["user_keywords"]:
            html.append(f"""<span class="pill pill-keyword">{kw.replace('_', ' ')}</span>""")
        html.append("""
                </div>""")
                
        # EXIF Pill Container
        if exif_parts:
            html.append("""
                <div class="pill-container">""")
            for p in exif_parts:
                html.append(f"""<span class="pill pill-exif">{p}</span>""")
            html.append("""
                </div>""")
                
        # Dynamic AI Metrics & Exposure Analytics Box
        if ai_metrics:
            exp_data = ai_metrics.get("exp", {})
            hist_data = ai_metrics.get("hist", {})
            dom_colors = ai_metrics.get("dominant", [])
            
            html.append("""
                <!-- AI Exposure & Tonal Analysis -->
                <div class="ai-analytics-box">
                    <div class="ai-box-title">
                        <span>AI Tonal & Exposure Metrics</span>
                        <span style="font-family:var(--font-code); font-size:0.6rem; color:var(--accent-blue);">Computed On-The-Fly</span>
                    </div>""")
            
            # Stacked Zone Bar
            if "zone_deep_shadows" in exp_data:
                ds = exp_data.get("zone_deep_shadows", 0)
                sh = exp_data.get("zone_shadows", 0)
                md = exp_data.get("zone_midtones", 0)
                hl = exp_data.get("zone_highlights", 0)
                bh = exp_data.get("zone_bright_highlights", 0)
                total_zones = max(0.0001, ds + sh + md + hl + bh)
                
                html.append(f"""
                    <div class="zone-stacked-bar">
                        <div class="zone-segment zone-ds" style="width: {ds/total_zones*100:.1f}%;" title="Deep Shadows: {ds*100:.1f}%"></div>
                        <div class="zone-segment zone-sh" style="width: {sh/total_zones*100:.1f}%;" title="Shadows: {sh*100:.1f}%"></div>
                        <div class="zone-segment zone-md" style="width: {md/total_zones*100:.1f}%;" title="Midtones: {md*100:.1f}%"></div>
                        <div class="zone-segment zone-hl" style="width: {hl/total_zones*100:.1f}%;" title="Highlights: {hl*100:.1f}%"></div>
                        <div class="zone-segment zone-bh" style="width: {bh/total_zones*100:.1f}%;" title="Bright Highlights: {bh*100:.1f}%"></div>
                    </div>""")
                    
            # Metric grid
            html.append("""
                    <div class="metric-grid">""")
            if "exp_luminance_mean" in exp_data:
                html.append(f"""
                        <div class="metric-item">
                            <span class="metric-label">Luminance</span>
                            <span class="metric-val">{exp_data['exp_luminance_mean']*100:.1f}%</span>
                        </div>""")
            if "exp_colorfulness" in exp_data:
                html.append(f"""
                        <div class="metric-item">
                            <span class="metric-label">Vividness</span>
                            <span class="metric-val">{exp_data['exp_colorfulness']*100:.1f}%</span>
                        </div>""")
            if "exp_warmth_proxy" in exp_data:
                html.append(f"""
                        <div class="metric-item">
                            <span class="metric-label">Warmth Balance</span>
                            <span class="metric-val">{exp_data['exp_warmth_proxy']*100:.1f}%</span>
                        </div>""")
            html.append("""
                    </div>""")
                    
            # Histogram sparkline (LAB luminance bins)
            hist_L = hist_data.get("hist_L", [])
            if hist_L and len(hist_L) == 16:
                points_list = []
                max_val = max(hist_L) if max(hist_L) > 0 else 1
                for idx, val in enumerate(hist_L):
                    x = idx * 10
                    # Scale to 30px height, pad 2px
                    y = 33 - int((val / max_val) * 28)
                    points_list.append(f"{x},{y}")
                points_str = " ".join(points_list)
                
                html.append(f"""
                    <!-- LAB Luminance Histogram Sparkline -->
                    <div class="histogram-svg-container">
                        <div style="font-size:0.62rem; color:var(--text-muted); font-family:var(--font-code); width:65px; margin-bottom: 2px;">Tonal Curve:</div>
                        <svg viewBox="0 0 150 35" style="flex-grow:1; height:30px;">
                            <defs>
                                <linearGradient id="sparklineGrad-{i}" x1="0%" y1="0%" x2="100%" y2="0%">
                                    <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.6"/>
                                    <stop offset="50%" stop-color="#8b5cf6" stop-opacity="0.6"/>
                                    <stop offset="100%" stop-color="#10b981" stop-opacity="0.6"/>
                                </linearGradient>
                            </defs>
                            <polyline fill="none" stroke="rgba(255, 255, 255, 0.1)" stroke-width="1" points="0,33 150,33"/>
                            <path d="M 0,33 L {points_str} L 150,33 Z" fill="url(#sparklineGrad-{i})"/>
                            <polyline fill="none" stroke="#60a5fa" stroke-width="1.5" points="{points_str}"/>
                        </svg>
                    </div>""")
            
            # Dominant Colors Swatch
            if dom_colors:
                html.append("""
                    <div class="color-palette">""")
                for color in dom_colors:
                    html.append(f"""<div class="color-swatch" style="background-color: {color};" title="{color}"></div>""")
                html.append("""
                    </div>""")
                    
            html.append("""
                </div>""")
                
        # Sliders Section
        canon = item["canonical_settings"]
        if canon:
            html.append("""
                <div class="develop-settings">
                    <div class="settings-title">Sliders & Parameters Ingested</div>
                    <div class="settings-grid">""")
            
            # Helper to generate HTML for a basic adjustment (-100 to 100)
            def gen_basic_slider_html(slider_key, label_name, min_val=-100.0, max_val=100.0):
                if slider_key not in canon:
                    return ""
                val = canon[slider_key]
                span = max_val - min_val
                percent = min(100, max(0, int((val - min_val) / span * 100)))
                
                fill_style = ""
                # Center point is relative
                center_pct = int(-min_val / span * 100)
                if val < 0:
                    width = center_pct - percent
                    fill_style = f"left: {percent}%; width: {width}%;"
                    fill_class = "negative"
                else:
                    width = percent - center_pct
                    fill_style = f"left: {center_pct}%; width: {width}%;"
                    fill_class = "positive"
                    
                prefix = "+" if val > 0 else ""
                val_str = f"{prefix}{int(val)}"
                
                return f"""
                        <div class="slider-row">
                            <span class="slider-label">{label_name}</span>
                            <div class="slider-track-container" title="Value: {val}">
                                <div class="slider-center-tick" style="left: {center_pct}%;"></div>
                                <div class="slider-fill {fill_class}" style="{fill_style}"></div>
                            </div>
                            <span class="slider-value">{val_str}</span>
                        </div>"""

            # Column 1: Tone adjusting
            html.append("""
                        <!-- Column 1: Core Tone sliders -->
                        <div class="settings-column">""")
            # Exposure (-5.0 to 5.0)
            if "exposure" in canon:
                val = canon["exposure"]
                percent = min(100, max(0, int((val + 5.0) / 10.0 * 100)))
                if val < 0:
                    width = 50 - percent
                    fill_style = f"left: {percent}%; width: {width}%;"
                    fill_class = "negative"
                else:
                    width = percent - 50
                    fill_style = f"left: 50%; width: {width}%;"
                    fill_class = "positive"
                html.append(f"""
                            <div class="slider-row">
                                <span class="slider-label">Exposure</span>
                                <div class="slider-track-container">
                                    <div class="slider-center-tick"></div>
                                    <div class="slider-fill {fill_class}" style="{fill_style}"></div>
                                </div>
                                <span class="slider-value">{"+" if val > 0 else ""}{val:.2f}</span>
                            </div>""")
                            
            html.append(gen_basic_slider_html("contrast", "Contrast"))
            html.append(gen_basic_slider_html("highlights", "Highlights"))
            html.append(gen_basic_slider_html("shadows", "Shadows"))
            html.append(gen_basic_slider_html("whites", "Whites"))
            html.append(gen_basic_slider_html("blacks", "Blacks"))
            html.append(gen_basic_slider_html("vibrance", "Vibrance"))
            html.append(gen_basic_slider_html("saturation", "Saturation"))
            html.append("""
                        </div>""")
            
            # Column 2: Color, Detail, Curves
            html.append("""
                        <!-- Column 2: Presence, Curve & Detail sliders -->
                        <div class="settings-column">""")
            html.append(gen_basic_slider_html("texture", "Texture"))
            html.append(gen_basic_slider_html("clarity", "Clarity"))
            html.append(gen_basic_slider_html("dehaze", "Dehaze"))
            
            # Parametric Curves
            html.append(gen_basic_slider_html("tone_curve_highlights", "Curve High"))
            html.append(gen_basic_slider_html("tone_curve_lights", "Curve Light"))
            html.append(gen_basic_slider_html("tone_curve_darks", "Curve Dark"))
            html.append(gen_basic_slider_html("tone_curve_shadows", "Curve Shadow"))
            
            # Detail/Effects (non-centered: 0 to Max)
            html.append(gen_basic_slider_html("sharpening", "Sharpening", min_val=0.0, max_val=150.0))
            html.append(gen_basic_slider_html("noise_reduction", "Noise Red.", min_val=0.0, max_val=100.0))
            html.append(gen_basic_slider_html("vignette", "Vignette"))
            html.append(gen_basic_slider_html("grain", "Grain", min_val=0.0, max_val=100.0))
            html.append("""
                        </div>""")
            
            html.append("""
                    </div>""")
            
            # Temp/Tint details
            if "temperature" in canon or "tint" in canon:
                t_val = canon.get("temperature")
                ti_val = canon.get("tint")
                temp_str = f"Temp: {int(t_val)}" if t_val is not None else ""
                tint_str = f"Tint: {int(ti_val)}" if ti_val is not None else ""
                sep = " / " if temp_str and tint_str else ""
                html.append(f"""
                    <div style="font-size: 0.72rem; color: var(--text-muted); font-family: var(--font-code); display: flex; justify-content: flex-end; margin-top: 0.2rem;">
                        {temp_str}{sep}{tint_str}
                    </div>""")
                    
            html.append("""
                </div>""")
                
        html.append("""
            </div>
        </div>""")
        
    html.append("""
    </div>
    
    <footer style="margin-top: 5rem; text-align: center; color: var(--text-muted); font-size: 0.8rem; border-top: 1px solid var(--border-color); padding-top: 2rem;">
        <p>StyleAI Catalog Diagnostic Report &bull; Privacy-Centric Local Analysis</p>
    </footer>
</body>
</html>""")
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(html))
        
    print(f"Report successfully saved to: {output_path}")

def main():
    args = parse_args()
    db_path, lrcat_path = get_db_connections(args.catalog_dir)
    path_mapping = query_lightroom_file_paths(lrcat_path)
    sampled, total_count, all_examples = query_chroma_training_examples(db_path, args.sample_size)
    generate_html_report(sampled, total_count, all_examples, path_mapping, args.output)
    
if __name__ == "__main__":
    main()
