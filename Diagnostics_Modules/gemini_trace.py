import time
from datetime import datetime

class GeminiTrace:

    @staticmethod
    def log_start_extended(task_type, module, model, input_size):
        print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"🔥 GEMINI CALL START [{task_type.upper()}]")
        print(f"⏰ Time: {datetime.now()}")
        print(f"📦 Model: {model}")
        print(f"📍 Module: {module}")
        print(f"📊 Input Size: {input_size} chars")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        return time.time()

    @staticmethod
    def log_end_extended(start_time, cache_hit=False):
        duration = time.time() - start_time
        print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"✅ GEMINI CALL END {'(CACHED)' if cache_hit else ''}")
        print(f"⏱ Duration: {duration:.2f}s")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    @staticmethod
    def log_event(task_type, module, model, input_size, cache_hit=False):
        """Quick log for non-API events (like cache hits)"""
        print(f"✨ [GEMINI EVENT] Task: {task_type} | Module: {module} | Cache Hit: {cache_hit} | Model: {model}")

    @staticmethod
    def print_summary():
        # This is now handled by gemini_router.print_usage_report()
        # but kept for compatibility.
        from Intelligence_Modules.gemini_governor import gemini_router
        gemini_router.print_usage_report()

    @staticmethod
    def log_start(model, args, kwargs):
        # Deprecated: usage should move to gemini_router
        return time.time()

    @staticmethod
    def log_end(start_time):
        # Deprecated: usage should move to gemini_router
        pass
