# Graph Report - E:\ALI-(Artificial-Large-Intelligence)  (2026-06-09)

## Corpus Check
- 46 files · ~34,570 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 259 nodes · 347 edges · 24 communities detected
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 64 edges (avg confidence: 0.78)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]

## God Nodes (most connected - your core abstractions)
1. `GeminiGovernor` - 27 edges
2. `HFGovernor` - 16 edges
3. `call_gemini()` - 9 edges
4. `call_mistral()` - 8 edges
5. `call_hf()` - 8 edges
6. `get_user_profile()` - 8 edges
7. `execute_complex_chain()` - 7 edges
8. `chat_with_ali()` - 7 edges
9. `execute_vision_chain()` - 6 edges
10. `call_qwen()` - 6 edges

## Surprising Connections (you probably didn't know these)
- `call_mistral()` --calls--> `test_mistral_connector()`  [INFERRED]
  E:\ALI-(Artificial-Large-Intelligence)\connectors\mistral.py → E:\ALI-(Artificial-Large-Intelligence)\tests\test_connectors.py
- `vision_endpoint()` --calls--> `execute_vision_chain()`  [INFERRED]
  E:\ALI-(Artificial-Large-Intelligence)\api\main.py → E:\ALI-(Artificial-Large-Intelligence)\chains\vision_chain.py
- `ubi_profile_endpoint()` --calls--> `get_user_profile()`  [INFERRED]
  E:\ALI-(Artificial-Large-Intelligence)\api\main.py → E:\ALI-(Artificial-Large-Intelligence)\ubi\pattern_learner.py
- `ubi_profile_endpoint()` --calls--> `get_mse_stats()`  [INFERRED]
  E:\ALI-(Artificial-Large-Intelligence)\api\main.py → E:\ALI-(Artificial-Large-Intelligence)\ubi\mse_tracker.py
- `execute_complex_chain()` --calls--> `call_mistral()`  [INFERRED]
  E:\ALI-(Artificial-Large-Intelligence)\chains\reasoning_chain.py → E:\ALI-(Artificial-Large-Intelligence)\connectors\mistral.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.07
Nodes (17): GeminiGovernor, is_gemini_globally_down(), Check if the global circuit breaker is active., Public wrapper for prompt simplification used by the VANGUARD retry loop., Record a 5xx failure. Trip breaker if conditions met., [VANGUARD] Local Fallback to Ollama (Phi-3)., VANGUARD BULLETPROOF GENERATOR: Loop-based Retry + Global Deadline + Jitter., Reset the circuit breaker on a successful call. (+9 more)

### Community 1 - "Community 1"
Cohesion: 0.09
Nodes (18): HFGovernor, HFGovernor — Hugging Face Model Router v1.0 ====================================, Intelligent HuggingFace model router.      Selects the best available model per, Load all HF_TOKEN, HF_TOKEN_2, HF_TOKEN_3... from environment., Round-robin across the token pool., Decrement ban timers (call under state_lock)., Ban a model with tiered cooldown. Record per-task failure memory., Update running stats after a successful call. (+10 more)

### Community 2 - "Community 2"
Cohesion: 0.1
Nodes (15): # NOTE: safety is intentionally NOT a node — it runs as a FastAPI BackgroundTask, classify_task(), get_routing_plan(), detect_emotion(), call_mistral(), ALIState, emotion_node(), Phase 20 — User Behaviour Intelligence node.     Runs AFTER emotion, BEFORE rout (+7 more)

### Community 3 - "Community 3"
Cohesion: 0.16
Nodes (16): append_to_safety_log(), load_anton_solved(), _load_json(), load_knowledge_base(), load_solved_problems(), save_anton_solved(), _save_json(), save_knowledge_base() (+8 more)

### Community 4 - "Community 4"
Cohesion: 0.12
Nodes (11): adapt_prompt_with_emotion(), call_deepseek(), call_gemini(), check_safety_gemini(), vision_endpoint(), chain_node(), execute_complex_chain(), test_deepseek_connector() (+3 more)

### Community 5 - "Community 5"
Cohesion: 0.16
Nodes (17): _classify_kurtosis(), get_peak_hours(), _get_user(), get_user_profile(), _load_profiles(), UBI — Pattern Learner ===================== Computes kurtosis of a user's topic, Read the current profile for a user (no write)., Return the user's top-3 most active UTC hours. (+9 more)

### Community 6 - "Community 6"
Cohesion: 0.14
Nodes (8): run_anton_loop(), Pick the next unsolved, unattempted problem from the pool., select_problem(), anton_deep_reason(), Generates a visual graph of how Son of Anton navigated a reasoning path., visualize_anton_reasoning(), validate_solution(), generate_anton_visual()

### Community 7 - "Community 7"
Cohesion: 0.2
Nodes (13): classify_topic(), _cosine_distance(), _get_model(), measure_and_record(), predict(), _predict_next_topic(), UBI — Topic Predictor ===================== Predicts the user's next question to, Compute embedding distance between predicted and actual topic,     then forward (+5 more)

### Community 8 - "Community 8"
Cohesion: 0.18
Nodes (8): BaseModel, ChatRequest, Protected endpoint for external agents (like Antigravity) to sync or trigger act, Diagnostic endpoint: returns the current UBI profile for a session.     Useful f, ruflow_sync_endpoint(), TypingEvent, ubi_profile_endpoint(), VisionRequest

### Community 9 - "Community 9"
Cohesion: 0.35
Nodes (11): build_app(), _build_council_md(), _build_ubi_md(), chat_with_ali(), _check_backend(), _get_ubi_profile(), ALI - Gradio Chat Interface ============================ A premium dark-themed c, Main chat handler -- sends message to ALI and streams response. (+3 more)

### Community 10 - "Community 10"
Cohesion: 0.18
Nodes (11): Called by the frontend when the user starts typing.     For FOCUSED users, pre-f, typing_endpoint(), cancel_preload(), _fire_preload(), get_preloaded_answer(), UBI — Preloader =============== FOCUSED users only: pre-fires a cheap Gemini API, Called by chain_node before executing the full multi-LLM chain.      Validates t, Cancel any active preload for a session (e.g. when user clears input). (+3 more)

### Community 11 - "Community 11"
Cohesion: 0.36
Nodes (8): get_mse_stats(), _get_user(), _load_profiles(), UBI — MSE Tracker ================= Stores per-user MSE history in ruflow_brain/, Return current MSE stats for a user (read-only)., Called after every interaction to record the embedding distance between     the, record_prediction_error(), _save_profiles()

### Community 12 - "Community 12"
Cohesion: 0.6
Nodes (4): appendMessage(), checkStatus(), fetchConfig(), sendMessage()

### Community 13 - "Community 13"
Cohesion: 0.4
Nodes (0): 

### Community 14 - "Community 14"
Cohesion: 0.67
Nodes (2): export_graph_to_base64(), Reads a generated graph image and converts it to base64 for Gemini Vision.

### Community 15 - "Community 15"
Cohesion: 0.67
Nodes (1): ALI Engine -- Live LLM Ping Test Tests all 4 LLMs with a real API call and print

### Community 16 - "Community 16"
Cohesion: 1.0
Nodes (0): 

### Community 17 - "Community 17"
Cohesion: 1.0
Nodes (0): 

### Community 18 - "Community 18"
Cohesion: 1.0
Nodes (0): 

### Community 19 - "Community 19"
Cohesion: 1.0
Nodes (0): 

### Community 20 - "Community 20"
Cohesion: 1.0
Nodes (0): 

### Community 21 - "Community 21"
Cohesion: 1.0
Nodes (0): 

### Community 22 - "Community 22"
Cohesion: 1.0
Nodes (0): 

### Community 23 - "Community 23"
Cohesion: 1.0
Nodes (1): Main chat handler -- sends message to ALI and streams response.

## Knowledge Gaps
- **59 isolated node(s):** `Protected endpoint for external agents (like Antigravity) to sync or trigger act`, `Called by the frontend when the user starts typing.     For FOCUSED users, pre-f`, `Diagnostic endpoint: returns the current UBI profile for a session.     Useful f`, `Takes multiple raw answers (e.g., from different models) and synthesizes them us`, `Check if the global circuit breaker is active.` (+54 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 16`** (2 nodes): `call_cerebras()`, `cerebras_connector.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 17`** (2 nodes): `groq_connector.py`, `call_groq()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 18`** (2 nodes): `generate_ast_graph()`, `ast_generator.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 19`** (2 nodes): `generate_dependency_graph()`, `dependency_graph.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 20`** (2 nodes): `logic_flow.py`, `generate_flow_graph()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 21`** (2 nodes): `export_to_antigravity()`, `antigravity_export.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 22`** (1 nodes): `__init__.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 23`** (1 nodes): `Main chat handler -- sends message to ALI and streams response.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `call_gemini()` connect `Community 4` to `Community 0`, `Community 1`, `Community 6`?**
  _High betweenness centrality (0.356) - this node is a cross-community bridge._
- **Why does `execute_vision_chain()` connect `Community 4` to `Community 2`?**
  _High betweenness centrality (0.280) - this node is a cross-community bridge._
- **Why does `vision_endpoint()` connect `Community 4` to `Community 8`?**
  _High betweenness centrality (0.249) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `HFGovernor` (e.g. with `HuggingFace Connector — Governor-Aware ======================================= R` and `Intelligent HuggingFace call with automatic model rotation.      Routing logic (`) actually correct?**
  _`HFGovernor` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `call_gemini()` (e.g. with `execute_complex_chain()` and `execute_vision_chain()`) actually correct?**
  _`call_gemini()` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `call_mistral()` (e.g. with `execute_complex_chain()` and `execute_synthesis_chain()`) actually correct?**
  _`call_mistral()` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `call_hf()` (e.g. with `HFGovernor` and `.get_available_model()`) actually correct?**
  _`call_hf()` has 5 INFERRED edges - model-reasoned connections that need verification._