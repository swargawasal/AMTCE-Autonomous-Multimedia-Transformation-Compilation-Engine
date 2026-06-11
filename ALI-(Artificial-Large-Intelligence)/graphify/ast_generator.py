import ast
import graphviz
from typing import Optional

def generate_ast_graph(code: str, output_path: str = "ast_graph") -> Optional[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
        
    dot = graphviz.Digraph(comment='AST Graph')
    
    def add_nodes(node, parent_id=None):
        node_id = str(id(node))
        label = type(node).__name__
        
        # Add a bit more detail for some nodes
        if isinstance(node, ast.FunctionDef):
            label += f"({node.name})"
        elif isinstance(node, ast.Name):
            label += f"({node.id})"
            
        dot.node(node_id, label)
        
        if parent_id is not None:
            dot.edge(parent_id, node_id)
            
        for child in ast.iter_child_nodes(node):
            add_nodes(child, node_id)
            
    add_nodes(tree)
    
    try:
        dot.render(output_path, format="png", cleanup=True)
        return output_path + ".png"
    except Exception as e:
        print(f"Graphviz error: {e}")
        return None
