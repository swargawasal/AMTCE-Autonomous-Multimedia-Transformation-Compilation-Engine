import os
import sys
import subprocess
import importlib.metadata
import logging
import urllib.request

logger = logging.getLogger("deps_installer")

def install_missing_dependencies():
    """
    Checks watermark_requirements.txt and installs any missing packages.
    """
    req_file = os.path.join(os.path.dirname(__file__), "watermark_requirements.txt")
    if not os.path.exists(req_file):
        logger.warning(f"⚠️ {req_file} not found. Skipping dependency check.")
        return

    with open(req_file, 'r') as f:
        requirements = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    missing = []
    for req in requirements:
        # Simple extraction of package name (handles basic cases)
        pkg_name = req.split('==')[0].split('>=')[0].split('<')[0].strip()
        try:
            importlib.metadata.version(pkg_name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(req)

    if missing:
        logger.info(f"📦 Found {len(missing)} missing dependencies. Installing...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
            logger.info("✅ All dependencies installed successfully.")
        except Exception as e:
            logger.error(f"❌ Failed to install dependencies: {e}")

def check_models_presence():
    """
    Ensures the 'models' directory and files are present.
    If missing, provides a warning or attempts to download (stub).
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    models_dir = os.path.join(base_dir, "models")
    
    proto_file = os.path.join(models_dir, "deploy.prototxt")
    model_file = os.path.join(models_dir, "res10_300x300_ssd_iter_140000.caffemodel")

    if not os.path.exists(models_dir):
        logger.info("📂 'models' directory missing. Creating...")
        os.makedirs(models_dir, exist_ok=True)

    missing_files = []
    if not os.path.exists(proto_file): missing_files.append("deploy.prototxt")
    if not os.path.exists(model_file): missing_files.append("res10_300x300_ssd_iter_140000.caffemodel")

    # LaMa Model Check (Unified models folder) removed.
    # We now use OpenCV Inpainting (Standard) which requires no external model files.
    logger.info("💎 Hybrid-Standard Mode Active (OpenCV). No heavy models required.")

    if missing_files:
        logger.warning(f"⚠️ DNN Models missing: {', '.join(missing_files)}")
        # Note: In a production scenario, we would host these somewhere and use urllib to download.
        # For now, we simply alert the user to ensure they are present.
        logger.info("ℹ️ Please ensure the DNN models are placed in the 'Visual_Refinement_Modules/models' folder.")

def run_setup():
    """Main entry point for setup."""
    logger.info("🛠️ Running Auto-Setup Check...")
    install_missing_dependencies()
    check_models_presence()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_setup()
