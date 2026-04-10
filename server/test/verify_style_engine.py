import sys
import os
import unittest
from unittest.mock import MagicMock
import json

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from service_style_engine import calculate_composite_score, interpolate_recipes, StyleEngine
from edit_recipe import EditRecipe

class TestStyleEngineLogic(unittest.TestCase):
    
    def test_composite_score(self):
        print("Testing composite score...")
        target_meta = {
            "clip_embedding": [1.0] * 512,
            "exposure": {"mean_luminance": 0.5},
            "scene_tags": ["scene_outdoor"]
        }
        
        # ex1 matches everything
        ex1 = {
            "clip_embedding": [1.0] * 512,
            "metadata": {
                "exposure_metrics": json.dumps({"mean_luminance": 0.5}),
                "scene_tags": json.dumps(["scene_outdoor"])
            }
        }
        
        # ex2 matches nothing well
        ex2 = {
            "clip_embedding": [0.0] * 512,
            "metadata": {
                "exposure_metrics": json.dumps({"mean_luminance": 0.1}),
                "scene_tags": json.dumps(["scene_dark"])
            }
        }
        
        score1, det1 = calculate_composite_score(target_meta, ex1)
        score2, det2 = calculate_composite_score(target_meta, ex2)
        
        print(f"  Example 1 Score: {score1} ({det1})")
        print(f"  Example 2 Score: {score2} ({det2})")
        
        self.assertGreater(score1, score2)
        self.assertEqual(det1["scene_match"], 1.0)
        self.assertLess(det2["scene_match"], 0.2)

    def test_interpolation(self):
        print("Testing recipe interpolation...")
        r1 = EditRecipe({"Exposure": 1.0, "Contrast": 0})
        r2 = EditRecipe({"Exposure": 0.0, "Contrast": 40})
        
        weighted = [
            (r1, 0.75),
            (r2, 0.25)
        ]
        
        interp = interpolate_recipes(weighted)
        
        # 1.0 * 0.75 + 0.0 * 0.25 = 0.75
        self.assertEqual(interp.get_global_setting("Exposure"), 0.75)
        # 0 * 0.75 + 40 * 0.25 = 10
        self.assertEqual(interp.get_global_setting("Contrast"), 10)

    def test_adaptive_compensation(self):
        print("Testing adaptive RAW compensation...")
        # Training was high key (0.8), Target is low key (0.3).
        # Shift is 0.3 - 0.8 = -0.5.
        # Should result in POSITIVE exposure shift to compensate for the underexposed target.
        
        training_exp = {"mean_luminance": 0.8}
        target_exp = {"mean_luminance": 0.3}
        
        r1 = EditRecipe({"Exposure": 0.0}) 
        
        engine = StyleEngine()
        engine.training_service = MagicMock()
        engine.training_service.get_training_stats.return_value = {"readiness": "active", "count": 20}
        
        mock_ex = {
            "id": "ex1",
            "clip_embedding": [1.0]*512,
            "metadata": {
                "recipe_json": r1.to_json(),
                "exposure_metrics": json.dumps(training_exp),
                "scene_tags": "[]"
            }
        }
        engine.training_service.query_similar_training_examples.return_value = [mock_ex]
        
        target_meta = {
            "exposure": target_exp,
            "clip_embedding": [1.0]*512,
            "scene_tags": []
        }
        
        recipe, conf, stats = engine.style_edit(b"fake", target_meta)
        
        final_exp = recipe.get_global_setting("Exposure")
        print(f"  Adaptive Shift: {stats['adaptive_exposure_shift']}")
        print(f"  Final Exposure: {final_exp}")
        
        # Target is darker than training. Compensation should be positive.
        self.assertGreater(stats["adaptive_exposure_shift"], 0)
        self.assertGreater(final_exp, 0)

if __name__ == "__main__":
    unittest.main()
