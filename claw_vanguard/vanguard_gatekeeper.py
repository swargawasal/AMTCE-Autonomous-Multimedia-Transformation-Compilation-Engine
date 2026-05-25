import os
import json
import time
import sys
from typing import List, Dict, Any
from claw_vanguard.vanguard_forge import vanguard_forge

class VanguardGatekeeper:
    """
    The Human-in-the-Loop Judge CLI.
    Ensures that AI optimizations are only promoted after manual validation.
    """
    
    def __init__(self):
        self.history_dir = "logs/forge_history"
        
    def list_proposals(self) -> List[Dict[str, Any]]:
        """Find non-stale proposals in the history."""
        if not os.path.exists(self.history_dir):
            return []
            
        proposals = []
        now = time.time()
        for f in os.listdir(self.history_dir):
            if f.endswith(".json"):
                path = os.path.join(self.history_dir, f)
                with open(path, 'r', encoding='utf-8') as j:
                    data = json.load(j)
                    # Check for Stale (24h)
                    age_hours = (now - data.get("timestamp", 0)) / 3600
                    data["stale"] = age_hours > 24
                    data["age_hours"] = round(age_hours, 1)
                    proposals.append(data)
        
        # Sort by timestamp (newest first)
        proposals.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return proposals

    def run(self):
        """Main CLI Loop."""
        print("\n" + "═"*60)
        print("🛡️  VANGUARD GATEKEEPER: THE JUDGE")
        print("═"*60)
        
        while True:
            proposals = self.list_proposals()
            if not proposals:
                print("\nℹ️  No pending proposals found.")
                print("[Q]uit | [R]ollback existing file")
                choice = input("\n> ").lower()
                if choice == 'q': break
                if choice == 'r': self.handle_rollback()
                continue
                
            print(f"\n🔍 Found {len(proposals)} pending optimization proposals:\n")
            for i, p in enumerate(proposals):
                status = "[!] STALE" if p["stale"] else "[NEW]"
                print(f"{i+1}. {status} {p['target_file']} (Age: {p['age_hours']}h) | Score: {p['scores']['auditor']}/1.0")

            print("\n" + "-"*30)
            print("Select # to Review | [R]ollback | [Q]uit")
            choice = input("\n> ").lower()
            
            if choice == 'q': break
            if choice == 'r': self.handle_rollback()
            
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(proposals):
                    self.review_proposal(proposals[idx])
            except ValueError:
                continue

    def handle_rollback(self):
        """Rollback logic."""
        target = input("\nEnter file path to rollback (e.g. Intelligence_Modules/tool_system.py): ")
        if os.path.exists(target):
            success = vanguard_forge.rollback(target)
            if success:
                print(f"✅ Successfully rolled back {target} to latest backup.")
            else:
                print(f"❌ Rollback failed.")
        else:
            print(f"❌ File {target} not found.")

    def review_proposal(self, p: Dict[str, Any]):
        """Review Flow: Summary -> Diff? -> Decision."""
        print("\n" + "═"*60)
        print(f"📄 REVIEWING: {p['target_file']}")
        print(f"🆔 ID: {p['proposal_id']}")
        print("═"*60)
        
        # 1. Summary
        print(f"\n🧠 AI Audit Score: {p['scores']['auditor']}/1.0")
        print(f"📊 Consensus Score: {p['scores']['total']}/3.0")
        print(f"🛡️  Risk Level: {p['risk_level']}")
        print(f"\n💬 Auditor Critique:\n{p['audit'].get('critique', 'No critique available.')}")
        
        risks = p['audit'].get('risks', [])
        if risks:
            print("\n🚨 Identified Risks:")
            for r in risks:
                print(f"  - [{r['severity'].upper()}] {r['type']}")

        # 2. View Diff?
        print("\n" + "-"*30)
        view_diff = input("View Full Code Diff? [y/N]: ").lower()
        if view_diff == 'y':
            print("\n" + "📄 DIFF START " + "-"*40)
            print(p['diff'])
            print("-" * 55)

        # 3. Decision
        print("\nDECISION:")
        print("[A]pprove & Promote | [D]iscard | [B]ack to list")
        decision = input("\n> ").lower()
        
        if decision == 'a':
            self.promote_proposal(p)
        elif decision == 'd':
            self.discard_proposal(p)

    def promote_proposal(self, p: Dict[str, Any]):
        """Atomic Promotion."""
        print(f"\n🚀 Promoting {p['proposal_id']} to {p['target_file']}...")
        
        forge_content = p.get("full_code")
        if not forge_content:
            print("❌ ERROR: Full code missing in proposal JSON. Cannot promote.")
            return

        snapshot = vanguard_forge.promote_to_primary(p["target_file"], forge_content)
        print(f"✅ SUCCESS: File updated and atomically swapped.")
        print(f"📁 Backup stored at: {snapshot}")
        
        # Cleanup
        self.discard_proposal(p)
        input("\nPress Enter to continue...")

    def discard_proposal(self, p: Dict[str, Any]):
        """Remove the proposal JSON."""
        path = os.path.join(self.history_dir, f"{p['proposal_id']}.json")
        if os.path.exists(path):
            os.remove(path)
            print(f"🗑️  Proposal {p['proposal_id']} removed from history.")

if __name__ == "__main__":
    gatekeeper = VanguardGatekeeper()
    gatekeeper.run()
