import base64
import io
import unittest

from PIL import Image

from providers.base import LLMProviderBase


class _StubProvider(LLMProviderBase):
    def generate_metadata(self, request):
        raise NotImplementedError

    def is_available(self):
        return True

    def generate_edit_recipe(self, request):
        raise NotImplementedError

    def list_available_models(self):
        return []


def _make_jpeg_bytes(color=(255, 0, 0)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _make_png_bytes(color=(0, 255, 0)):
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


class ImageToBase64Tests(unittest.TestCase):
    def setUp(self):
        self.provider = _StubProvider({})

    def test_jpeg_input_is_validated_and_reencoded(self):
        jpeg_bytes = _make_jpeg_bytes()
        result = self.provider._image_to_base64(jpeg_bytes)
        decoded = base64.b64decode(result)
        self.assertTrue(decoded.startswith(b"\xff\xd8\xff"))
        with Image.open(io.BytesIO(decoded)) as image:
            self.assertEqual(image.size, (8, 8))

    def test_jpeg_magic_number_detected(self):
        jpeg_bytes = _make_jpeg_bytes()
        self.assertTrue(jpeg_bytes.startswith(b"\xff\xd8\xff"))

    def test_png_re_encoded_to_jpeg(self):
        png_bytes = _make_png_bytes()
        result = self.provider._image_to_base64(png_bytes)
        decoded = base64.b64decode(result)
        # Should now be a JPEG, not the original PNG bytes
        self.assertNotEqual(decoded, png_bytes)
        self.assertTrue(decoded.startswith(b"\xff\xd8\xff"))
        # And should still decode back to a valid image
        Image.open(io.BytesIO(decoded)).verify()

    def test_returns_valid_base64(self):
        result = self.provider._image_to_base64(_make_jpeg_bytes())
        self.assertIsInstance(result, str)
        # No exception means valid base64
        base64.b64decode(result, validate=True)

    def test_non_image_bytes_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.provider._image_to_base64(b"not an image at all")

    def test_empty_bytes_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.provider._image_to_base64(b"")

    def test_invalid_jpeg_magic_header_is_not_trusted(self):
        bogus = b"\xff\xd8\xff" + b"garbage payload"
        with self.assertRaises(ValueError):
            self.provider._image_to_base64(bogus)

    def test_large_jpeg_is_bounded_before_base64_encoding(self):
        buffer = io.BytesIO()
        with Image.new("RGB", (4096, 3072), (10, 20, 30)) as image:
            image.save(buffer, format="JPEG")

        decoded = base64.b64decode(self.provider._image_to_base64(buffer.getvalue()))
        with Image.open(io.BytesIO(decoded)) as image:
            self.assertLessEqual(max(image.size), 2048)


if __name__ == "__main__":
    unittest.main()
