"""Segment safety gate: clamps and sanitizes reconstructed segments before editing.

IMPORTANT: This gate deliberately preserves creative NON-CHRONOLOGICAL ordering.
A human editor may place a segment from source t=33s BEFORE source t=5s.
Do NOT sort by start time — that destroys the editorial intent.
"""

from typing import List, Dict, Optional

MAX_SEGMENTS = 10     # Allow up to 10 segments for full video coverage
MIN_DURATION = 0.5    # Minimum clip length
MAX_DURATION = 15.0   # Raised from 6.0 — allows full editorial segments (buildup, climax)


def validate(
    segments: List[Dict],
    video_duration: float,
    clip_duration_map: Optional[Dict[int, float]] = None,
) -> List[Dict]:
    """Return a sanitized segment list within duration bounds.

    KEY BEHAVIOR:
    - Preserves the input ORDER (non-chronological creative edits allowed)
    - Does NOT sort by start time
    - Clamps durations to [MIN_DURATION, MAX_DURATION]
    - Caps total count at MAX_SEGMENTS

    clip_duration_map: optional {clip_id: duration} dict for multi-clip jobs.
    When supplied, each segment is clamped against ITS OWN clip's duration
    instead of clip 0's duration. Single-clip jobs (map=None) are unchanged.
    """
    if not segments:
        return []

    # Limit count — keep first N (they're already priority-ordered by the editor)
    capped = segments[:MAX_SEGMENTS]

    sanitized = []
    for seg in capped:
        start = max(0.0, float(seg.get("start", 0.0)))
        end   = float(seg.get("end", start + MIN_DURATION))
        c_id  = int(seg.get("clip_id", 0))

        # [MULTI_CLIP FIX] Use per-clip duration when map is available.
        # Previously ALL segments were clamped against clip-0 duration, silently
        # dropping every segment whose timestamp exceeded clip 0's length.
        if clip_duration_map and c_id in clip_duration_map:
            clip_dur = clip_duration_map[c_id]
        else:
            clip_dur = video_duration

        if clip_dur <= 0:
            clip_dur = video_duration  # final safety net

        # Clamp to this clip's duration
        start = min(start, max(0.0, clip_dur - MIN_DURATION))
        end   = min(end,   max(0.0, clip_dur))

        # Ensure valid range
        if end <= start:
            end = start + MIN_DURATION

        duration = end - start

        # Enforce duration bounds
        if duration < MIN_DURATION:
            end = start + MIN_DURATION
            duration = MIN_DURATION
        elif duration > MAX_DURATION:
            end = start + MAX_DURATION
            duration = MAX_DURATION

        if start >= clip_dur:
            continue

        sanitized.append({**seg, "start": round(start, 3), "end": round(end, 3)})

    return sanitized
