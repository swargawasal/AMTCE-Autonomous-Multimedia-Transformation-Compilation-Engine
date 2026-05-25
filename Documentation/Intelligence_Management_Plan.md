# 🏛️ AMTCE Intelligence Management Plan

This document outlines the strategic utilization of LLM APIs to ensure maximum reasoning power while maintaining strict quota safety and cost efficiency.

## 🧠 Tier 1: The Tactical Swarm (Daily Operations)
- **Primary Model:** `mistral/mistral-large-latest` (via Groq/Mistral)
- **Usage:** Daily swarm coordination, directory management, viral blueprinting, and task delegation.
- **Quota Logic:** High limits allow for expansive, iterative reasoning without exhausting core video processing tokens.

## 👁️ Tier 2: The Visual Specialists (Core Processing)
- **Primary Model:** `gemini-2.5-flash-lite` (via Gemini Governor)
- **Usage:** Forensic analysis, watermark detection, visual refinement, and subject-aware cropping.
- **Quota Logic:** Strictly managed by the `GeminiGovernor`. Daily limits (20-100) are reserved for frame-by-frame intelligence.

## 💎 Tier 3: The Master Architect (Emergency/Critical)
- **Primary Model:** `deepseek-reasoner` (The "Vault")
- **Usage:** Complex architectural refactoring, debugging recursive logic failures, or deep-dive security audits.
- **Quota Logic:** **5M Lifetime Tokens.** 
- **Deployment Protocol:** ONLY used if Tier 1 and Tier 2 fail to resolve a logical blocker. Must be manually unlocked in `multimedia_crew.py`.

## 📁 Organizational Standard
- **No Dirty Roots:** All new modules must reside in dedicated subfolders (e.g., `Swarm_Orchestration/`, `Intelligence_Modules/`).
- **Path Awareness:** All scripts must use relative pathing to ensure portability for GitHub and app deployment.
