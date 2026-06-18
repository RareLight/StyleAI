import os
import tempfile
import sys

# Add the root directory to path to import ratings_sync
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from scripts.ratings_sync import extract_rating_from_xmp, has_valid_rating, sync_ratings

# Sample XMP content for testing
XMP_WITH_RATING_ATTR = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmp:Rating="3">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""

XMP_WITH_RATING_TAG = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <xmp:Rating>5</xmp:Rating>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""

XMP_WITH_ZERO_RATING = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmp:Rating="0">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""

XMP_NO_RATING = """<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/">
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>"""

XMP_MALFORMED_XML_BUT_VALID_REGEX = """<x:xmpmeta>
 <rdf:RDF>
  <rdf:Description xmp:Rating="4">
  <!-- Malformed closing tag -->
 </rdf:RDF>
</x:xmpmeta>"""


def write_temp_file(dir_path: str, filename: str, content: str) -> str:
    path = os.path.join(dir_path, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_extract_rating():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test attribute-style rating
        f1 = write_temp_file(tmpdir, "f1.xmp", XMP_WITH_RATING_ATTR)
        assert extract_rating_from_xmp(f1) == 3
        assert has_valid_rating(f1) is True

        # Test tag-style rating
        f2 = write_temp_file(tmpdir, "f2.xmp", XMP_WITH_RATING_TAG)
        assert extract_rating_from_xmp(f2) == 5
        assert has_valid_rating(f2) is True

        # Test zero rating
        f3 = write_temp_file(tmpdir, "f3.xmp", XMP_WITH_ZERO_RATING)
        assert extract_rating_from_xmp(f3) == 0
        assert has_valid_rating(f3) is False

        # Test no rating
        f4 = write_temp_file(tmpdir, "f4.xmp", XMP_NO_RATING)
        assert extract_rating_from_xmp(f4) is None
        assert has_valid_rating(f4) is False

        # Test malformed XML with regex fallback
        f5 = write_temp_file(tmpdir, "f5.xmp", XMP_MALFORMED_XML_BUT_VALID_REGEX)
        assert extract_rating_from_xmp(f5) == 4
        assert has_valid_rating(f5) is True


def test_sync_ratings_dry_run_vs_apply():
    with (
        tempfile.TemporaryDirectory() as src_dir,
        tempfile.TemporaryDirectory() as dst_dir,
    ):
        # Create directories and files
        # RAW file list in dest
        # 1. photo1.NEF - lacks XMP in dest. Has XMP with 4 stars in src.
        # 2. photo2.ARW - has unrated XMP (0 stars) in dest. Has XMP with 5 stars in src.
        # 3. photo3.dng - has rated XMP (3 stars) in dest. Has XMP with 1 star in src (should NOT be overwritten).
        # 4. photo4.CR2 - lacks XMP in dest. Lacks XMP in src as well.

        # Setup source
        write_temp_file(src_dir, "photo1.NEF", "")
        write_temp_file(src_dir, "photo1.xmp", XMP_WITH_RATING_ATTR)  # 3 stars

        write_temp_file(src_dir, "photo2.ARW", "")
        write_temp_file(src_dir, "photo2.xmp", XMP_WITH_RATING_TAG)  # 5 stars

        write_temp_file(src_dir, "photo3.dng", "")
        write_temp_file(src_dir, "photo3.xmp", XMP_WITH_RATING_TAG)  # 5 stars

        # Setup destination
        write_temp_file(dst_dir, "photo1.NEF", "")
        # No XMP for photo1

        write_temp_file(dst_dir, "photo2.ARW", "")
        write_temp_file(dst_dir, "photo2.xmp", XMP_WITH_ZERO_RATING)  # 0 stars

        write_temp_file(dst_dir, "photo3.dng", "")
        write_temp_file(dst_dir, "photo3.xmp", XMP_WITH_RATING_ATTR)  # 3 stars

        write_temp_file(dst_dir, "photo4.CR2", "")
        # No XMP in dest, no XMP in src

        # Run Dry-Run first
        stats_dry = sync_ratings(src_dir, dst_dir, dry_run=True, verbose=True)

        # Verify stats
        assert stats_dry["total_scanned_raws"] == 4
        assert stats_dry["existing_rated_xmps"] == 1  # photo3
        assert stats_dry["existing_unrated_xmps"] == 1  # photo2
        assert stats_dry["no_xmps"] == 2  # photo1, photo4
        assert stats_dry["copied"] == 2  # photo1 (copied), photo2 (overwritten)
        assert stats_dry["skipped_not_found_in_source"] == 1  # photo4

        # Ensure no files were actually modified/created in dest during dry-run
        assert not os.path.exists(os.path.join(dst_dir, "photo1.xmp"))
        assert extract_rating_from_xmp(os.path.join(dst_dir, "photo2.xmp")) == 0

        # Run Apply
        stats_apply = sync_ratings(src_dir, dst_dir, dry_run=False, verbose=True)

        assert stats_apply["copied"] == 2

        # Verify changes in destination
        # photo1.xmp should now exist and have 3 stars
        dst_p1_xmp = os.path.join(dst_dir, "photo1.xmp")
        assert os.path.exists(dst_p1_xmp)
        assert extract_rating_from_xmp(dst_p1_xmp) == 3

        # photo2.xmp should have been overwritten and have 5 stars
        dst_p2_xmp = os.path.join(dst_dir, "photo2.xmp")
        assert extract_rating_from_xmp(dst_p2_xmp) == 5

        # photo3.xmp should still have 3 stars (not overwritten by 5 stars)
        dst_p3_xmp = os.path.join(dst_dir, "photo3.xmp")
        assert extract_rating_from_xmp(dst_p3_xmp) == 3


def test_sync_ratings_style_b_naming():
    with (
        tempfile.TemporaryDirectory() as src_dir,
        tempfile.TemporaryDirectory() as dst_dir,
    ):
        # Style B naming: photo.NEF.xmp
        write_temp_file(src_dir, "photo1.NEF", "")
        write_temp_file(src_dir, "photo1.NEF.xmp", XMP_WITH_RATING_ATTR)  # 3 stars

        write_temp_file(dst_dir, "photo1.NEF", "")

        # Sync
        stats = sync_ratings(src_dir, dst_dir, dry_run=False, verbose=True)
        assert stats["copied"] == 1

        dst_p1_xmp = os.path.join(dst_dir, "photo1.NEF.xmp")
        assert os.path.exists(dst_p1_xmp)
        assert extract_rating_from_xmp(dst_p1_xmp) == 3
