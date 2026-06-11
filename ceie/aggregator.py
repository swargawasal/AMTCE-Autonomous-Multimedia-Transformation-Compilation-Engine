"""
ceie/aggregator.py
------------------
Aggregates chunk-relative edit blueprints into a single, global-timestamped master plan.
Flattens the timeline into sorted lists of operations for easy consumption by the applicator.
"""

from copy import deepcopy
from typing import Dict, List, Any
from ceie.models.edit_schema import MasterEditPlan

def globalize_plan(master_plan: MasterEditPlan) -> MasterEditPlan:
    """
    Returns a new MasterEditPlan where all timestamps in all chunks
    have been shifted by the chunk's start offset to be global.
    """
    plan = deepcopy(master_plan)
    
    for chunk in plan.chunks:
        offset = chunk.chunk_start_sec
        
        # 1. Cuts
        for cut in chunk.cuts:
            cut.at_sec += offset
            
        # 2. Trims
        for trim in chunk.trims:
            trim.start_sec += offset
            trim.end_sec += offset
            
        # 3. Speed Ramps
        for ramp in chunk.speed_ramps:
            ramp.start_sec += offset
            ramp.end_sec += offset
            
        # 4. Transitions
        for trans in chunk.transitions:
            trans.at_sec += offset
            
        # 5. Text Overlays
        for overlay in chunk.text_overlays:
            overlay.at_sec += offset
            
        # 6. Karaoke
        for kara in chunk.karaoke_segments:
            kara.start_sec += offset
            kara.end_sec += offset
            
        # 7. Voiceovers
        for vo in chunk.voiceover_segments:
            vo.insert_at_sec += offset
            
        # 8. Zoom Focus
        for zoom in chunk.zoom_focus:
            zoom.at_sec += offset
            
    return plan

def flatten_timeline(global_plan: MasterEditPlan) -> Dict[str, List[Any]]:
    """
    Flattens a globalized MasterEditPlan into a single dict of chronological operations.
    """
    timeline = {
        "cuts": [],
        "trims": [],
        "speed_ramps": [],
        "transitions": [],
        "text_overlays": [],
        "karaoke_segments": [],
        "voiceover_segments": [],
        "zoom_focus": []
    }
    
    for chunk in global_plan.chunks:
        timeline["cuts"].extend([c.model_dump() for c in chunk.cuts])
        timeline["trims"].extend([t.model_dump() for t in chunk.trims])
        timeline["speed_ramps"].extend([s.model_dump() for s in chunk.speed_ramps])
        timeline["transitions"].extend([tr.model_dump() for tr in chunk.transitions])
        timeline["text_overlays"].extend([o.model_dump() for o in chunk.text_overlays])
        timeline["karaoke_segments"].extend([k.model_dump() for k in chunk.karaoke_segments])
        timeline["voiceover_segments"].extend([v.model_dump() for v in chunk.voiceover_segments])
        timeline["zoom_focus"].extend([z.model_dump() for z in chunk.zoom_focus])
        
    # Sort chronologically by timestamp/start time
    timeline["cuts"].sort(key=lambda x: x["at_sec"])
    timeline["trims"].sort(key=lambda x: x["start_sec"])
    timeline["speed_ramps"].sort(key=lambda x: x["start_sec"])
    timeline["transitions"].sort(key=lambda x: x["at_sec"])
    timeline["text_overlays"].sort(key=lambda x: x["at_sec"])
    timeline["karaoke_segments"].sort(key=lambda x: x["start_sec"])
    timeline["voiceover_segments"].sort(key=lambda x: x["insert_at_sec"])
    timeline["zoom_focus"].sort(key=lambda x: x["at_sec"])
    
    return timeline
