import sys
import os
import unittest

import numpy as np

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from services.training import normalize_develop_settings_for_style


class TestNormalizeDevelopSettingsForStyle(unittest.TestCase):
    def test_white_balance_mode_is_preserved(self):
        canonical = normalize_develop_settings_for_style(
            {
                "WhiteBalance": "Custom",
                "Temperature": 6125,
                "Tint": 14,
            }
        )

        self.assertEqual(canonical["white_balance"], "Custom")
        self.assertEqual(canonical["temperature"], 6125.0)
        self.assertEqual(canonical["tint"], 14.0)

    def test_partial_hsl_preserved(self):
        # Simulate Lightroom exporting only a Red hue adjustment
        raw_settings = {"HueAdjustmentRed": 15.0}
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("hsl", canonical)
        self.assertIn("red", canonical["hsl"])
        self.assertEqual(canonical["hsl"]["red"]["hue"], 15.0)
        self.assertEqual(canonical["hsl"]["red"]["saturation"], 0.0)
        self.assertEqual(canonical["hsl"]["red"]["luminance"], 0.0)
        # Check that other colors were not added or are not required for red to be present
        self.assertEqual(len(canonical["hsl"]), 1)

    def test_partial_color_grading_preserved(self):
        # Simulate Lightroom exporting only Shadows color grading
        raw_settings = {
            "ColorGradeShadowsHue": 210.0,
            "ColorGradeShadowsSat": 25.0,
        }
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("color_grading", canonical)
        self.assertIn("shadows", canonical["color_grading"])
        self.assertEqual(canonical["color_grading"]["shadows"]["hue"], 210.0)
        self.assertEqual(canonical["color_grading"]["shadows"]["saturation"], 25.0)
        self.assertEqual(canonical["color_grading"]["blending"], 50.0)

    def test_color_grading_zero_values_do_not_fall_back_to_legacy_values(self):
        canonical = normalize_develop_settings_for_style(
            {
                "ColorGradeShadowsHue": 0,
                "ColorGradeShadowsSat": 0,
                "ColorGradeShadowLum": 0,
                "ColorGradeGlobalLum": 0,
                "ColorGradeBlending": 0,
                "ColorGradeBalance": 0,
                "SplitToningShadowsHue": 45,
                "SplitToningShadowsSaturation": 70,
                "SplitToningBalance": 80,
            }
        )

        self.assertEqual(canonical["color_grading"]["shadows"]["hue"], 0.0)
        self.assertEqual(canonical["color_grading"]["shadows"]["saturation"], 0.0)
        self.assertEqual(canonical["color_grading"]["shadows"]["luminance"], 0.0)
        self.assertEqual(canonical["color_grading"]["global"]["luminance"], 0.0)
        self.assertEqual(canonical["color_grading"]["blending"], 0.0)
        self.assertEqual(canonical["color_grading"]["balance"], 0.0)

    def test_color_grading_uses_lightroom_singular_keys(self):
        canonical = normalize_develop_settings_for_style(
            {
                "ColorGradeShadowHue": 215,
                "ColorGradeShadowSat": 18,
                "ColorGradeHighlightHue": 42,
                "ColorGradeHighlightSat": 9,
                "SplitToningBalance": -15,
            }
        )

        grading = canonical["color_grading"]
        self.assertEqual(grading["shadows"]["hue"], 215.0)
        self.assertEqual(grading["highlights"]["hue"], 42.0)
        self.assertEqual(grading["blending"], 50.0)
        self.assertEqual(grading["balance"], -15.0)

    def test_partial_tone_curve_preserved(self):
        # Simulate only Master tone curve exported
        raw_settings = {"ToneCurvePV2012": [0.0, 0.0, 128.0, 140.0, 255.0, 255.0]}
        canonical = normalize_develop_settings_for_style(raw_settings)
        self.assertIn("tone_curve", canonical)
        self.assertIn("point_curve", canonical["tone_curve"])
        self.assertIn("master", canonical["tone_curve"]["point_curve"])
        self.assertEqual(
            canonical["tone_curve"]["point_curve"]["master"],
            [0.0, 0.0, 128.0, 140.0, 255.0, 255.0],
        )

    def test_generated_index_keywords_are_not_promoted_to_user_keywords(self):
        from unittest.mock import MagicMock, patch
        from services import source_embeddings, training

        training_collection = MagicMock()
        training_collection.get.return_value = {"ids": []}
        main_collection = MagicMock()
        main_collection.get.return_value = {
            "metadatas": [
                {
                    "filename": "example.nef",
                    "keywords": '["macro", "wildlife"]',
                    "flattened_keywords": "macro, wildlife",
                }
            ]
        }

        with (
            patch.object(training, "_training_collection", training_collection),
            patch("services.chroma._ensure_initialized"),
            patch("services.chroma.collection", main_collection),
        ):
            training.add_training_example(
                photo_id="photo-1",
                develop_settings={},
                embedding=[0.1] * training.EMBEDDING_DIM,
                filename=None,
                user_keywords=None,
                focal_length=105.0,
                skip_discovery=True,
                source_provenance="raw_preview",
                source_stamp={
                    "source_embedding_provenance": "raw_preview",
                    "source_embedding_fingerprint": "fingerprint-photo-1",
                    "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
                    "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
                    "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
                },
            )

        stored = training_collection.add.call_args.kwargs["metadatas"][0]
        self.assertEqual(stored["filename"], "example.nef")
        self.assertEqual(stored["focal_length"], 105.0)
        self.assertNotIn("user_keywords", stored)

    def test_training_enrichment_only_backfills_missing_identity_and_exif(self):
        from unittest.mock import MagicMock, patch
        from services import training

        training_collection = MagicMock()
        main_collection = MagicMock()
        main_collection.get.return_value = {
            "ids": ["photo-1"],
            "metadatas": [
                {
                    "filename": "example.nef",
                    "lens": "index lens",
                    "keywords": '["macro"]',
                    "flattened_keywords": "macro",
                    "caption": "generated caption",
                }
            ],
        }
        metadatas = [
            {
                "lens": "training lens",
                "content_tags": '["family"]',
            }
        ]

        with (
            patch.object(training, "_training_collection", training_collection),
            patch("services.chroma._ensure_initialized"),
            patch("services.chroma.collection", main_collection),
        ):
            training._enrich_and_sync_metadatas_from_main_index(["photo-1"], metadatas)

        self.assertEqual(metadatas[0]["filename"], "example.nef")
        self.assertEqual(metadatas[0]["lens"], "training lens")
        self.assertEqual(metadatas[0]["content_tags"], '["family"]')
        self.assertNotIn("keywords", metadatas[0])
        self.assertNotIn("caption", metadatas[0])


def make_dummy_jpeg(width=100, height=100, color=(128, 128, 128)) -> bytes:
    import io
    from PIL import Image

    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestExposureMetrics(unittest.TestCase):
    def test_compute_exposure_metrics_dark_vs_bright(self):
        from services.training import compute_exposure_metrics

        dark_bytes = make_dummy_jpeg(100, 100, color=(15, 15, 15))
        bright_bytes = make_dummy_jpeg(100, 100, color=(245, 245, 245))

        dark_metrics = compute_exposure_metrics(dark_bytes)
        bright_metrics = compute_exposure_metrics(bright_bytes)

        self.assertIn("exp_luminance_mean", dark_metrics)
        self.assertLess(
            dark_metrics["exp_luminance_mean"], bright_metrics["exp_luminance_mean"]
        )
        self.assertGreater(dark_metrics["zone_deep_shadows"], 0.5)
        self.assertGreater(bright_metrics["zone_bright_highlights"], 0.5)

    def test_compute_exposure_metrics_invalid_bytes_fallback(self):
        from services.training import compute_exposure_metrics

        metrics = compute_exposure_metrics(b"invalid image bytes")
        self.assertEqual(metrics["exp_luminance_mean"], 0.5)
        self.assertEqual(metrics["exp_contrast"], 0.0)


class TestTrainingReadiness(unittest.TestCase):
    @staticmethod
    def _eligible_metadata(profile="Adobe Color"):
        import json
        from services import source_embeddings

        return {
            "has_embedding": True,
            "source_provenance": "raw_preview",
            "source_embedding_provenance": "raw_preview",
            "source_embedding_fingerprint": "fingerprint",
            "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
            "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
            "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
            "camera_profile": profile,
            "canonical_settings": json.dumps({"exposure": 0.25}),
        }

    def _stats(self, metadatas, embeddings, *, active_generation_id=None):
        from unittest.mock import MagicMock, patch
        from services import training

        collection = MagicMock()
        collection.count.return_value = len(metadatas)
        page = {
            "ids": [f"photo-{index}" for index in range(len(metadatas))],
            "metadatas": metadatas,
            "embeddings": embeddings,
        }
        with (
            patch.object(training, "_training_collection", collection),
            patch.object(training, "_ensure_initialized"),
            patch.object(training, "_iter_training_pages", return_value=iter([page])),
            patch(
                "services.policy_runtime.list_active_policies",
                return_value=[],
            ),
            patch(
                "services.policy_runtime._load_active_artifacts",
                return_value=(
                    {"partition": MagicMock(generation_id=active_generation_id)}
                    if active_generation_id
                    else {}
                ),
            ),
        ):
            return training.get_training_stats()

    def test_excluded_only_catalog_is_not_ready(self):
        metadata = self._eligible_metadata()
        metadata["source_provenance"] = "lightroom_rendered_preview"

        stats = self._stats([metadata], [[1.0, 0.0]])

        self.assertEqual(stats["eligible_count"], 0)
        self.assertEqual(stats["exclusions"], {"source_not_neutral": 1})
        self.assertEqual(stats["readiness"], "cold_start")
        self.assertFalse(stats["has_enough_examples"])

    def test_partition_minimum_is_not_satisfied_by_split_total(self):
        metadatas = [self._eligible_metadata("Adobe Color") for _ in range(6)]
        metadatas += [self._eligible_metadata("Camera Standard") for _ in range(6)]

        stats = self._stats(metadatas, [[1.0, 0.0]] * 12)

        self.assertEqual(stats["eligible_count"], 12)
        self.assertEqual(sorted(stats["eligible_partitions"].values()), [6, 6])
        self.assertEqual(stats["readiness"], "collecting")
        self.assertFalse(stats["has_enough_examples"])

    def test_missing_source_contract_stamp_is_excluded(self):
        metadata = self._eligible_metadata()
        metadata.pop("source_embedding_schema")

        stats = self._stats([metadata], [[1.0, 0.0]])

        self.assertEqual(stats["eligible_count"], 0)
        self.assertEqual(stats["exclusions"], {"stale_source_stamp": 1})

    def test_trainable_partition_and_active_generation_have_distinct_states(self):
        metadatas = [self._eligible_metadata() for _ in range(12)]
        embeddings = [[1.0, 0.0]] * 12

        ready = self._stats(metadatas, embeddings)
        active = self._stats(
            metadatas,
            embeddings,
            active_generation_id="generation-1",
        )

        self.assertEqual(ready["readiness"], "ready_to_rebuild")
        self.assertEqual(ready["next_action"], "rebuild")
        self.assertEqual(active["readiness"], "active")
        self.assertEqual(active["active_generation_id"], "generation-1")


class TestFocalLengthAndTODBuckets(unittest.TestCase):
    def test_focal_length_bucket_boundaries(self):
        from services.training import focal_length_bucket

        self.assertEqual(focal_length_bucket(None), "unknown")
        self.assertEqual(focal_length_bucket(16.0), "ultra_wide")
        self.assertEqual(focal_length_bucket(24.0), "wide")
        self.assertEqual(focal_length_bucket(50.0), "normal")
        self.assertEqual(focal_length_bucket(85.0), "short_tele")
        self.assertEqual(focal_length_bucket(200.0), "tele")
        self.assertEqual(focal_length_bucket(400.0), "super_tele")

    def test_time_of_day_bucket_local_hours(self):
        from datetime import datetime
        from services.training import time_of_day_bucket

        self.assertEqual(time_of_day_bucket(None), "unknown")

        # Pick specific timestamps and check against their local hour bucket mapping
        ts = 1700000000.0
        bucket = time_of_day_bucket(ts)
        local_hour = datetime.fromtimestamp(ts).hour
        if 5 <= local_hour < 8:
            expected = "dawn"
        elif 8 <= local_hour < 12:
            expected = "morning"
        elif 12 <= local_hour < 17:
            expected = "afternoon"
        elif 17 <= local_hour < 20:
            expected = "evening"
        else:
            expected = "night"
        self.assertEqual(bucket, expected)


class TestBurstClusteringHeroSelection(unittest.TestCase):
    def test_burst_clustering_selects_hero(self):
        from services.policy_runtime import _curate_bursts

        emb = [1.0] + [0.0] * 1151
        examples = [
            {
                "photo_id": f"photo-{index}",
                "normalized_embedding": np.asarray(emb),
                "metadata": {
                    "capture_time": 1000.0 + index * 2,
                    "rating": rating,
                    "pick_status": int(rating == 5),
                },
                "flat_target": {"exposure": index / 10},
            }
            for index, rating in enumerate((3, 5, 2))
        ]

        curated, weights = _curate_bursts(examples)
        self.assertEqual(len(curated), 1)
        self.assertEqual(curated[0]["metadata"]["rating"], 5)
        self.assertEqual(weights[0], 1.0 / 3.0)


class TestTrainingEvidenceFeatures(unittest.TestCase):
    def test_preflight_reexports_stale_training_evidence(self):
        from unittest.mock import MagicMock, patch
        from services import source_embeddings, training

        current = {
            "has_embedding": True,
            "source_provenance": "raw_preview",
            "source_embedding_provenance": "raw_preview",
            "source_embedding_fingerprint": "current",
            "source_embedding_schema": source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
            "source_embedding_model": source_embeddings.SOURCE_EMBEDDING_MODEL_ID,
            "source_embedding_preprocess": source_embeddings.SOURCE_EMBEDDING_PREPROCESS_VERSION,
        }
        stale = dict(current, source_embedding_schema="neutral-source-old")
        collection = MagicMock()
        collection.get.return_value = {
            "ids": ["current", "stale"],
            "metadatas": [current, stale],
        }
        with (
            patch.object(training, "_training_collection", collection),
            patch.object(training, "_ensure_initialized"),
        ):
            existing = training.get_existing_training_ids(["current", "stale"])

        self.assertEqual(existing, {"current"})
        collection.get.assert_called_once_with(
            ids=["current", "stale"],
            include=["metadatas"],
        )

    def test_list_training_examples_preserves_metadata(self):
        from unittest.mock import MagicMock, patch
        from services.training import list_training_examples

        mock_coll = MagicMock()
        mock_coll.get.return_value = {
            "ids": ["p_ex1"],
            "metadatas": [
                {
                    "uuid": "lr-uuid-1",
                    "filename": "pano.dng",
                    "width": 6000,
                    "height": 2000,
                    "focal_length": 105.0,
                    "shutter_speed": 0.01,
                    "iso": 100,
                    "rating": 5,
                    "content_tags": ["family"],
                }
            ],
        }
        with patch("services.training._ensure_initialized"):
            with patch("services.training._training_collection", mock_coll):
                with patch(
                    "services.training._enrich_and_sync_metadatas_from_main_index"
                ):
                    examples = list_training_examples()
                    self.assertEqual(len(examples), 1)
                    ex = examples[0]
                    self.assertEqual(ex["width"], 6000)
                    self.assertEqual(ex["height"], 2000)
                    self.assertEqual(ex["focal_length"], 105.0)
                    self.assertEqual(ex["rating"], 5)

    def test_policy_training_reader_accepts_chroma_numpy_embeddings(self):
        from unittest.mock import MagicMock, patch
        from services import training

        embedding_rows = np.asarray(
            [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            dtype=np.float32,
        )
        page = {
            "ids": ["photo-1", "photo-2"],
            "metadatas": [{"filename": "one.dng"}, {"filename": "two.dng"}],
            "embeddings": embedding_rows,
        }

        with (
            patch.object(training, "_ensure_initialized"),
            patch.object(training, "_training_collection", MagicMock()),
            patch.object(training, "_iter_training_pages", return_value=[page]),
            patch.object(training, "_enrich_and_sync_metadatas_from_main_index"),
        ):
            examples = training.list_training_examples_with_embeddings()

        self.assertEqual(
            [item["photo_id"] for item in examples], ["photo-1", "photo-2"]
        )
        self.assertEqual(examples[0]["embedding"].dtype, np.float32)
        self.assertEqual(examples[1]["embedding"].dtype, np.float32)
        np.testing.assert_allclose(examples[0]["embedding"], embedding_rows[0])
        np.testing.assert_allclose(examples[1]["embedding"], embedding_rows[1])


if __name__ == "__main__":
    unittest.main()
