import os
import json
import pytest

from Intelligence_Modules.caption_memory import stable_hash
from Text_Modules.gemini_captions import (
    compress_caption_text,
    select_style_from_path,
    stable_hash as gc_hash,
)


def load_config():
    with open("The_json/caption_prompt.json", "r", encoding="utf-8") as f:
        return json.load(f)


def test_stable_hash_consistency():
    text = "sample_video_path.mp4"
    assert stable_hash(text) == stable_hash(text)
    assert gc_hash(text) == gc_hash(text)


def test_priority_compression_keeps_energy():
    cfg = load_config()
    filler = cfg.get("FILLER_WORDS", [])
    priority = cfg.get("PRIORITY_WORDS", [])
    raw = "Main character energy all day long"
    compressed = compress_caption_text(raw, filler, priority, target_max=4, hard_max=6)
    words = compressed.split()
    assert "energy" in [w.lower() for w in words]
    assert len(words) <= 6


def test_style_distribution_entropy():
    cfg = load_config()
    categories = cfg.get("STYLE_CATEGORIES", [])
    selections = [
        select_style_from_path(f"video_{i}.mp4", categories)
        for i in range(50)
    ]
    # Expect broad spread across categories (at least 5 unique)
    assert len(set(selections)) >= min(5, len(categories))
