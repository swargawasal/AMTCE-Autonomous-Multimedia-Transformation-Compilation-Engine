import os
import re
import shutil
import glob

mapping = {
    'analytics_engine': 'Analytics_Modules',
    'creative_director': 'Content_Intelligence',
    'editor_brain': 'Intelligence_Modules',
    'editor_brain_v3': 'Intelligence_Modules',
    'editor_memory': 'Intelligence_Modules',
    'gemini_governor': 'Intelligence_Modules',
    'gemini_trace': 'Diagnostics_Modules',
    'generate_test_graph': 'tests',
    'learning_stability_gate': 'Core_Modules',
    'list_models': 'Utilities',
    'list_models_filtered': 'Utilities',
    'meaning_engine': 'Content_Intelligence',
    'memory_updater': 'Intelligence_Modules',
    'narrative_coherence_engine': 'Content_Intelligence',
    'orchestrator_soe_patch': 'Core_Modules',
    'pacing_engine': 'Content_Intelligence',
    'pattern_extractor': 'Intelligence_Modules',
    'perception_engine': 'Content_Intelligence',
    'persona_engine': 'Content_Intelligence',
    'pipeline_audit': 'Diagnostics_Modules',
    'pipeline_health_monitor': 'Diagnostics_Modules',
    'rag_main': 'rag',
    'refactor_all_remaining': 'scripts',
    'refactor_intelligence': 'scripts',
    'refactor_intelligence_v2': 'scripts',
    'retention_analyzer': 'Monetization_Metrics',
    'reward_scorer': 'Monetization_Metrics',
    'segment_safety_gate': 'Visual_Refinement_Modules',
    'segment_validator': 'Visual_Refinement_Modules',
    'self_optimizing_editor': 'Intelligence_Modules',
    'story_builder': 'Content_Intelligence',
    'strategy_optimizer': 'Intelligence_Modules',
    'style_validator': 'Visual_Refinement_Modules',
    'temporal_signal_builder': 'Core_Modules',
    'test_creative_director': 'tests',
    'test_fix_debug': 'tests',
    'test_motion_editor': 'tests',
    'test_onnx': 'tests',
    'test_scene_editor_pipeline': 'tests',
    'test_smart_scene': 'tests',
    'test_sync_v2': 'tests',
    'test_truncation_logic': 'tests',
    'verify_3call_resilience': 'tests/verification',
    'verify_escaping': 'tests/verification',
    'verify_governor': 'tests/verification',
    'verify_layout': 'tests/verification',
    'verify_timing': 'tests/verification',
    'video_log': 'Diagnostics_Modules',
    'compiler': 'Compiler_Modules'
}

def update_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return False

    original_content = content
    for mod, target in mapping.items():
        if target.startswith('tests') or target == 'scripts':
            folder_path = target.replace('/', '.')
        else:
            folder_path = target
        
        # import mod
        # import mod as x
        content = re.sub(
            r'^(\s*)import\s+' + re.escape(mod) + r'(\s+as\s+\w+)?(\s*)$',
            r'\1from ' + folder_path + r' import ' + mod + r'\2\3',
            content,
            flags=re.MULTILINE
        )
        
        # from mod import x
        content = re.sub(
            r'^(\s*)from\s+' + re.escape(mod) + r'\s+import\s+',
            r'\1from ' + folder_path + r'.' + mod + r' import ',
            content,
            flags=re.MULTILINE
        )

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated imports in {filepath}")
        return True
    return False

def main():
    all_py_files = glob.glob('**/*.py', recursive=True)
    
    # Exclude venv and hidden directories
    all_py_files = [f for f in all_py_files if 'venv\\' not in f and not f.startswith('.')]
    
    # Update imports
    for py_file in all_py_files:
        update_file(py_file)
        
    # Add __init__.py files
    folders = set(mapping.values())
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
        init_file = os.path.join(folder, '__init__.py')
        if not os.path.exists(init_file):
            with open(init_file, 'w', encoding='utf-8') as f:
                pass
                
    # Move files
    for mod, target in mapping.items():
        src = mod + '.py'
        if os.path.exists(src):
            dst = os.path.join(target, src)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            print(f"Moved {src} to {dst}")

if __name__ == '__main__':
    main()
