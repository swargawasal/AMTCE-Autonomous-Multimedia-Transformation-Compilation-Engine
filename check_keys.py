import sys, os
sys.path.insert(0, r"D:\AMTCE-short-form-editor-only")
os.chdir(r"D:\AMTCE-short-form-editor-only")

# Check what keys each engine READS from profile_data
import re

files_to_check = {
    "signal_fusion_engine": r"D:\AMTCE-short-form-editor-only\Content_Intelligence\signal_fusion_engine.py",
    "moment_miner": r"D:\AMTCE-short-form-editor-only\Visual_Refinement_Modules\moment_miner.py",
    "emotional_spike": r"D:\AMTCE-short-form-editor-only\Content_Intelligence\emotional_spike_detector.py",
    "retention_curve": r"D:\AMTCE-short-form-editor-only\Content_Intelligence\retention_curve_engine.py",
    "motion_detector": r"D:\AMTCE-short-form-editor-only\Visual_Refinement_Modules\motion_detector.py",
    "beat_engine": r"D:\AMTCE-short-form-editor-only\Audio_Modules\beat_engine.py",
}

for name, path in files_to_check.items():
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # Find profile_data["key"] or profile_data.get("key") patterns
    keys = re.findall(r'profile_data\[[\'"]([\w_]+)[\'"]\]', content)
    keys += re.findall(r'profile_data\.get\([\'"]([\w_]+)[\'"]', content)
    keys = sorted(set(keys))
    print(f"\n{name}:")
    for k in keys:
        print(f"  reads: profile_data['{k}']")
