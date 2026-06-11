import graphviz
from typing import List, Dict, Optional

def generate_flow_graph(steps: List[Dict[str, str]], output_path: str = "logic_flow") -> Optional[str]:
    dot = graphviz.Digraph(comment='Execution Logic Flow')
    
    for i, step in enumerate(steps):
        node_id = f"step_{i}"
        label = f"{step.get('action', 'Action')}\n{step.get('detail', '')}"
        dot.node(node_id, label, shape="box")
        
        if i > 0:
            dot.edge(f"step_{i-1}", node_id)
            
    try:
        dot.render(output_path, format="png", cleanup=True)
        return output_path + ".png"
    except Exception as e:
        print(f"Graphviz error: {e}")
        return None
