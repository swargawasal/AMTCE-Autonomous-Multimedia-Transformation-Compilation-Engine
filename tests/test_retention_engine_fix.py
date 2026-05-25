import unittest
import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Content_Intelligence.retention_curve_engine import RetentionCurveEngine
from Diagnostics_Modules.pipeline_audit import SilentFailureDetector


class TestRetentionEngineFix(unittest.TestCase):
    def test_beats_only_uniform_peaks_not_flagged(self):
        """
        Beats-only footage produces naturally periodic peaks.
        This is CORRECT behaviour — the auditor must NOT flag it as suspicious.
        """
        engine = RetentionCurveEngine()

        profile_data = {
            "duration": 10.0,
            "beat_data": {
                "beats": [
                    {"time": 2.0, "energy": 0.8},
                    {"time": 4.0, "energy": 0.8},
                    {"time": 6.0, "energy": 0.8},
                    {"time": 8.0, "energy": 0.8},
                ]
            },
            "motion_scores": [],        # absent — beats-only scenario
            "subject_tracking": [],     # absent — beats-only scenario
            "candidate_moments": [],
        }

        result = engine.analyse(profile_data)
        peaks = result["retention_peaks"]
        times = [p["time"] for p in peaks]
        print(f"Beats-only peaks: {times}")

        # Write peaks back so the auditor can inspect them
        profile_data["retention_peaks"] = peaks

        detector = SilentFailureDetector()
        failures = detector.detect(profile_data)

        uniform_failures = [f for f in failures if "suspiciously uniform" in f]
        self.assertEqual(
            uniform_failures,
            [],
            "Beats-only uniform peaks should NOT be flagged as suspicious",
        )

    def test_multisignal_uniform_peaks_flagged(self):
        """
        When motion + face + beats are all present but peaks are still perfectly
        regular, that IS suspicious (engine ignored the other signals).
        """
        # Synthesise a multi-signal profile where peaks are forced to be uniform
        # by placing equal retention_peaks manually at exact 2-second intervals.
        profile_data = {
            "duration": 10.0,
            "motion_scores": [{"time": 1.5, "score": 0.7}],  # non-empty
            "subject_tracking": [{"time": 3.0, "bbox": [100, 100, 200, 300]}],  # non-empty
            "candidate_moments": [],
            "beat_data": {
                "beats": [
                    {"time": 2.0, "energy": 0.8},
                    {"time": 4.0, "energy": 0.8},
                    {"time": 6.0, "energy": 0.8},
                    {"time": 8.0, "energy": 0.8},
                ]
            },
            # Manually inject perfectly uniform peaks to simulate the engine
            # ignoring motion/face diversity
            "retention_peaks": [
                {"time": 2.0, "score": 0.5},
                {"time": 4.0, "score": 0.5},
                {"time": 6.0, "score": 0.5},
            ],
        }

        detector = SilentFailureDetector()
        failures = detector.detect(profile_data)

        uniform_failures = [f for f in failures if "suspiciously uniform" in f]
        self.assertGreater(
            len(uniform_failures),
            0,
            "Uniform peaks with multi-signal input SHOULD be flagged as suspicious",
        )

    def test_energy_biased_peaks(self):
        """
        With varying beat energy, the curve should score higher near stronger beats.
        """
        engine = RetentionCurveEngine()

        profile_data = {
            "duration": 10.0,
            "beat_data": {
                "beats": [
                    {"time": 2.0, "energy": 0.3},  # weak
                    {"time": 4.0, "energy": 0.9},  # strong
                    {"time": 6.0, "energy": 0.3},  # weak
                    {"time": 8.0, "energy": 0.9},  # strong
                ]
            },
            "motion_scores": [],
            "subject_tracking": [],
            "candidate_moments": [],
        }

        result = engine.analyse(profile_data)
        curve = result["retention_curve"]

        score_4 = next((c["score"] for c in curve if abs(c["time"] - 4.0) < 0.3), None)
        score_2 = next((c["score"] for c in curve if abs(c["time"] - 2.0) < 0.3), None)

        print(f"Score at 4.0s (strong): {score_4}")
        print(f"Score at 2.0s (weak):   {score_2}")

        self.assertIsNotNone(score_4, "Should have a curve point near 4.0s")
        self.assertIsNotNone(score_2, "Should have a curve point near 2.0s")
        self.assertGreater(score_4, score_2, "Stronger beats should yield higher retention scores")


if __name__ == "__main__":
    unittest.main()
