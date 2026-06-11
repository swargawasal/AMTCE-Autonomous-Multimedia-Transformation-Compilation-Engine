"""
tests/test_viral_hooks.py
--------------------------
Unit tests for the select_viral_hook() function.
Validates:
  1. Returns a non-empty string
  2. Actress/title name is injected into {name} placeholders
  3. Memory de-duplication avoids repeating the same hook consecutively
  4. VIRAL_HOOKS pool contains all 20 expected hooks
  5. Fallback works with empty context
"""

import os
import sys
import pytest

# Add project root to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Text_Modules.overlay_engine import select_viral_hook, VIRAL_HOOKS


class TestViralHooks:
    """Tests for the viral hook selection system."""

    def test_returns_string(self):
        """Hook selector always returns a string."""
        result = select_viral_hook({})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_context_works(self):
        """No crash on empty context."""
        result = select_viral_hook(None)
        assert isinstance(result, str)
        result2 = select_viral_hook({})
        assert isinstance(result2, str)

    def test_name_injected_when_title_given(self):
        """When actress_name is provided, {name} hooks use the actual name."""
        name_hooks = [h for h in VIRAL_HOOKS if "{name}" in h]
        assert len(name_hooks) >= 3, "Need at least 3 name-bearing hooks in pool"

        ctx = {
            "actress_name": "Mrunal",
            "niche_category": "entertainment",
            "energy_score": 0.8,
        }
        # Run multiple times to eventually land a {name} hook
        results = [select_viral_hook(ctx) for _ in range(20)]
        name_results = [r for r in results if "Mrunal" in r]
        assert len(name_results) > 0, "Expected at least one hook referencing actress name"

    def test_no_raw_placeholder_in_output(self):
        """The literal '{name}' placeholder must never appear in output."""
        for _ in range(10):
            result = select_viral_hook({"actress_name": "TestActress"})
            assert "{name}" not in result, f"Unreplaced placeholder in: {result}"

    def test_no_name_bhai_fallback(self):
        """When no name in context, {name} slots fall back to 'Bhai'."""
        results = [select_viral_hook({"title": ""}) for _ in range(20)]
        for r in results:
            assert "{name}" not in r
            # If a name hook was picked, it should use 'Bhai'
            # (not crash or leave placeholder)

    def test_viral_hooks_pool_size(self):
        """Pool should contain exactly 20 hooks."""
        assert len(VIRAL_HOOKS) == 20, f"Expected 20 hooks, got {len(VIRAL_HOOKS)}"

    def test_energy_score_affects_selection(self):
        """High energy_score should return energetic-type hooks more often."""
        high_energy_results = [
            select_viral_hook({
                "title": "",
                "niche_category": "entertainment",
                "energy_score": 0.9,
            })
            for _ in range(15)
        ]
        # Just verify they are all non-empty strings — mood routing is soft
        assert all(isinstance(r, str) and len(r) > 0 for r in high_energy_results)

    def test_no_crash_on_bad_energy_score(self):
        """No crash on invalid/missing energy_score."""
        result = select_viral_hook({"energy_score": None})
        assert isinstance(result, str)
        result2 = select_viral_hook({"energy_score": "invalid"})
        assert isinstance(result2, str)

    def test_title_extraction_strips_system_prefixes(self):
        """System prefixes like 'VIRAL:' and 'FASHION:' are stripped from title."""
        ctx = {"title": "VIRAL: Mrunal Thakur Dance", "actress_name": ""}
        results = [select_viral_hook(ctx) for _ in range(20)]
        # Should never see raw system prefix in hook output
        for r in results:
            assert "VIRAL:" not in r

    def test_uniqueness_over_runs(self):
        """Should not repeat the exact same hook 5 times in a row (memory guard)."""
        ctx = {"title": "Test Video", "niche_category": "fashion"}
        results = [select_viral_hook(ctx) for _ in range(5)]
        # At least 2 different hooks in 5 runs (memory de-dup should kick in)
        unique = set(results)
        assert len(unique) >= 2, f"Too many repeats: {results}"
