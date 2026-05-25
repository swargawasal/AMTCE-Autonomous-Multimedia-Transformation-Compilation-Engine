import os, shutil

dst = r"D:\AMTCE-short-form-editor-only"

# ============================================================
# WHAT WE KEEP — The pure editing backbone
# ============================================================
KEEP_DIRS = {
    # Core editing phases (already sorted)
    "Phase_1_Cleansing",
    "Phase_2_Signal_Extraction",
    "Phase_3_Cognitive_Editing",
    "Phase_4_Reconstruction",
    "Bonus_Reaction_Engine",  # will be individually filtered
    
    # Backend engines needed by the above
    "Audio_Modules",
    "Video_Modules",
    "Compiler_Modules",
    "Content_Intelligence",
    "Visual_Refinement_Modules",
    "Intelligence_Modules",
    "Core_Modules",
    "Diagnostics_Modules",
    "Health_handlers",
    "Utilities",
    "The_json",
    "data",
    "config",
    "music",
    "assets",
}

# Files to KEEP per folder (surgical - only what editing needs)
KEEP_FILES = {
    "Audio_Modules": {
        "beat_engine.py", "audio_processing.py", "audio_beat_detector.py",
        "audio_pool_manager.py", "music_manager.py", "music_intelligence.py",
        "lyric_rhythm_aligner.py", "speech_to_text.py", "__init__.py"
    },
    "Video_Modules": {
        "timeline_cutter.py", "trimmer.py"
    },
    "Compiler_Modules": {
        "rhythm_timeline_builder.py", "creative_editor_bridge.py",
        "transition_intelligence_engine.py", "music_driven_editor.py",
        "smart_transition_engine.py", "video_pipeline.py", "compiler.py",
        "audio_pipeline.py", "editor_retry_engine.py", "pipeline_context.py",
        "anchors.py", "story_director.py", "speed_engine.py",
        "batch_factory.py", "__init__.py"
    },
    "Content_Intelligence": {
        "emotional_spike_detector.py", "expression_change_engine.py",
        "retention_curve_engine.py", "signal_fusion_engine.py", "hook_engine.py",
        "hook_variants.py", "content_intelligence_engine.py",
        "signal_repair_layer.py", "universal_content_director.py",
        "creative_director.py", "persona_engine.py", "transformation_engine.py",
        "pacing_engine.py", "perception_engine.py", "meaning_engine.py",
        "feature_flag_controller.py", "source_detector.py",
        "narrative_coherence_engine.py", "story_builder.py", "__init__.py"
    },
    "Visual_Refinement_Modules": {
        "moment_miner.py", "motion_detector.py", "subject_tracker.py",
        "timeline_reconstructor.py", "signal_fusion_engine.py",
        "rhythm_quality_guard.py", "segment_safety_gate.py",
        "scene_detector.py", "smart_scene_editor.py",
        "segment_validator.py", "moment_selector.py",
        "effect_assigner.py", "render_applier.py",
        "timeline_builder.py", "compute_caps.py",
        "import_gate.py", "Heavy_import_gate.py",
        "quality_orchestrator.py", "__init__.py"
    },
    "Intelligence_Modules": {
        "unified_intelligence.py", "creative_brain.py", "pacing_architect.py",
        "energy_scorer.py", "editor_brain.py", "editor_brain_v3.py",
        "gemini_governor.py", "gemini_status_manager.py", "deepseek_router.py",
        "decision_engine.py", "cinematic_duration_engine.py", "editing_plan.py",
        "adaptive_intelligence.py", "semantic_editor.py", "scene_intel.py",
        "deduplication.py", "forensic_analyzer.py", "shot_detector.py",
        "pattern_extractor.py", "quality_evaluator.py", "risk_engine.py",
        "vision_intelligence.py", "content_brain.py", "narrative_brain.py",
        "router_orchestra.py", "smart_reuse_engine.py", "generator.py",
        "nzt_simulation_loop.py", "learning_stability_gate.py", "__init__.py"
    },
    "Core_Modules": {
        "editor_memory.py", "memory_updater.py", "pipeline_context.py",
        "segment_validator.py", "self_optimizing_editor.py", "story_builder.py",
        "pacing_engine.py", "perception_engine.py", "meaning_engine.py",
        "narrative_coherence_engine.py", "retention_analyzer.py",
        "reward_scorer.py", "temporal_signal_builder.py",
        "strategy_optimizer.py", "analytics_engine.py",
        "orchestrator_soe_patch.py", "pattern_extractor.py",
        "video_log.py", "learning_stability_gate.py", "__init__.py"
    },
    "Diagnostics_Modules": {
        "gemini_trace.py", "pipeline_feature_auditor.py",
        "pipeline_health_monitor.py", "__init__.py"
    },
    "Health_handlers": {
        "necessary_import_gate.py", "health.py", "__init__.py"
    },
    "Utilities": None,  # keep all
    "Phase_1_Cleansing": None,   # keep all (already filtered)
    "Phase_2_Signal_Extraction": None,
    "Phase_3_Cognitive_Editing": None,
    "Phase_4_Reconstruction": None,
    "Bonus_Reaction_Engine": None,
    "The_json": None,  # keep all json states
    "data": None,
    "config": None,
    "music": None,
    "assets": None,
}

# ============================================================
# FOLDERS TO COMPLETELY DELETE (noise — not editing)
# ============================================================
DELETE_DIRS = [
    "Uploader_Modules",
    "Actress_Modules",
    "Monetization_Metrics",
    "Analytics_Modules",
    "Download_Modules",
    "Higgsfield_Modules",
    "Influencer_Modules",
    "Thumb_Modules",
    "Text_Modules",
    "Trend_Intelligence",
    "Upscale_Modules",
    "Compilation_Modules",
    "First_Shot_Engine",
    "First_Shots",
    "Swarm_Orchestration",
    "Datasets_and_text_files",
    "rag",
    "claw_vanguard",
    "Reaction_Engine",       # full reaction engine not needed
    "Bonus_Reaction_Engine", # basic reaction also not core editing
    "analyzer",
    "decision",
    "Diagnostics",
    "setup_modules",
    "Backend_Audio_Managers",
    "Backend_LLM_APIs",
    "Backend_Memory_State",
    "Backend_Misc_Dependencies",
    "Backend_Orchestrator",
    "Backend_SubDependencies",
    "Backend_Diagnostics",
    "Backend_Video_Utils",
]

# Root-level files to delete
DELETE_ROOT_FILES = [
    "analytics_cache.json",
    "publish_queue.json",
]

deleted_dirs = []
deleted_files = []

for d in DELETE_DIRS:
    path = os.path.join(dst, d)
    if os.path.exists(path):
        shutil.rmtree(path)
        deleted_dirs.append(d)
        print(f"  [DELETED DIR] {d}")

for f in DELETE_ROOT_FILES:
    path = os.path.join(dst, f)
    if os.path.exists(path):
        os.remove(path)
        deleted_files.append(f)
        print(f"  [DELETED FILE] {f}")

# Now surgical file-level cleanup per folder
print("\n--- Surgical file cleanup ---")
for folder, keep_set in KEEP_FILES.items():
    if keep_set is None:
        continue  # keep everything in this folder
    folder_path = os.path.join(dst, folder)
    if not os.path.isdir(folder_path):
        continue
    for fname in os.listdir(folder_path):
        fpath = os.path.join(folder_path, fname)
        if os.path.isfile(fpath) and fname not in keep_set:
            os.remove(fpath)
            deleted_files.append(f"{folder}/{fname}")
            print(f"  [DELETED FILE] {folder}/{fname}")

print(f"\n=== DONE ===")
print(f"Deleted {len(deleted_dirs)} directories")
print(f"Deleted {len(deleted_files)} files")
print(f"\nRemaining structure:")
for item in sorted(os.listdir(dst)):
    item_path = os.path.join(dst, item)
    if os.path.isdir(item_path):
        count = sum(len(files) for _, _, files in os.walk(item_path))
        print(f"  [DIR]  {item}/ ({count} files)")
    else:
        print(f"  [FILE] {item}")
