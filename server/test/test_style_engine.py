import sys
import os
import pytest
from unittest.mock import MagicMock

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from service_style_engine import calculate_composite_score, interpolate_recipes, StyleEngine
from edit_recipe import EditRecipe

def test_calculate_composite_score():
    target_meta = {
        "clip_embedding": [0.1] * 512,
        "exposure": {"mean_luminance": 0.5},
        "scene_tags": ["scene_outdoor", "scene_landscape"]
    }
    
    # Example 1: High visual similarity, same scene
    ex1 = {
        "clip_embedding": [0.11] * 512, # high sim
        "metadata": {
            "exposure_metrics": "{\"mean_luminance\": 0.52}",
            "scene_tags": "[\"scene_outdoor\", \"scene_landscape\"]"
        }
    }
    
    # Example 2: Low visual similarity
    ex2 = {
        "clip_embedding": [0.5] * 512, # low sim
        "metadata": {
            "exposure_metrics": "{\"mean_luminance\": 0.5}",
            "scene_tags": "[\"scene_outdoor\"]"
        }
    }
    
    score1, details1 = calculate_composite_score(target_meta, ex1)
    score2, details2 = calculate_composite_score(target_meta, ex2)
    
    assert score1 > score2
    assert details1["visual_sim"] > 0.9
    assert details1["scene_match"] == 1.0

def test_interpolate_recipes():
    # Recipe 1: Exposure +1
    r1 = EditRecipe({"Exposure": 1.0, "Contrast": 10})
    # Recipe 2: Exposure -1
    r2 = EditRecipe({"Exposure": -1.0, "Contrast": 50})
    
    recipes_with_weights = [
        (r1, 0.5),
        (r2, 0.5)
    ]
    
    interpolated = interpolate_recipes(recipes_with_weights)
    
    # (1.0*0.5) + (-1.0*0.5) = 0
    assert interpolated.get_global_setting("Exposure") == 0.0
    # (10*0.5) + (50*0.5) = 30
    assert interpolated.get_global_setting("Contrast") == 30

def test_adaptive_compensation():
    # If training photo was dark (0.2) and target is bright (0.8)
    # The engine should suggest LOWERING exposure relative to what was done to the dark photo
    
    training_exp = {"mean_luminance": 0.2}
    target_exp = {"mean_luminance": 0.8}
    
    # Let's say the recipe for r1 was perfect for a dark photo
    r1 = EditRecipe({"Exposure": 0.5}) 
    
    style_engine = StyleEngine()
    
    # Mocking verify_training_readiness to always return true
    style_engine.training_service.get_training_stats = MagicMock(return_value={"readiness": "active", "count": 20})
    
    # Mocking query_similar_training_examples
    mock_ex = {
        "id": "ex1",
        "clip_embedding": [0.1]*512,
        "metadata": {
            "recipe_json": r1.to_json(),
            "exposure_metrics": "{\"mean_luminance\": 0.2}",
            "scene_tags": "[]"
        }
    }
    style_engine.training_service.query_similar_training_examples = MagicMock(return_value=[mock_ex])
    
    target_photo_bytes = b"fake"
    target_meta = {
        "exposure": target_exp,
        "clip_embedding": [0.1]*512,
        "scene_tags": []
    }
    
    # We should see the compensation logic kicked in
    # target (0.8) - training (0.2) = 0.6 difference. 
    # Exposure in recipe was 0.5. 
    # Result should be roughly 0.5 - 0.6 = -0.1
    
    result_recipe, confidence, stats = style_engine.style_edit(target_photo_bytes, target_meta)
    
    final_exposure = result_recipe.get_global_setting("Exposure")
    assert final_exposure < 0.5
    assert stats["adaptive_exposure_shift"] < 0
