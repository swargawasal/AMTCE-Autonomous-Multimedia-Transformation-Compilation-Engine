"""
Higgsfield Earn — Campaign Submission Helper
============================================
Handles the monetization side of Higgsfield integration.

How Higgsfield Earn works (confirmed from higgsfield.ai):
    1. You sign up at higgsfield.ai/earn and verify social accounts
    2. Browse active campaigns (each has a brief / content requirement)
    3. Generate content using Higgsfield (free tier eligible)
    4. Post to your social media account
    5. Paste the post URL into your Earn dashboard
    6. Get paid in 3 tiers:
         - Base pay:         approved content
         - 24h bonus:        views in first 24 hours
         - 7-day milestone:  view milestones (10k, 50k, 100k)
    7. Max $1,000 per video on day 1 / $2,500 lifetime per video

Via CLI/API:
    - You CAN authenticate a Higgsfield account via CLI
    - Content generation is via CLI (programmatic) ← AMTCE controls this
    - Campaign submission is currently MANUAL via the Earn dashboard
    - This module automates everything it can and logs what requires manual action

AMTCE Log flow when Earn submit is triggered:
    [higgsfield.earn] Post detected: instagram.com/p/xxxx (60,000 views)
    [higgsfield.earn] ✅ Earn submission queued — MANUAL ACTION REQUIRED
    [higgsfield.earn] Open: https://higgsfield.ai/earn → paste link → submit
    [higgsfield.earn] Expected payout tier: 24h bonus (≥10k views reached)
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger("higgsfield.earn")

EARN_LOG_FILE = os.path.join(os.path.dirname(__file__), "earn_submissions.json")


def _load_submissions() -> list:
    if os.path.exists(EARN_LOG_FILE):
        try:
            with open(EARN_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_submissions(data: list) -> None:
    try:
        with open(EARN_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"[EARN] Failed to save submissions: {e}")


class HiggsfieldEarn:
    """
    Tracks Higgsfield Earn submissions and estimates payout tiers.

    Usage:
        earn = HiggsfieldEarn()
        earn.log_submission(
            post_url="https://instagram.com/p/xxxx",
            platform="instagram",
            higgsfield_video_path="Influencer_Output/higgsfield/Luna/hf_video_001.mp4",
            campaign_name="Fashion Week 2026",
        )
        earn.print_pending_submissions()
    """

    # Payout tier thresholds (approximate, based on Higgsfield Earn structure)
    PAYOUT_TIERS = [
        {"views": 0,      "label": "Base Pay",       "est_usd": "~$1–$5"},
        {"views": 10_000, "label": "10k Milestone",  "est_usd": "~$10–$30"},
        {"views": 50_000, "label": "50k Milestone",  "est_usd": "~$40–$100"},
        {"views": 100_000,"label": "100k Milestone", "est_usd": "~$100–$300"},
        {"views": 500_000,"label": "Viral Tier",     "est_usd": "~$500–$2,500"},
    ]

    def __init__(self):
        self.submissions = _load_submissions()

    def log_submission(
        self,
        post_url:              str,
        platform:              str,          # "instagram" | "youtube" | "telegram"
        higgsfield_video_path: str,
        campaign_name:         str  = "General",
        persona_name:          str  = "General",
        notes:                 str  = "",
    ) -> dict:
        """
        Log a posted video for Earn submission.
        Returns the submission record with manual action instructions.
        """
        record = {
            "id":                   f"earn_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp":            datetime.now().isoformat(),
            "post_url":             post_url,
            "platform":             platform,
            "higgsfield_video":     higgsfield_video_path,
            "campaign":             campaign_name,
            "persona":              persona_name,
            "notes":                notes,
            "submitted_to_earn":    False,
            "current_views":        0,
            "payout_tier_reached":  "Base Pay",
            "status":               "pending_manual_submission",
        }

        self.submissions.append(record)
        _save_submissions(self.submissions)

        logger.info(
            f"[HIGGSFIELD EARN] ✅ Post logged for Earn submission\n"
            f"   Platform: {platform} | URL: {post_url}\n"
            f"   ⚠️  MANUAL ACTION: Go to https://higgsfield.ai/earn → paste URL → submit"
        )

        return record

    def get_pending_submissions(self) -> list:
        """Return all submissions not yet manually submitted to Higgsfield Earn."""
        return [s for s in self.submissions if not s.get("submitted_to_earn")]

    def mark_submitted(self, submission_id: str, views: int = 0) -> None:
        """Mark a submission as completed and update view count."""
        for s in self.submissions:
            if s["id"] == submission_id:
                s["submitted_to_earn"] = True
                s["current_views"]     = views
                s["payout_tier_reached"] = self._get_tier(views)
                s["status"] = "submitted"
                break
        _save_submissions(self.submissions)
        logger.info(f"[HIGGSFIELD EARN] Marked submitted: {submission_id} | views={views:,}")

    def _get_tier(self, views: int) -> str:
        tier = "Base Pay"
        for t in self.PAYOUT_TIERS:
            if views >= t["views"]:
                tier = t["label"]
        return tier

    def print_pending_submissions(self) -> str:
        """Generate a summary of all pending manual Earn submissions."""
        pending = self.get_pending_submissions()
        if not pending:
            return "✅ No pending Higgsfield Earn submissions."

        lines = [
            f"⚠️  {len(pending)} Higgsfield Earn submission(s) require manual action:",
            "──────────────────────────────────────────",
        ]
        for i, s in enumerate(pending, 1):
            lines.append(
                f"{i}. [{s['platform']}] {s['post_url']}\n"
                f"   Campaign: {s['campaign']} | Posted: {s['timestamp'][:10]}\n"
                f"   → Submit at: https://higgsfield.ai/earn"
            )
        return "\n".join(lines)

    def earn_summary(self) -> str:
        """Full summary of all Earn submissions and estimated earnings."""
        total        = len(self.submissions)
        submitted    = sum(1 for s in self.submissions if s.get("submitted_to_earn"))
        pending      = total - submitted
        total_views  = sum(s.get("current_views", 0) for s in self.submissions)
        top_tier     = self._get_tier(max((s.get("current_views", 0) for s in self.submissions), default=0))

        lines = [
            "💰 Higgsfield Earn Summary",
            f"   Total submissions: {total}",
            f"   Submitted to Earn: {submitted}",
            f"   Pending manual:    {pending}",
            f"   Total tracked views: {total_views:,}",
            f"   Highest tier reached: {top_tier}",
            "",
            "📋 Payout Tiers Reference:",
        ]
        for t in self.PAYOUT_TIERS:
            lines.append(f"   {t['views']:>7,} views → {t['label']:20s} {t['est_usd']}")
        return "\n".join(lines)
