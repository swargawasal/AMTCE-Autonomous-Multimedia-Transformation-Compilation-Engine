"""
CEIE — Cinematic Edit Intelligence Engine
-----------------------------------------
Translates long raw footage into YouTube-ready transformative UGC edits using sequential Gemini analysis.
"""

from ceie.chunker import chunk_video
from ceie.analyzer import VideoAnalyzer
from ceie.aggregator import globalize_plan, flatten_timeline
from ceie.applicator import apply_edit_timeline
