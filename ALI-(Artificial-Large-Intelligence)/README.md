# ALI (Artificial Large Intelligence) Engine

ALI is an autonomous, hybrid, multi-model intelligence engine designed to route tasks intelligently across different LLM superpowers (DeepSeek for logic, Gemini for vision/context, Qwen for multilingual/facts, Mistral for emotion/polish).

## Architecture
- **Superpower Router**: Classifies tasks and assigns a lead model.
- **Dual Safety Layer**: Async validation by Gemini Flash Lite (content safety) and Qwen (factual consistency).
- **Son of Anton**: Background reasoning loop running every 6 hours via GitHub Actions.
- **Graphify**: Generates AST, logic, and dependency graphs of reasoning paths for Gemini Vision to interpret.
- **Memory (Ruflow Brain)**: Persistent JSON storage of solved problems and safety logs.

## Setup
1. Clone the repo.
2. Run `pip install -r requirements.txt`.
3. Set your API keys in `.env` (copy `.env.example`).
4. Run tests with `pytest tests/`.

## Deployment
- The system is designed to run via **GitHub Actions** to bypass API rate limits and keep the PC safe.
- **`ali_server.yml`**: On-demand FastAPI server that spins up, creates a Cloudflare tunnel, writes the URL to `frontend/api_config.json`, and shuts down after 15 minutes of idle time.
- **`ali_loop.yml`**: Son of Anton cron job that runs every 6 hours and commits verified solutions back to `ruflow_brain/`.

## Local Sync
For Windows (Antigravity):
Run `scripts/local_sync.bat` to pull the latest GitHub-solved problems down to your local PC and create a local backup.
Run `scripts/antigravity_export.py` to format solutions for the Antigravity OS memory schema.
