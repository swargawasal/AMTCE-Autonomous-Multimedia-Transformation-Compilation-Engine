import graphviz
from typing import Dict, List, Optional

def generate_dependency_graph(dependencies: Dict[str, List[str]], output_path: str = "dependency_graph") -> Optional[str]:
    dot = graphviz.Digraph(comment='Dependency Graph')
    
    for module, deps in dependencies.items():
        dot.node(module, module)
        for dep in deps:
            dot.node(dep, dep)
            dot.edge(module, dep)
            
    try:
        dot.render(output_path, format="png", cleanup=True)
        return output_path + ".png"
    except Exception as e:
        print(f"Graphviz error: {e}")
        return None
