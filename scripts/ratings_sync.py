#!/usr/bin/env python3
"""
ratings_sync.py

A utility script to restore Lightroom Classic metadata (specifically star ratings and edits in XMP sidecars)
from a backup directory tree (source) to a working directory tree (destination).

It recursively crawls the destination directory, identifies RAW files, checks if they have an associated XMP,
and if they don't have an XMP or have an XMP without a star rating (unrated/0 stars), it copies the corresponding
XMP from the source directory.

CRUCIAL constraints:
- NEVER touch, move, delete, or modify RAW files.
- NEVER overwrite or modify destination XMP files that already contain a star rating (1-5).
- NEVER create, modify, or delete any directories.
- Default to Dry-Run mode to prevent accidental data modification.
"""

import os
import sys
import shutil
import re
import argparse
import xml.etree.ElementTree as ET
from typing import Optional, Tuple, Dict, Any

# Common RAW file extensions (case-insensitive)
RAW_EXTENSIONS = {
    ".nef",
    ".cr2",
    ".cr3",
    ".arw",
    ".dng",
    ".orf",
    ".rw2",
    ".pef",
    ".raf",
    ".srw",
    ".srf",
    ".sr2",
    ".crw",
    ".mrw",
    ".x3f",
    ".erf",
    ".mef",
    ".3fr",
    ".fff",
    ".mos",
    ".iiq",
}


def extract_rating_from_xmp(xmp_path: str) -> Optional[int]:
    """
    Parses an XMP sidecar file and returns the star rating as an integer if found.
    Returns None if the rating is missing, empty, or invalid.
    """
    if not os.path.isfile(xmp_path):
        return None

    try:
        with open(xmp_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        # 1. XML ElementTree parsing (cleanest way when XML namespace prefixes map correctly)
        try:
            root = ET.fromstring(content)
            for elem in root.iter():
                # Check attributes for any key ending with 'Rating'
                for key, val in elem.attrib.items():
                    if key.endswith("Rating") or (
                        ":" in key and key.split(":")[-1] == "Rating"
                    ):
                        try:
                            return int(val)
                        except ValueError:
                            pass
                # Check tag name
                tag = elem.tag
                if tag.endswith("Rating") or (
                    ":" in tag and tag.split(":")[-1] == "Rating"
                ):
                    if elem.text:
                        try:
                            return int(elem.text.strip())
                        except ValueError:
                            pass
        except Exception:
            # Fall back to regex parsing if ElementTree fails (e.g. malformed namespaces, entities, etc.)
            pass

        # 2. Regex parsing (extremely robust fallback)
        # Matches: xmp:Rating="3", Rating="3", <xmp:Rating>3</xmp:Rating>, etc.
        attr_match = re.search(r'\b(?:\w+:)?Rating=["\']\s*(\d+)\s*["\']', content)
        if attr_match:
            return int(attr_match.group(1))

        tag_match = re.search(
            r"<(?:\w+:)?Rating>\s*(\d+)\s*</(?:\w+:)?Rating>", content
        )
        if tag_match:
            return int(tag_match.group(1))

    except Exception:
        # Ignore and fail silently/gracefully, returning None
        pass

    return None


def has_valid_rating(xmp_path: str) -> bool:
    """
    Checks if the XMP file exists and contains a valid star rating (1 to 5).
    """
    rating = extract_rating_from_xmp(xmp_path)
    return rating is not None and 1 <= rating <= 5


def locate_xmp_sidecar(raw_path: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Given a RAW file path, checks for the existence of its XMP sidecar.
    Lightroom can use two styles:
      Style A: photo.xmp (replacing RAW extension)
      Style B: photo.NEF.xmp (appending .xmp)

    Returns a tuple of (existing_xmp_path, expected_xmp_path).
    If neither exists, returns (None, default_expected_xmp_path).
    """
    base_no_ext, ext = os.path.splitext(raw_path)
    style_a = base_no_ext + ".xmp"
    style_b = raw_path + ".xmp"

    if os.path.isfile(style_a):
        return style_a, style_a
    if os.path.isfile(style_b):
        return style_b, style_b

    # Default to Style A as it is the standard Lightroom convention
    return None, style_a


def sync_ratings(
    source_dir: str, dest_dir: str, dry_run: bool = True, verbose: bool = False
) -> Dict[str, int]:
    """
    Crawls the destination directory to identify RAW files needing XMP metadata,
    and copies the corresponding XMPs from the source directory.

    Returns a dictionary of counts summarizing the results.
    """
    stats = {
        "total_scanned_raws": 0,
        "existing_rated_xmps": 0,
        "existing_unrated_xmps": 0,
        "no_xmps": 0,
        "copied": 0,
        "skipped_not_found_in_source": 0,
        "errors": 0,
    }

    # Normalize paths
    source_dir = os.path.abspath(source_dir)
    dest_dir = os.path.abspath(dest_dir)

    if not os.path.isdir(source_dir):
        print(f"Error: Source path '{source_dir}' is not a directory.", file=sys.stderr)
        stats["errors"] += 1
        return stats

    if not os.path.isdir(dest_dir):
        print(
            f"Error: Destination path '{dest_dir}' is not a directory.", file=sys.stderr
        )
        stats["errors"] += 1
        return stats

    for root, _, files in os.walk(dest_dir):
        for file in files:
            _, ext = os.path.splitext(file)
            if ext.lower() not in RAW_EXTENSIONS:
                continue

            stats["total_scanned_raws"] += 1
            dst_raw_path = os.path.join(root, file)
            rel_raw_path = os.path.relpath(dst_raw_path, dest_dir)
            src_raw_path = os.path.join(source_dir, rel_raw_path)

            # Locate XMP in destination
            dst_xmp_path, default_dst_xmp = locate_xmp_sidecar(dst_raw_path)

            needs_sync = False
            if dst_xmp_path is None:
                # Case 1: No XMP sidecar exists in destination
                stats["no_xmps"] += 1
                needs_sync = True
                if verbose:
                    print(f"[VERBOSE] No XMP for RAW: {rel_raw_path}")
            else:
                # Case 2: XMP exists. Does it have a rating?
                if has_valid_rating(dst_xmp_path):
                    stats["existing_rated_xmps"] += 1
                    if verbose:
                        print(f"[VERBOSE] Skipping (already rated): {rel_raw_path}")
                else:
                    stats["existing_unrated_xmps"] += 1
                    needs_sync = True
                    if verbose:
                        print(
                            f"[VERBOSE] Needs overwrite (unrated XMP exists): {rel_raw_path}"
                        )

            if not needs_sync:
                continue

            # Locate corresponding XMP in source
            # First, check if source RAW base exists so we can map it
            src_xmp_path, _ = locate_xmp_sidecar(src_raw_path)

            # If not found via the exact relative RAW path check, check both styles directly
            if src_xmp_path is None:
                src_base_no_ext, src_ext = os.path.splitext(src_raw_path)
                style_a = src_base_no_ext + ".xmp"
                style_b = src_raw_path + ".xmp"
                if os.path.isfile(style_a):
                    src_xmp_path = style_a
                elif os.path.isfile(style_b):
                    src_xmp_path = style_b

            if src_xmp_path is None or not os.path.isfile(src_xmp_path):
                stats["skipped_not_found_in_source"] += 1
                if verbose:
                    print(f"[VERBOSE] Warning: No source XMP found for {rel_raw_path}")
                continue

            # Set target destination path
            if dst_xmp_path is None:
                # Use matching style from source
                rel_xmp_path = os.path.relpath(src_xmp_path, source_dir)
                target_dst_xmp = os.path.join(dest_dir, rel_xmp_path)
            else:
                target_dst_xmp = dst_xmp_path

            # Check source rating for informational logging
            src_rating = extract_rating_from_xmp(src_xmp_path)
            src_rating_str = (
                f"{src_rating} stars" if src_rating is not None else "no stars"
            )

            action_type = "Overwrite" if dst_xmp_path else "Copy"

            # Verify destination parent directory exists (must exist since the RAW file is inside it)
            dst_xmp_dir = os.path.dirname(target_dst_xmp)
            if not os.path.isdir(dst_xmp_dir):
                print(
                    f"Error: Target directory '{dst_xmp_dir}' does not exist. Skipping.",
                    file=sys.stderr,
                )
                stats["errors"] += 1
                continue

            if dry_run:
                print(
                    f"[DRY-RUN] Would {action_type.lower()} XMP from backup for '{rel_raw_path}' (source rating: {src_rating_str})"
                )
                stats["copied"] += 1
            else:
                try:
                    # Perform copy safely
                    shutil.copy2(src_xmp_path, target_dst_xmp)
                    print(
                        f"[{action_type}] XMP for '{rel_raw_path}' (rating: {src_rating_str})"
                    )
                    stats["copied"] += 1
                except Exception as e:
                    print(
                        f"Error: Failed to copy XMP for '{rel_raw_path}': {e}",
                        file=sys.stderr,
                    )
                    stats["errors"] += 1

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync/restore Lightroom Classic XMP metadata sidecars from a backup snapshot safely."
    )
    parser.add_argument(
        "source", help="Source directory (older backup snapshot with complete metadata)"
    )
    parser.add_argument(
        "destination",
        help="Destination directory (working folder needing metadata sync)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes to the destination files. By default, the script runs in dry-run mode.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable detailed logging output."
    )

    args = parser.parse_args()

    print("=" * 70)
    print("Lightroom Classic Ratings Metadata Sync Utility")
    print("=" * 70)
    print(f"Source Directory:      {args.source}")
    print(f"Destination Directory: {args.destination}")
    print(
        f"Execution Mode:        {'APPLY (MODIFIES FILES)' if args.apply else 'DRY-RUN (NO CHANGES)'}"
    )
    print("=" * 70)

    dry_run = not args.apply
    stats = sync_ratings(
        args.source, args.destination, dry_run=dry_run, verbose=args.verbose
    )

    print("=" * 70)
    print("Execution Summary:")
    print("=" * 70)
    print(f"Total RAW files scanned:             {stats['total_scanned_raws']}")
    print(
        f"Destination RAWs with rated XMPs:     {stats['existing_rated_xmps']} (skipped)"
    )
    print(
        f"Destination RAWs with unrated XMPs:   {stats['existing_unrated_xmps']} (eligible for overwrite)"
    )
    print(
        f"Destination RAWs with no XMPs:        {stats['no_xmps']} (eligible for copy)"
    )
    print(f"Source XMPs successfully found/copied: {stats['copied']}")
    print(
        f"Missing XMPs in source directory:     {stats['skipped_not_found_in_source']}"
    )
    print(f"Errors encountered:                   {stats['errors']}")
    print("=" * 70)

    if dry_run and stats["copied"] > 0:
        print("Note: Run with '--apply' to actually execute the copy operations.")
        print("=" * 70)


if __name__ == "__main__":
    main()
