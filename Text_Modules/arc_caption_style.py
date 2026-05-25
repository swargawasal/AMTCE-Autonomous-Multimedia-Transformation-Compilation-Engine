"""
Shared arc-caption styling helper.

This module centralizes the "viral / reference image" caption style so both
rendering paths can generate consistent FFmpeg drawtext filters:

- Font: PlayfairDisplay-Bold.ttf
- Color: #FFD700
- No outline
- No shadow
- Center aligned
- 3-4 short lines
- Arc-like vertical stacking near the top of frame

The helper is intentionally standalone and dependency-light so it can be
imported from multiple modules without creating circular imports.
"""

from __future__ import annotations

import os
import re
import textwrap
from typing import Dict, List, Sequence

FONT_DIR = os.path.join("assets", "fonts")
PLAYFAIR_FONT_PATH = os.path.join(FONT_DIR, "PlayfairDisplay-Bold.ttf")
FALLBACK_FONT_PATH = PLAYFAIR_FONT_PATH

CAPTION_COLOR = "#FFD400"
CAPTION_FONT_SIZE_PCT = 0.045  # 4.5% of video height (1080p → ~48px)
CAPTION_WRAP_WIDTH = 35
BORDER_WIDTH = 3
BORDER_COLOR = "black"
SHADOW_COLOR = "black"
SHADOW_X = 2
SHADOW_Y = 2

BRAND_FONT_SIZE = 32
BRAND_BORDER_WIDTH = 2

MIN_CHARS_PER_LINE = 12
MAX_CHARS_PER_LINE = 14
MIN_LINES = 3
MAX_LINES = 4

# Top-of-frame arc positions requested by user.
ARC_Y_POSITIONS: Dict[int, Sequence[str]] = {
    1: ("h*0.12",),
    2: ("h*0.08", "h*0.17"),
    3: ("h*0.05", "h*0.12", "h*0.20"),
    4: ("h*0.05", "h*0.12", "h*0.20", "h*0.28"),
}

EMPHASIS_WORDS = {
    "i",
    "me",
    "my",
    "mine",
    "you",
    "your",
    "yours",
    "we",
    "our",
    "ours",
    "they",
    "them",
    "theirs",
    "bad",
    "crazy",
    "wild",
    "truth",
    "never",
    "always",
    "why",
    "how",
    "what",
    "who",
    "stop",
    "wait",
    "look",
    "listen",
    "this",
    "that",
    "topic",
    "secret",
    "viral",
    "broke",
    "rich",
    "best",
    "worst",
    "now",
    "today",
}

EMPHASIS_PUNCTUATION = ("!", "?", "!!", "?!", "!?")

_UPPER_TOKEN_RE = re.compile(r"[A-Z]{3,}")
_WORD_RE = re.compile(r"\S+")


def get_font_path() -> str:
    """
    Return the preferred Playfair font path.

    The caller can decide whether to verify existence. This helper only
    centralizes the configured font location.
    """
    return (
        PLAYFAIR_FONT_PATH if os.path.exists(PLAYFAIR_FONT_PATH) else FALLBACK_FONT_PATH
    )


def escape_drawtext(text: str) -> str:
    """
    Escape text for FFmpeg drawtext usage.
    """
    if not text:
        return ""

    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace(":", r"\:")
    escaped = escaped.replace("'", "")
    escaped = escaped.replace("%", r"\%")
    escaped = escaped.replace("[", r"\[").replace("]", r"\]")
    escaped = escaped.replace("\n", " ")
    escaped = " ".join(escaped.split())
    return escaped


def normalize_caption_text(text: str) -> str:
    """
    Clean caption input while preserving original casing for emphasis heuristics.
    """
    if not text:
        return ""

    cleaned = text.replace("\r", " ").replace("\n", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text)


def _line_char_limits() -> List[int]:
    """
    Prefer 4 lines when possible, but allow 3 lines if the text is shorter.
    """
    return [
        MAX_CHARS_PER_LINE,
        MAX_CHARS_PER_LINE,
        MAX_CHARS_PER_LINE,
        MAX_CHARS_PER_LINE,
    ]


def _split_long_token(token: str, max_len: int) -> List[str]:
    if len(token) <= max_len:
        return [token]

    parts: List[str] = []
    start = 0
    while start < len(token):
        parts.append(token[start : start + max_len])
        start += max_len
    return parts


def wrap_caption_lines(
    text: str,
    min_chars: int = MIN_CHARS_PER_LINE,
    max_chars: int = MAX_CHARS_PER_LINE,
    min_lines: int = MIN_LINES,
    max_lines: int = MAX_LINES,
) -> List[str]:
    """
    Split text into 3-4 short lines suitable for arc captions.

    Rules:
    - Avoid paragraph-like output
    - Prefer 4 lines when enough text exists
    - Keep each line around 12-14 characters where feasible
    - Never exceed 4 lines
    """
    cleaned = normalize_caption_text(text)
    if not cleaned:
        return []

    words: List[str] = []
    for token in _tokenize(cleaned):
        words.extend(_split_long_token(token, max_chars))

    if not words:
        return []

    # First pass: greedy wrap respecting max_chars.
    lines: List[str] = []
    current = ""

    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            lines.append(current)
        current = word

    if current:
        lines.append(current)

    # If we got too many lines, rebalance into exactly max_lines.
    if len(lines) > max_lines:
        rejoined = " ".join(lines)
        target_lines = max_lines
        width = max_chars
        balanced = textwrap.wrap(
            rejoined,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
        )
        while len(balanced) > target_lines and width < max_chars + 4:
            width += 1
            balanced = textwrap.wrap(
                rejoined,
                width=width,
                break_long_words=True,
                break_on_hyphens=False,
            )
        lines = balanced[:max_lines]

    # If too few lines, split longer lines until we reach min_lines when possible.
    while len(lines) < min_lines:
        idx = max(range(len(lines)), key=lambda i: len(lines[i]))
        line = lines[idx]
        parts = line.split()
        if len(parts) < 2:
            break

        midpoint = max(1, len(parts) // 2)
        left = " ".join(parts[:midpoint]).strip()
        right = " ".join(parts[midpoint:]).strip()

        if not left or not right:
            break

        replacement = [left, right]
        lines = lines[:idx] + replacement + lines[idx + 1 :]

        if len(lines) >= min_lines:
            break

    # Clamp to max_lines and remove empty lines.
    lines = [line.strip() for line in lines if line.strip()][:max_lines]

    # Final compaction: if still too many lines, merge the shortest adjacent pair.
    while len(lines) > max_lines:
        best_idx = 0
        best_len = None
        for i in range(len(lines) - 1):
            merged_len = len(lines[i]) + 1 + len(lines[i + 1])
            if best_len is None or merged_len < best_len:
                best_len = merged_len
                best_idx = i
        merged = f"{lines[best_idx]} {lines[best_idx + 1]}".strip()
        lines = lines[:best_idx] + [merged] + lines[best_idx + 2 :]

    return lines


def is_emphasis_line(line: str) -> bool:
    """
    Heuristic to choose the larger emphasis font size.

    A line is emphasized if:
    - it contains strong punctuation
    - it is mostly uppercase
    - it contains a known emphasis keyword
    - it is a very short punch line
    """
    if not line:
        return False

    stripped = line.strip()
    lowered = stripped.lower()

    if any(p in stripped for p in EMPHASIS_PUNCTUATION):
        return True

    if _UPPER_TOKEN_RE.search(stripped):
        return True

    words = re.findall(r"[A-Za-z']+", lowered)
    if any(word in EMPHASIS_WORDS for word in words):
        return True

    if len(stripped) <= 8:
        return True

    return False


def font_size_for_line(line: str) -> int:
    return EMPHASIS_FONT_SIZE if is_emphasis_line(line) else NORMAL_FONT_SIZE


def y_positions_for_count(line_count: int) -> Sequence[str]:
    if line_count <= 1:
        return ARC_Y_POSITIONS[1]
    if line_count == 2:
        return ARC_Y_POSITIONS[2]
    if line_count == 3:
        return ARC_Y_POSITIONS[3]
    return ARC_Y_POSITIONS[4]


def build_arc_caption_spec(text: str) -> List[dict]:
    """
    Return structured caption line data:
    [
        {"text": "...", "fontsize": 85, "y": "h*0.05"},
        ...
    ]
    """
    lines = wrap_caption_lines(text)
    if not lines:
        return []

    positions = y_positions_for_count(len(lines))
    spec: List[dict] = []

    for idx, line in enumerate(lines):
        spec.append(
            {
                "text": line,
                "fontsize": font_size_for_line(line),
                "y": positions[idx],
                "fontcolor": CAPTION_COLOR,
                "align": "center",
            }
        )

    return spec


def build_drawtext_filters(
    text: str,
    *,
    enable_expr: str | None = None,
    font_path: str | None = None,
) -> List[str]:
    """
    Build FFmpeg drawtext filter segments for the arc caption style.
    """
    resolved_font = font_path or get_font_path()
    escaped_font = os.path.abspath(resolved_font).replace("\\", "/").replace(":", r"\:")

    filters: List[str] = []
    for item in build_arc_caption_spec(text):
        safe_line = escape_drawtext(item["text"])
        if not safe_line:
            continue

        segment = (
            f"drawtext=fontfile='{escaped_font}':"
            f"text='{safe_line}':"
            f"fontsize={item['fontsize']}:"
            f"fontcolor={item['fontcolor']}:"
            f"x=(w-text_w)/2:"
            f"y={item['y']}"
        )

        if enable_expr:
            segment += f":enable='{enable_expr}'"

        filters.append(segment)

    return filters


def build_caption_drawtext_filter(
    text: str,
    video_height: int = 1920,
    *,
    enable_expr: str | None = None,
    font_path: str | None = None,
) -> str:
    """
    Build FFmpeg drawtext filter for viral caption style.

    Style:
    - Color: Yellow (#FFD400)
    - Font: Bold
    - Outline: Black borderw=3
    - Shadow: black shadowx=2 shadowy=2
    - Position: Centered at y=h*0.70
    - Width: Wrapped to 35 chars max
    """
    resolved_font = font_path or get_font_path()
    escaped_font = os.path.abspath(resolved_font).replace("\\", "/").replace(":", r"\:")

    # Wrap text to max 35 chars per line
    wrapped_lines = textwrap.wrap(text, width=CAPTION_WRAP_WIDTH) if text else []
    if not wrapped_lines:
        return ""

    # Dynamic font size: 4.5% of video height
    font_size = int(video_height * CAPTION_FONT_SIZE_PCT)

    filters = []
    line_height = int(font_size * 1.2)  # Line spacing

    # Calculate starting Y position (center the block at h*0.70)
    total_height = len(wrapped_lines) * line_height
    start_y = f"h*0.70 - {total_height // 2}"

    for i, line in enumerate(wrapped_lines):
        safe_line = escape_drawtext(line)
        if not safe_line:
            continue

        y_pos = f"{start_y} + {i * line_height}"

        segment = (
            f"drawtext=fontfile='{escaped_font}':"
            f"text='{safe_line}':"
            f"fontsize={font_size}:"
            f"fontcolor={CAPTION_COLOR}:"
            f"borderw={BORDER_WIDTH}:"
            f"bordercolor={BORDER_COLOR}:"
            f"shadowcolor={SHADOW_COLOR}:"
            f"shadowx={SHADOW_X}:"
            f"shadowy={SHADOW_Y}:"
            f"x=(w-text_w)/2:"
            f"y={y_pos}"
        )

        if enable_expr:
            segment += f":enable='{enable_expr}'"

        filters.append(segment)

    return ",".join(filters)
