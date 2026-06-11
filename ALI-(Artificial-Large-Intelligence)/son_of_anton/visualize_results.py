from graphify.reasoning_visualizer import visualize_anton_reasoning

def generate_anton_visual(problem: str):
    path = ["DeepSeek (Brute Force)", "Gemini (Validation)"]
    visualize_anton_reasoning(problem, path, "anton_reasoning_latest")
    print("Generated visual map of Anton's reasoning in anton_reasoning_latest.png")
