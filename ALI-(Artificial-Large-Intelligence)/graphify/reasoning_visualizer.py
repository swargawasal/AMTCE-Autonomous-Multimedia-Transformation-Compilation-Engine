import graphviz
from typing import List, Optional

def visualize_anton_reasoning(problem: str, path: List[str], output_path: str = "reasoning_diff") -> Optional[str]:
    """Generates a visual graph of how Son of Anton navigated a reasoning path."""
    dot = graphviz.Digraph(comment='Son of Anton Reasoning Path')
    
    dot.node("problem", f"Problem:\n{problem[:50]}...", shape="box", style="filled", fillcolor="lightgrey")
    
    prev_node = "problem"
    for i, model in enumerate(path):
        node_id = f"step_{i}_{model}"
        dot.node(node_id, f"{model} Analysis")
        dot.edge(prev_node, node_id)
        prev_node = node_id
        
    dot.node("solution", "Verified Solution", shape="doubleoctagon", style="filled", fillcolor="lightgreen")
    dot.edge(prev_node, "solution")
    
    try:
        dot.render(output_path, format="png", cleanup=True)
        return output_path + ".png"
    except Exception as e:
        print(f"Graphviz error: {e}")
        return None
