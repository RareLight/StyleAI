from services import image_cache


def setup_function():
    image_cache.clear()


def teardown_function():
    image_cache.clear()


def test_store_backpressures_without_evicting_accepted_image(monkeypatch):
    monkeypatch.setattr(image_cache, "_MAX_CACHE_ENTRIES", 1)
    monkeypatch.setattr(image_cache, "_MAX_CACHE_BYTES", 100)

    assert image_cache.store_image("first", b"first") is True
    assert image_cache.store_image("second", b"second") is False
    assert image_cache.get_image("first") == b"first"
    assert image_cache.get_image("second") is None


def test_store_enforces_byte_budget(monkeypatch):
    monkeypatch.setattr(image_cache, "_MAX_CACHE_ENTRIES", 10)
    monkeypatch.setattr(image_cache, "_MAX_CACHE_BYTES", 5)

    assert image_cache.store_image("first", b"1234") is True
    assert image_cache.store_image("second", b"12") is False
    assert image_cache.get_image("first") == b"1234"


def test_pop_images_is_atomic_when_any_item_is_missing():
    assert image_cache.store_image("first", b"one")
    assert image_cache.store_image("second", b"two")

    images, missing = image_cache.pop_images(["first", "missing"])

    assert images is None
    assert missing == ["missing"]
    assert image_cache.get_image("first") == b"one"
    assert image_cache.get_image("second") == b"two"


def test_pop_images_returns_requested_order():
    assert image_cache.store_image("first", b"one")
    assert image_cache.store_image("second", b"two")

    images, missing = image_cache.pop_images(["second", "first"])

    assert images == [b"two", b"one"]
    assert missing == []
    assert image_cache.get_image("first") is None
    assert image_cache.get_image("second") is None
