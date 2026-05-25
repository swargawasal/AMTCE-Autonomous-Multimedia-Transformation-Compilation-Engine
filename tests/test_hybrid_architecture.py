import pytest
import os
import sys
import json
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from Intelligence_Modules import vision_intelligence, content_brain
from Monetization_Metrics import fashion_scout

def test_vision_fallback():
    # Provide empty frames, should use fallback since we probably mock Gemini failure
    res = vision_intelligence.get_fallback_payload()
    assert res["watermark"]["present"] is False
    assert res["forensic"]["safety"] == "safe"
    assert "scene_type" in res["forensic"]

def test_content_brain_fallback():
    res = content_brain.get_fallback_payload()
    assert "editorial_script" in res["brain"]
    assert "narrative" in res

def test_fashion_fallback():
    res = fashion_scout.get_fallback_payload()
    assert res["fashion"]["vibe"] == "CASUAL"

def test_mock_cache_compatibility():
    vd = vision_intelligence.get_fallback_payload()
    cd = content_brain.get_fallback_payload()
    fd = fashion_scout.get_fallback_payload()
    
    # We copy the mock class from orchestrator
    class MockIntelligenceCache:
        def __init__(self, vd, cd, fd):
            self.watermarks = []
            raw_wm = vd.get("watermark", {}).get("items", [])
            for item in raw_wm:
                box = item.get("box_2d")
                if box and len(box) == 4:
                    ymin, xmin, ymax, xmax = box
                    self.watermarks.append({
                        "coordinates": {
                            "x": xmin, "y": ymin, "w": max(1, xmax - xmin), "h": max(1, ymax - ymin)
                        },
                        "type": item.get("type", "logo")
                    })
            self.quality_score = float(vd.get("quality", {}).get("score", 0.5))
            self.upscale_recommended = bool(vd.get("quality", {}).get("upscale_recommended", False))
            self.ffmpeg_recipe = vd.get("quality", {}).get("ffmpeg_recipe", {})
            self.forensic_strategy = vd.get("forensic", {})
            self.editorial_script = cd.get("brain", {}).get("editorial_script", "")
            self.generated_title = cd.get("brain", {}).get("generated_title", "")
            self.overlay_data = cd.get("brain", {}).get("overlay_data", {})
            self.fashion_scout = fd.get("fashion", {})
            self.narrative_script = cd.get("narrative", {}).get("script", "")
            self.risk_level = "LOW" if self.forensic_strategy.get("safety") == "safe" else "MEDIUM"
            self.api_calls_made = 3
            
    cache = MockIntelligenceCache(vd, cd, fd)
    assert cache.watermarks == []
    assert cache.risk_level == "LOW"
    assert cache.quality_score == 0.5
    assert cache.api_calls_made == 3

def test_mock_cache_with_watermarks():
    vd = {
        "watermark": {
            "present": True,
            "items": [{"box_2d": [100, 200, 300, 400], "type": "logo"}]
        },
        "quality": {"score": 0.8, "upscale_recommended": False},
        "forensic": {"safety": "safe"}
    }
    cd = content_brain.get_fallback_payload()
    fd = fashion_scout.get_fallback_payload()
    
    class MockIntelligenceCache:
        def __init__(self, vd, cd, fd):
            self.watermarks = []
            raw_wm = vd.get("watermark", {}).get("items", [])
            for item in raw_wm:
                box = item.get("box_2d")
                if box and len(box) == 4:
                    ymin, xmin, ymax, xmax = box
                    self.watermarks.append({
                        "coordinates": {
                            "x": xmin, "y": ymin, "w": max(1, xmax - xmin), "h": max(1, ymax - ymin)
                        },
                        "type": item.get("type", "logo")
                    })
            self.quality_score = float(vd.get("quality", {}).get("score", 0.5))
            self.upscale_recommended = bool(vd.get("quality", {}).get("upscale_recommended", False))
            self.ffmpeg_recipe = vd.get("quality", {}).get("ffmpeg_recipe", {})
            self.forensic_strategy = vd.get("forensic", {})
            self.editorial_script = cd.get("brain", {}).get("editorial_script", "")
            self.generated_title = cd.get("brain", {}).get("generated_title", "")
            self.overlay_data = cd.get("brain", {}).get("overlay_data", {})
            self.fashion_scout = fd.get("fashion", {})
            self.narrative_script = cd.get("narrative", {}).get("script", "")
            self.risk_level = "LOW" if self.forensic_strategy.get("safety") == "safe" else "MEDIUM"
            self.api_calls_made = 3

    cache = MockIntelligenceCache(vd, cd, fd)
    assert len(cache.watermarks) == 1
    assert "coordinates" in cache.watermarks[0]
    assert cache.watermarks[0]["coordinates"]["x"] == 200
    assert cache.watermarks[0]["coordinates"]["y"] == 100
    assert cache.watermarks[0]["coordinates"]["w"] == 200
    assert cache.watermarks[0]["coordinates"]["h"] == 200

