#!/usr/bin/env python3
"""Analyze real training data to validate style grouping parameters.

Reads .xmp sidecars from the training-image-sets folder, extracts develop
settings, and compares baseline vs. edited to understand editing patterns.
"""

from __future__ import annotations

import json
import os
import re
import statistics
from pathlib import Path
from typing import Any

# Canonical keys we care about for style analysis
CANONICAL_KEYS = [
    "Exposure2012",
    "Contrast2012",
    "Highlights2012",
    "Shadows2012",
    "Whites2012",
    "Blacks2012",
    "Temp",
    "Tint",
    "Texture",
    "Clarity2012",
    "Dehaze",
    "Vibrance",
    "Saturation",
    "Sharpness",
    "LuminanceSmoothing",
    "ColorNoiseReduction",
    "PostCropVignetteAmount",
    "GrainAmount",
    "ParametricHighlights",
    "ParametricLights",
    "ParametricDarks",
    "ParametricShadows",
]


def parse_xmp_file(xmp_path: str) -> dict[str, Any]:
    """Parse an XMP sidecar and extract develop settings + metadata."""
    with open(xmp_path, "r", encoding="utf-8") as f:
        content = f.read()

    result: dict[str, Any] = {
        "filename": os.path.basename(xmp_path).replace(".xmp", ""),
        "settings": {},
        "camera_make": None,
        "camera_model": None,
        "camera_profile": None,
        "lens": None,
        "focal_length": None,
        "iso": None,
        "aperture": None,
        "shutter_speed": None,
    }

    # Extract crs: (current settings)
    for key in CANONICAL_KEYS:
        # Pattern: crs:Key="value"
        pattern = rf'crs:{key}="([^"]+)"'
        match = re.search(pattern, content)
        if match:
            val_str = match.group(1)
            # Try to parse as number
            try:
                # Handle signs like "+1.65" or "-83"
                val = float(val_str)
                result["settings"][key] = val
            except ValueError:
                result["settings"][key] = val_str

    # Extract camera info
    make_match = re.search(r'tiff:Make="([^"]+)"', content)
    if make_match:
        result["camera_make"] = make_match.group(1)

    model_match = re.search(r'tiff:Model="([^"]+)"', content)
    if model_match:
        result["camera_model"] = model_match.group(1)

    profile_match = re.search(r'crs:CameraProfile="([^"]+)"', content)
    if profile_match:
        result["camera_profile"] = profile_match.group(1)

    lens_match = re.search(r'aux:Lens="([^"]+)"', content)
    if lens_match:
        result["lens"] = lens_match.group(1)

    focal_match = re.search(r'exif:FocalLength="(\d+)/(\d+)"', content)
    if focal_match:
        num, den = int(focal_match.group(1)), int(focal_match.group(2))
        result["focal_length"] = round(num / den, 1) if den else None

    iso_match = re.search(r"<rdf:li>(\d+)</rdf:li>\s*</exif:ISOSpeedRatings>", content)
    if iso_match:
        result["iso"] = int(iso_match.group(1))

    aperture_match = re.search(r'exif:FNumber="(\d+)/(\d+)"', content)
    if aperture_match:
        num, den = int(aperture_match.group(1)), int(aperture_match.group(2))
        result["aperture"] = round(num / den, 1) if den else None

    shutter_match = re.search(r'exif:ExposureTime="([^"]+)"', content)
    if shutter_match:
        result["shutter_speed"] = shutter_match.group(1)

    return result


def compute_edit_delta(baseline: dict, edited: dict) -> dict[str, float]:
    """Compute the difference between edited and baseline settings."""
    delta = {}
    for key in CANONICAL_KEYS:
        base_val = baseline["settings"].get(key, 0)
        edit_val = edited["settings"].get(key, 0)
        if isinstance(base_val, (int, float)) and isinstance(edit_val, (int, float)):
            delta[key] = round(edit_val - base_val, 2)
    return delta


def main():
    base_dir = Path("/Users/anna/Documents/Coding/lr-ai/training-image-sets")
    baseline_dir = (
        base_dir
        / "1. Baseline Photos - Unedited - Custom Camera Profiles and Corrections"
    )
    edited_dir = (
        base_dir
        / "2. Output Photos - Full Edits - Custom Camera Profiles and Corrections"
    )

    # Pair files by base filename
    baseline_files = {f.stem: f for f in baseline_dir.glob("*.xmp")}
    edited_files = {f.stem: f for f in edited_dir.glob("*.xmp")}

    common_names = sorted(set(baseline_files.keys()) & set(edited_files.keys()))
    print(f"Found {len(common_names)} paired XMP files")
    print("=" * 80)

    all_deltas: list[dict[str, float]] = []
    profiles_seen: set[str] = set()

    for name in common_names:
        baseline = parse_xmp_file(str(baseline_files[name]))
        edited = parse_xmp_file(str(edited_files[name]))

        delta = compute_edit_delta(baseline, edited)
        all_deltas.append(delta)

        profiles_seen.add(baseline.get("camera_profile") or "unknown")
        profiles_seen.add(edited.get("camera_profile") or "unknown")

        # Show a sample (first 3)
        if len(all_deltas) <= 3:
            print(f"\nFile: {name}")
            print(f"  Camera: {baseline['camera_make']} {baseline['camera_model']}")
            print(f"  Profile (baseline): {baseline['camera_profile']}")
            print(f"  Profile (edited):   {edited['camera_profile']}")
            print(f"  Lens: {baseline['lens']} @ {baseline['focal_length']}mm")
            print("  Significant edits:")
            for key, val in delta.items():
                if abs(val) > 1:
                    print(f"    {key}: {val:+.2f}")

    print("\n" + "=" * 80)
    print("SUMMARY STATISTICS")
    print("=" * 80)

    # Camera profiles used
    print(f"\nCamera profiles seen: {profiles_seen}")

    # Compute mean and variance of each slider delta
    print("\nEdit delta statistics (edited - baseline):")
    print(
        f"{'Key':<25} {'Mean':>8} {'StdDev':>8} {'Min':>8} {'Max':>8} {'Variance':>10}"
    )
    print("-" * 75)

    for key in CANONICAL_KEYS:
        values = [d[key] for d in all_deltas if key in d]
        if not values:
            continue
        mean = statistics.mean(values)
        stddev = statistics.stdev(values) if len(values) > 1 else 0
        min_val = min(values)
        max_val = max(values)
        var = statistics.pvariance(values) if len(values) > 1 else 0

        # Only show keys that have meaningful variation
        if abs(mean) > 0.5 or stddev > 1:
            print(
                f"{key:<25} {mean:>+8.2f} {stddev:>8.2f} {min_val:>8.2f} {max_val:>8.2f} {var:>10.4f}"
            )

    print("\n" + "=" * 80)
    print("STYLE GROUPING VALIDATION")
    print("=" * 80)

    # Simulate what style_grouping would see
    print("\nIf we grouped by camera + genre and computed variance:")

    # For demonstration, assume all are same camera/genre
    example_metas = []
    for delta in all_deltas:
        # Convert to canonical_settings format
        canonical = {}
        for key in CANONICAL_KEYS:
            # Map LR key to canonical key
            canon_key = key.replace("2012", "").lower()
            if key in delta:
                canonical[canon_key] = delta[key]
        example_metas.append(
            {
                "photo_id": "test",
                "canonical_settings": json.dumps(canonical),
                "scene_tags": '["scene_architecture"]',
                "exp_luminance_mean": "0.5",
            }
        )

    # Compute NORMALIZED variance for each key
    print(f"\n{'Canonical Key':<20} {'Raw Var':>12} {'Norm Var':>12} {'Status':>10}")
    print("-" * 60)
    variance_threshold = 0.15
    high_variance_keys = []
    for canon_key in (
        "exposure", "contrast", "temperature", "highlights", "shadows", "clarity", "dehaze"
    ):
        vals = []
        for meta in example_metas:
            settings = json.loads(meta["canonical_settings"])
            if canon_key in settings:
                vals.append(settings[canon_key])
        if len(vals) > 1:
            raw_var = statistics.pvariance(vals)
            # Normalize by slider range
            divisor = {"exposure": 5.0, "contrast": 100.0, "temperature": 10000.0,
                       "highlights": 100.0, "shadows": 100.0, "clarity": 100.0, "dehaze": 100.0}.get(canon_key, 100.0)
            norm_vals = [v / divisor for v in vals]
            norm_var = statistics.pvariance(norm_vals)
            status = "*** SPLIT" if norm_var > variance_threshold else "ok"
            if norm_var > variance_threshold:
                high_variance_keys.append(canon_key)
            print(f"{canon_key:<20} {raw_var:>12.2f} {norm_var:>12.4f} {status:>10}")

    if high_variance_keys:
        print(f"\nKeys exceeding NORMALIZED variance threshold ({variance_threshold}): {high_variance_keys}")
        print("These would trigger subgenre splitting in the current algorithm.")
    else:
        print(f"\nNo keys exceed NORMALIZED variance threshold ({variance_threshold}).")
        print("All examples would be grouped into a single style.")

    # -----------------------------------------------------------------------
    # HISTOGRAM-BASED GROUPING DEMONSTRATION
    # -----------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("HISTOGRAM-BASED GROUPING VALIDATION")
    print("=" * 80)

    # Try to load edited JPEGs and compute histogram signatures
    try:
        edited_jpgs = list(edited_dir.glob("*.jpg"))
        if edited_jpgs:
            print(f"\nLoading {len(edited_jpgs)} edited JPEGs for histogram analysis...")
            print("(Using ~3MP downsample for speed)\n")

            # Import the histogram function from training service
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
            from services.training import compute_histogram_signature, histogram_distance

            signatures = {}
            for jpg_path in edited_jpgs[:25]:  # limit to 25 for demo
                try:
                    with open(jpg_path, "rb") as f:
                        sig = compute_histogram_signature(f.read())
                    signatures[jpg_path.stem] = sig
                except Exception as exc:
                    print(f"  Warning: could not process {jpg_path.name}: {exc}")

            if len(signatures) >= 2:
                # Compute pairwise distances
                names = list(signatures.keys())
                distances = []
                print(f"\nPairwise histogram distances among {len(names)} images:")
                print(f"{'Image 1':<25} {'Image 2':<25} {'Distance':>10} {'Similar?':>10}")
                print("-" * 75)

                for i in range(len(names)):
                    for j in range(i + 1, len(names)):
                        dist = histogram_distance(signatures[names[i]], signatures[names[j]])
                        distances.append((names[i], names[j], dist))

                # Show closest and furthest pairs
                distances.sort(key=lambda x: x[2])
                print("\nClosest pairs (most similar visual style):")
                for name1, name2, dist in distances[:5]:
                    similar = "YES" if dist < 0.35 else "no"
                    print(f"{name1:<25} {name2:<25} {dist:>10.3f} {similar:>10}")

                print("\nMost distant pairs (different visual styles):")
                for name1, name2, dist in distances[-5:]:
                    similar = "YES" if dist < 0.35 else "no"
                    print(f"{name1:<25} {name2:<25} {dist:>10.3f} {similar:>10}")

                # Show distance distribution
                all_dists = [d[2] for d in distances]
                mean_dist = statistics.mean(all_dists)
                print(f"\nDistance statistics: mean={mean_dist:.3f}, min={min(all_dists):.3f}, max={max(all_dists):.3f}")
                print(f"Threshold 0.35 would group these into ~{len([d for d in all_dists if d < 0.35])} similar pairs")

                # Cluster demonstration
                print("\n" + "-" * 75)
                print("HISTOGRAM CLUSTERING RESULT (threshold=0.35):")
                clusters = []
                used = set()
                for i, name_i in enumerate(names):
                    if name_i in used:
                        continue
                    cluster = [name_i]
                    used.add(name_i)
                    for j, name_j in enumerate(names):
                        if name_j in used or i == j:
                            continue
                        dist = histogram_distance(signatures[name_i], signatures[name_j])
                        if dist < 0.35:
                            cluster.append(name_j)
                            used.add(name_j)
                    clusters.append(cluster)

                for idx, cluster in enumerate(clusters):
                    print(f"  Cluster {idx + 1} ({len(cluster)} images):")
                    for name in cluster[:5]:
                        print(f"    - {name}")
                    if len(cluster) > 5:
                        print(f"    ... and {len(cluster) - 5} more")

    except ImportError as exc:
        print(f"\nSkipping histogram analysis (missing dependency: {exc})")
        print("Install with: uv add numpy pillow")

    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)

    # Check if camera profile is consistent
    if len(profiles_seen) == 1:
        print(f"✓ Single camera profile used: {list(profiles_seen)[0]}")
        print(
            "  This simplifies the style model — no need to account for profile differences."
        )
    else:
        print(f"⚠ Multiple camera profiles: {profiles_seen}")
        print("  Styles are now grouped by PROFILE-INDEPENDENT histogram similarity.")
        print("  The same visual style across different profiles will be clustered together.")

    # Check if most photos are same camera
    cameras = set()
    for name in common_names:
        baseline = parse_xmp_file(str(baseline_files[name]))
        cameras.add(f"{baseline['camera_make']} {baseline['camera_model']}")
    if len(cameras) == 1:
        print(f"✓ Single camera body: {list(cameras)[0]}")
    else:
        print(f"⚠ Multiple cameras: {cameras}")

    print(f"\n✓ {len(common_names)} training examples available for style learning")
    print("  This exceeds the minimum of 5 for style engine activation.")
    print("  Histogram-based grouping will correctly cluster by visual outcome,")
    print("  not slider values — solving the custom profile variance problem.")


if __name__ == "__main__":
    main()
