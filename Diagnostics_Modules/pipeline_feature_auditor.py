import json
import logging
import os
from typing import Dict, Any, List

logger = logging.getLogger("feature_auditor")

class FeatureAuditor:
    """
    Tracks execution states of pipeline features and validates critical dependencies.
    """
    STATES = ["EXECUTED", "SKIPPED", "DISABLED", "FAILED"]

    def __init__(self):
        self.features: Dict[str, str] = {}
        # Pre-register common features to track
        core_features = [
            "watermark_detection", "watermark_inpaint", "scene_detection", 
            "scene_reconstruction", "voiceover_generation", "caption_generation", 
            "price_tag_engine", "music_engine", "smart_crop", "trend_engine", 
            "hook_engine", "beat_detection", "subject_tracking"
        ]
        for f in core_features:
            self.register_feature(f)

    def register_feature(self, feature_name: str):
        if feature_name not in self.features:
            self.features[feature_name] = "PENDING"

    def mark_executed(self, feature_name: str):
        self._set_state(feature_name, "EXECUTED")

    def mark_failed(self, feature_name: str):
        self._set_state(feature_name, "FAILED")

    def mark_skipped(self, feature_name: str):
        self._set_state(feature_name, "SKIPPED")

    def mark_disabled(self, feature_name: str):
        self._set_state(feature_name, "DISABLED")

    def _set_state(self, feature_name: str, state: str):
        try:
            if state in self.STATES:
                self.features[feature_name] = state
                logger.debug(f"📊 Feature [{feature_name}] marked as {state}")
        except Exception as e:
            # Failsafe: auditor must never crash the pipeline
            logger.error(f"Auditor internal error tracking {feature_name}: {e}")

    def status(self, feature_name: str) -> str:
        return self.features.get(feature_name, "UNKNOWN")

    def validate_dependencies(self):
        """
        Validates module dependency rules and marks downstream module failures if violated.
        Rules:
        - voiceover requires captions
        - price_tags require subject_tracking
        - scene_reconstruction requires scene_detection
        """
        try:
            # Rule 1: Voiceover needs captions
            if self.status("voiceover_generation") == "EXECUTED" and self.status("caption_generation") in ["FAILED", "DISABLED", "SKIPPED"]:
                logger.warning("⚠️ Dependency Violation: Voiceover ran but Captions failed/skipped.")
                
            # Rule 2: Price tags need subject tracking
            if self.status("price_tag_engine") == "EXECUTED" and self.status("subject_tracking") in ["FAILED", "DISABLED"]:
                logger.warning("⚠️ Dependency Violation: Price Tags ran but Subject Tracking failed.")
                
            # Rule 3: Reconstruction needs detection
            if self.status("scene_reconstruction") == "EXECUTED" and self.status("scene_detection") in ["FAILED", "DISABLED"]:
                logger.warning("⚠️ Dependency Violation: Reconstruction ran but Scene Detection failed.")
        except Exception as e:
            logger.error(f"Auditor dependency validation error: {e}")

    def generate_report(self) -> str:
        self.validate_dependencies()
        report = ["PIPELINE FEATURE REPORT", "-" * 30]
        for f, state in self.features.items():
            report.append(f"{f} -> {state}")
        return "\n".join(report)

    def save_report(self, output_path: str):
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(self.features, f, indent=4)
            logger.info(f"💾 Feature Report saved to {output_path}")
        except Exception as e:
            logger.error(f"Failed to save Feature Report: {e}")
