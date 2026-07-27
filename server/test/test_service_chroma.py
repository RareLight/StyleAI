import sys
import os
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

import services.chroma as chroma_service
from services.chroma import (
    _normalize_photo_id,
    _ensure_photo_metadata,
    _cosine_distance,
    _first_result_item,
)


class TestChromaHelpers(unittest.TestCase):
    def test_normalize_photo_id(self):
        self.assertEqual(_normalize_photo_id("photo-1"), "photo-1")
        self.assertEqual(_normalize_photo_id(None, "uuid-1"), "uuid-1")
        self.assertIsNone(_normalize_photo_id(None, None))

    def test_ensure_photo_metadata(self):
        meta = _ensure_photo_metadata("photo-1", {"rating": 5})
        self.assertEqual(meta["photo_id"], "photo-1")
        self.assertEqual(meta["uuid"], "photo-1")
        self.assertEqual(meta["rating"], 5)

    def test_first_result_item(self):
        self.assertIsNone(_first_result_item(None))
        self.assertEqual(_first_result_item([], default="d"), "d")
        self.assertEqual(_first_result_item([10, 20]), 10)
        self.assertEqual(_first_result_item(np.array([5, 10])), 5)

    def test_cosine_distance(self):
        emb_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        emb_b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        emb_c = np.array([0.0, 1.0, 0.0], dtype=np.float32)

        dist_same = _cosine_distance(emb_a, emb_b)
        dist_ortho = _cosine_distance(emb_a, emb_c)

        self.assertAlmostEqual(dist_same, 0.0, places=5)
        self.assertAlmostEqual(dist_ortho, 1.0, places=5)
        self.assertIsNone(_cosine_distance(None, emb_a))


class TestChromaCollectionIsolation(unittest.TestCase):
    """Verify strict isolation between search image collection and training examples collection."""

    def test_collection_name_separation(self):
        import chromadb

        client = chromadb.EphemeralClient()
        search_collection = client.get_or_create_collection(name="image_embeddings")
        training_collection = client.get_or_create_collection(name="training_examples")

        self.assertNotEqual(search_collection.name, training_collection.name)

        # Add item to search_collection only
        search_collection.add(
            ids=["photo-1"],
            metadatas=[{"photo_id": "photo-1"}],
            embeddings=[[0.1] * 1152],
        )

        self.assertEqual(search_collection.count(), 1)
        self.assertEqual(training_collection.count(), 0)


class TestChromaServiceCRUD(unittest.TestCase):
    def setUp(self):
        self.mock_collection = MagicMock()
        # Patch collection in chroma_service
        self.collection_patch = patch.object(
            chroma_service, "collection", self.mock_collection
        )
        self.collection_patch.start()

    def tearDown(self):
        self.collection_patch.stop()

    @patch("services.chroma._ensure_initialized")
    def test_add_image_with_embedding(self, mock_init):
        embedding = [0.1] * 1152
        metadata = {"title": "Test Photo", "rating": 4}
        chroma_service.add_image("photo-100", embedding, metadata)

        self.mock_collection.upsert.assert_called_once()
        args, kwargs = self.mock_collection.upsert.call_args
        self.assertEqual(kwargs["ids"], ["photo-100"])
        self.assertEqual(kwargs["embeddings"], [embedding])
        self.assertEqual(kwargs["metadatas"][0]["title"], "Test Photo")
        self.assertEqual(kwargs["metadatas"][0]["photo_id"], "photo-100")

    @patch("services.chroma._ensure_initialized")
    def test_add_image_metadata_only_uses_dummy_embedding(self, mock_init):
        metadata = {"title": "No Embedding Photo"}
        chroma_service.add_image("photo-101", None, metadata)

        self.mock_collection.upsert.assert_called_once()
        args, kwargs = self.mock_collection.upsert.call_args
        self.assertEqual(kwargs["ids"], ["photo-101"])
        self.assertEqual(len(kwargs["embeddings"][0]), 1152)
        self.assertTrue(all(val == 0.0 for val in kwargs["embeddings"][0]))

    @patch("services.chroma._ensure_initialized")
    def test_get_image(self, mock_init):
        self.mock_collection.get.return_value = {
            "ids": ["photo-100"],
            "metadatas": [{"photo_id": "photo-100", "title": "Test"}],
            "embeddings": [[0.1] * 1152],
        }

        res = chroma_service.get_image("photo-100")
        self.assertEqual(res["ids"], ["photo-100"])
        self.mock_collection.get.assert_called_once_with(
            ids=["photo-100"], include=["metadatas", "embeddings"]
        )

    @patch("services.chroma._ensure_initialized")
    def test_update_image(self, mock_init):
        metadata = {"rating": 5}
        chroma_service.update_image("photo-100", metadata, embedding=[0.2] * 1152)

        self.mock_collection.update.assert_called_once()
        args, kwargs = self.mock_collection.update.call_args
        self.assertEqual(kwargs["ids"], ["photo-100"])
        self.assertEqual(kwargs["embeddings"], [[0.2] * 1152])

    @patch("services.chroma._ensure_initialized")
    def test_delete_image(self, mock_init):
        chroma_service.delete_image("photo-100")
        self.mock_collection.delete.assert_called_once_with(ids=["photo-100"])

    @patch("services.chroma._ensure_initialized")
    def test_clear_image_metadata(self, mock_init):
        self.mock_collection.get.return_value = {
            "ids": ["photo-100"],
            "metadatas": [
                {
                    "photo_id": "photo-100",
                    "rating": 5,
                    "caption": "AI caption to clear",
                    "keywords": "AI, tags",
                }
            ],
            "embeddings": [[0.5] * 1152],
        }

        success = chroma_service.clear_image_metadata("photo-100")
        self.assertTrue(success)
        self.mock_collection.update.assert_called_once()
        args, kwargs = self.mock_collection.update.call_args
        updated_meta = kwargs["metadatas"][0]
        self.assertEqual(updated_meta["rating"], 5)
        self.assertNotIn("caption", updated_meta)
        self.assertNotIn("keywords", updated_meta)

    @patch("services.chroma._ensure_initialized")
    def test_get_image_count(self, mock_init):
        self.mock_collection.count.return_value = 3
        count = chroma_service.get_image_count()
        self.assertEqual(count, 3)
        self.mock_collection.count.assert_called_once()
        self.mock_collection.get.assert_not_called()

    @patch("services.chroma._ensure_initialized")
    def test_get_all_image_ids_filtering(self, mock_init):
        self.mock_collection.get.return_value = {
            "ids": ["p1", "p2"],
            "metadatas": [
                {"has_embedding": True},
                {"has_embedding": False},
            ],
        }

        with_emb = chroma_service.get_all_image_ids(has_embedding=True)
        without_emb = chroma_service.get_all_image_ids(has_embedding=False)

        self.assertEqual(with_emb, ["p1"])
        self.assertEqual(without_emb, ["p2"])

    @patch("services.chroma.COLLECTION_PAGE_SIZE", 2)
    @patch("services.chroma._ensure_initialized")
    def test_get_all_image_ids_paginates(self, mock_init):
        self.mock_collection.get.side_effect = [
            {"ids": ["p1", "p2"]},
            {"ids": ["p3"]},
        ]

        self.assertEqual(chroma_service.get_all_image_ids(), ["p1", "p2", "p3"])
        self.assertEqual(
            [call.kwargs["offset"] for call in self.mock_collection.get.call_args_list],
            [0, 2],
        )

    @patch("services.chroma._ensure_initialized")
    def test_get_image_metadata_stats(self, mock_init):
        self.mock_collection.get.return_value = {
            "ids": ["p1", "p2"],
            "metadatas": [
                {"has_embedding": True, "title": "First", "caption": "Cap"},
                {"has_embedding": False, "keywords": "test keyword"},
            ],
        }

        stats = chroma_service.get_image_metadata_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["with_embedding"], 1)
        self.assertEqual(stats["with_title"], 1)
        self.assertEqual(stats["with_caption"], 1)
        self.assertEqual(stats["with_keywords"], 1)

    @patch("services.chroma._ensure_initialized")
    def test_query_images(self, mock_init):
        self.mock_collection.query.return_value = {
            "ids": [["p1", "p2"]],
            "distances": [[0.1, 0.2]],
            "metadatas": [[{"photo_id": "p1"}, {"photo_id": "p2"}]],
        }

        res = chroma_service.query_images([[0.1] * 1152], n_results=5)
        self.assertEqual(res["ids"][0], ["p1", "p2"])
        self.mock_collection.query.assert_called_once()


def test_ensure_db_path_rejects_cross_catalog_switch(monkeypatch, tmp_path):
    import config

    original_path = str(tmp_path / "catalog-a" / "styleai.db")
    foreign_path = str(tmp_path / "catalog-b" / "styleai.db")
    monkeypatch.setattr(config, "DB_PATH", original_path)
    monkeypatch.setattr(chroma_service, "chroma_client", object())

    with pytest.raises(chroma_service.CatalogOwnershipError):
        chroma_service.ensure_db_path(foreign_path)


if __name__ == "__main__":
    unittest.main()
