"""Unit tests for services/index.py.

Covers thumbnail downscaling and memory efficiency, EXIF location extraction fallback,
batch processing flow and cancellation handling, and keyword flattening.
"""

import io
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch
import numpy as np
from PIL import Image

from services import index as index_service
from services import source_embeddings
from services.index import (
    _decode_worker_count,
    _flatten_keywords,
    _load_analysis_grayscale,
    _decode_image,
    process_image_task,
)


def make_dummy_jpeg(width=1500, height=1200, color=(100, 150, 200)) -> bytes:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


class TestFlattenKeywords(unittest.TestCase):
    def test_flatten_string(self):
        self.assertEqual(_flatten_keywords("Sunset, Beach"), "Sunset, Beach")

    def test_flatten_flat_list(self):
        res = _flatten_keywords(["Sunset", "Beach", "sunset"])
        self.assertEqual(res, "Sunset, Beach")

    def test_flatten_nested_dict(self):
        kw_dict = {
            "name": "Nature",
            "synonyms": ["Wild", "Outdoors"],
            "subcategories": [{"name": "Forest", "aliases": ["Woods"]}],
        }
        res = _flatten_keywords(kw_dict)
        self.assertIn("Nature", res)
        self.assertIn("Wild", res)
        self.assertIn("Outdoors", res)
        self.assertIn("Forest", res)
        self.assertIn("Woods", res)

    def test_flatten_empty_or_none(self):
        self.assertEqual(_flatten_keywords(None), "")
        self.assertEqual(_flatten_keywords([]), "")
        self.assertEqual(_flatten_keywords({}), "")


class TestImageDecodingAndGrayscale(unittest.TestCase):
    @patch("services.index.config.STYLEAI_HTTP_THREADS", 12)
    @patch("services.index.config.STYLEAI_GPU_BATCH_SIZE", 12)
    def test_decode_workers_follow_ingestion_budget(self):
        self.assertEqual(_decode_worker_count(1, cpu_capacity=12), 1)
        self.assertEqual(_decode_worker_count(8, cpu_capacity=12), 8)
        self.assertEqual(_decode_worker_count(48, cpu_capacity=12), 12)
        self.assertEqual(_decode_worker_count(48, cpu_capacity=3), 3)

    @patch("services.index.gc.collect")
    @patch("services.index.monotonic_time.monotonic")
    def test_forced_gc_is_rate_limited(self, mock_monotonic, mock_collect):
        previous = index_service._last_forced_gc_at
        try:
            index_service._last_forced_gc_at = 0.0
            mock_monotonic.side_effect = [100.0, 110.0, 131.0]
            self.assertTrue(index_service._maybe_collect_garbage())
            self.assertFalse(index_service._maybe_collect_garbage())
            self.assertTrue(index_service._maybe_collect_garbage())
            self.assertEqual(mock_collect.call_count, 2)
        finally:
            index_service._last_forced_gc_at = previous

    def test_load_analysis_grayscale_downscales(self):
        image_bytes = make_dummy_jpeg(1600, 1200)
        gray = _load_analysis_grayscale(image_bytes, max_side=512)
        self.assertIsInstance(gray, np.ndarray)
        self.assertLessEqual(max(gray.shape), 512)
        self.assertTrue(np.all(gray >= 0.0) and np.all(gray <= 1.0))

    def test_decode_image_downscales_large_image(self):
        image_bytes = make_dummy_jpeg(2000, 1500)
        img = _decode_image(image_bytes)
        self.assertIsNotNone(img)
        self.assertLessEqual(max(img.size), 1024)
        self.assertEqual(img.mode, "RGB")

    def test_decode_image_invalid_bytes(self):
        img = _decode_image(b"not an image")
        self.assertIsNone(img)


class TestProcessImageTask(unittest.TestCase):
    @patch("server_lifecycle.GLOBAL_CANCEL_EVENT")
    def test_batch_canceled_by_watchdog(self, mock_cancel_event):
        mock_cancel_event.is_set.return_value = True
        triplets = [(b"data", "uuid-1", "test.jpg", "lr-1")]
        options = {"provider": "ollama", "model": "qwen3-vl"}
        item_results = []
        success, failure, errors, warnings = process_image_task(
            triplets, options, item_results=item_results
        )
        self.assertEqual(success, 0)
        self.assertEqual(failure, 1)
        self.assertIn("Batch canceled by watchdog.", errors)
        self.assertEqual(
            item_results,
            [
                {
                    "photo_id": "uuid-1",
                    "filename": "test.jpg",
                    "status": "canceled",
                    "error": "Batch canceled by watchdog.",
                }
            ],
        )

    @patch("server_lifecycle.unload_model")
    @patch("services.index.get_analysis_service")
    @patch("services.index.chroma_service")
    @patch("services.index.exif_service")
    @patch("server_lifecycle.GLOBAL_CANCEL_EVENT")
    def test_provider_cancellation_is_a_canceled_result_not_a_batch_error(
        self,
        mock_cancel_event,
        mock_exif,
        mock_chroma,
        mock_get_analysis_service,
        _mock_unload_model,
    ):
        mock_cancel_event.is_set.return_value = False
        mock_exif.extract_location_tags.return_value = None
        mock_chroma.collection.get.return_value = {"ids": []}
        mock_get_analysis_service.return_value.analyze_batch.side_effect = (
            InterruptedError("operation job has been canceled")
        )
        item_results = []

        success, failure, errors, warnings = process_image_task(
            [(make_dummy_jpeg(100, 100), "uuid-1", "test.jpg", "lr-1")],
            {
                "regenerate_metadata": True,
                "compute_embeddings": False,
                "compute_metadata": True,
            },
            item_results=item_results,
        )

        self.assertEqual((success, failure, errors, warnings), (0, 0, [], []))
        self.assertEqual(item_results[0]["status"], "canceled")
        self.assertEqual(item_results[0]["error"], "operation job has been canceled")
        _mock_unload_model.assert_not_called()

    @patch("server_lifecycle.GLOBAL_CANCEL_EVENT")
    @patch("services.index.chroma_service")
    @patch("services.index.exif_service")
    def test_exif_location_fallback_on_exception(
        self, mock_exif, mock_chroma, mock_cancel_event
    ):
        mock_cancel_event.is_set.return_value = False
        mock_exif.extract_location_tags.side_effect = Exception("EXIF parse failure")
        mock_chroma.collection.get.return_value = {"ids": []}

        image_bytes = make_dummy_jpeg(200, 200)
        triplets = [(image_bytes, "uuid-1", "test.jpg", "lr-1")]
        # Embeddings are disabled, so no vision model is needed.
        options = {
            "regenerate_metadata": True,
            "compute_embeddings": False,
            "compute_metadata": False,
        }

        success, failure, errors, warnings = process_image_task(triplets, options)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 0)
        self.assertEqual(errors, [])

    @patch("server_lifecycle.GLOBAL_CANCEL_EVENT")
    @patch("services.index.chroma_service")
    @patch("services.index.exif_service")
    @patch("server_lifecycle.get_model")
    @patch("server_lifecycle.get_processor")
    def test_process_image_task_embedding_flow(
        self,
        mock_get_processor,
        mock_get_model,
        mock_exif,
        mock_chroma,
        mock_cancel_event,
    ):
        mock_cancel_event.is_set.return_value = False
        mock_exif.extract_location_tags.return_value = {
            "latitude": 40.0,
            "longitude": -74.0,
        }
        mock_chroma.collection.get.return_value = {"ids": []}
        mock_chroma.get_image.return_value = None

        # Mock SigLIP2 processor and model
        mock_model = MagicMock()
        mock_processor = MagicMock()
        import torch

        mock_processor.return_value = torch.zeros((3, 384, 384), dtype=torch.float32)
        mock_emb = torch.zeros((1, 1152), dtype=torch.float32)
        mock_model.encode_image.return_value = mock_emb

        mock_get_model.return_value = mock_model
        mock_get_processor.return_value = mock_processor

        image_bytes = make_dummy_jpeg(300, 300)
        triplets = [(image_bytes, "uuid-1", "test.jpg", "lr-1")]
        options = {
            "regenerate_metadata": True,
            "compute_embeddings": True,
            "compute_metadata": False,
        }

        success, failure, errors, warnings = process_image_task(triplets, options)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 0)
        self.assertEqual(errors, [])
        mock_chroma.add_image.assert_called_once()
        mock_exif.extract_location_tags.assert_not_called()
        stored_metadata = mock_chroma.add_image.call_args.args[2]
        self.assertEqual(
            stored_metadata["source_embedding_provenance"],
            source_embeddings.RENDERED_PREVIEW_PROVENANCE,
        )
        self.assertEqual(
            stored_metadata["source_embedding_schema"],
            source_embeddings.SOURCE_EMBEDDING_SCHEMA_VERSION,
        )
        self.assertTrue(
            all(key in stored_metadata for key in source_embeddings.SOURCE_METRIC_KEYS)
        )

    @patch("services.index.chroma_service")
    def test_process_image_task_string_booleans(self, mock_chroma):
        # When regenerate_metadata is passed as string 'false', it should not regenerate existing images
        metadata = source_embeddings.stamp_metadata(
            {"has_embedding": True, "title": "Existing Title"},
            source_embeddings.NeutralSource(
                image_bytes=b"unused",
                provenance=source_embeddings.RENDERED_PREVIEW_PROVENANCE,
                fingerprint="existing-fingerprint",
            ),
        )
        mock_chroma.collection.get.return_value = {
            "ids": ["uuid-1"],
            "metadatas": [metadata],
        }
        image_bytes = make_dummy_jpeg(300, 300)
        triplets = [(image_bytes, "uuid-1", "test.jpg", "lr-1")]
        options = {
            "regenerate_metadata": "false",
            "compute_embeddings": "true",
            "compute_metadata": "false",
        }
        success, failure, errors, warnings = process_image_task(triplets, options)
        self.assertEqual(success, 1)
        self.assertEqual(failure, 0)
        # Should not call add_image because regenerate_metadata='false' and image already has embedding
        mock_chroma.add_image.assert_not_called()


class TestDynamicGpuBatch(unittest.TestCase):
    @patch("services.index._maybe_collect_garbage")
    @patch("services.index.index_queue.task_done")
    @patch("services.operations.admission.acquire", return_value=nullcontext())
    @patch("services.operations.set_item_states")
    @patch("services.operations.is_cancel_requested", return_value=False)
    @patch("services.index.process_image_task")
    @patch("services.index.config.DB_PATH", "/tmp/styleai-operation-test")
    def test_operation_states_are_published_once_per_bounded_batch(
        self,
        mock_process,
        _mock_cancel,
        mock_set_states,
        _mock_acquire,
        mock_task_done,
        _mock_collect,
    ):
        def process(_triplets, _options, *, item_results):
            item_results.extend(
                [
                    {"photo_id": "p1", "status": "succeeded"},
                    {"photo_id": "p2", "status": "succeeded"},
                ]
            )
            return 2, 0, [], []

        mock_process.side_effect = process
        batch = [
            {
                "uuid": photo_id,
                "image_bytes": b"image",
                "filename": f"{photo_id}.jpg",
                "lr_uuid": None,
                "options": {},
                "job_id": "job-1",
            }
            for photo_id in ("p1", "p2")
        ]

        index_service._process_dynamic_gpu_batch(batch)

        self.assertEqual(mock_set_states.call_count, 2)
        self.assertEqual(
            [update["state"] for update in mock_set_states.call_args_list[0].args[2]],
            ["running", "running"],
        )
        self.assertEqual(
            [update["state"] for update in mock_set_states.call_args_list[1].args[2]],
            ["succeeded", "succeeded"],
        )
        self.assertEqual(mock_task_done.call_count, 2)


if __name__ == "__main__":
    unittest.main()
