import io
import unittest
from PIL import Image
from src.utils.image_processing import apply_face_blur

class TestImageProcessing(unittest.TestCase):
    def test_apply_face_blur_no_faces(self):
        # Create a simple 100x100 solid color image
        img = Image.new("RGB", (100, 100), color="blue")
        output = io.BytesIO()
        img.save(output, format="JPEG")
        original_bytes = output.getvalue()
        
        blurred_bytes = apply_face_blur(original_bytes, [])
        self.assertEqual(original_bytes, blurred_bytes)
        
    def test_apply_face_blur_with_faces(self):
        # Create a 100x100 solid color image
        img = Image.new("RGB", (100, 100), color="blue")
        output = io.BytesIO()
        img.save(output, format="JPEG")
        original_bytes = output.getvalue()
        
        # Bounding box of a face
        bbox = [[10, 10, 50, 50]]
        
        blurred_bytes = apply_face_blur(original_bytes, bbox)
        self.assertNotEqual(original_bytes, blurred_bytes)
        
        # Verify it's still a valid JPEG
        blurred_img = Image.open(io.BytesIO(blurred_bytes))
        self.assertEqual(blurred_img.size, (100, 100))
        self.assertEqual(blurred_img.format, "JPEG")

if __name__ == "__main__":
    unittest.main()
