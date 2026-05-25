
import os
import json
import logging
import datetime
from typing import Optional, Dict, Any
from googleapiclient.discovery import build
from Intelligence_Modules.gemini_governor import gemini_router
from Uploader_Modules.uploader import get_valid_credentials

logger = logging.getLogger("analytics_optimizer")

# Configuration
CACHE_FILE = "analytics_cache.json"
CACHE_DURATION_DAYS = 7  # Weekly refresh cycle for analytics patterns

class AnalyticsOptimizer:
    def __init__(self):
        self.router = gemini_router
        self.gemini_available = True if gemini_router else False
            
        self.cache = self._load_cache()

    def _load_cache(self) -> Dict[str, Any]:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cache(self, data: Dict[str, Any]):
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"❌ Failed to save analytics cache: {e}")

    def get_analytics_service(self):
        """Builds the YouTube Analytics API service."""
        try:
            creds = get_valid_credentials()
            if not creds:
                logger.error("❌ No valid credentials available for Analytics.")
                return None
            return build("youtubeAnalytics", "v2", credentials=creds)
        except Exception as e:
            logger.error(f"❌ Failed to build Analytics service: {e}")
            return None

    def fetch_viewer_data(self) -> Optional[str]:
        """
        Fetches channel view statistics organized by day of week and hour.
        Returns a formatted string summary of the data.
        """
        service = self.get_analytics_service()
        if not service:
            return None

        try:
            # Query last 90 days for better statistical significance
            end_date = datetime.date.today().strftime("%Y-%m-%d")
            start_date = (datetime.date.today() - datetime.timedelta(days=90)).strftime("%Y-%m-%d")

            logger.info(f"📊 Fetching YouTube Analytics data from {start_date} to {end_date}...")

            # We want to know when people are watching: simple views metric
            # Dimensions: day (YYYY-MM-DD). 'hour' and 'dayOfWeek' are NOT supported in v2 standard reports.
            request = service.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views",
                dimensions="day",
                sort="day"
            )
            response = request.execute()

            rows = response.get("rows", [])
            if not rows:
                logger.warning("⚠️ No analytics data found (new channel?).")
                return None
            
            # Aggregate by dayOfWeek manually
            # Data structure: { weekday_index: total_views }
            aggregated = {}
            for row in rows:
                # row format: [date_string "YYYY-MM-DD", views_int]
                date_str = row[0]
                views = int(row[1])
                
                # Get weekday index (0=Monday, 6=Sunday)
                try:
                    y, m, d = map(int, date_str.split('-'))
                    dt = datetime.date(y, m, d)
                    weekday = dt.weekday()
                    aggregated[weekday] = aggregated.get(weekday, 0) + views
                except Exception:
                    continue
            
            # Format for Gemini
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            
            summary = "Recent Viewership Data (Aggregated by Day of Week):\n"
            # Sort by weekday for a clean list
            for i in range(7):
                views = aggregated.get(i, 0)
                summary += f"- {days[i]}: {views} total views\n"
            
            return summary

        except Exception as e:
            logger.error(f"❌ Analytics API Error: {e}")
            return None

    def analyze_with_gemini(self, analytics_summary: str) -> Optional[Dict[str, Any]]:
        """
        Asks Gemini to pick the best day and suggest an hour based on niche heuristics.
        """
        if not self.gemini_available:
            return None

        prompt = (
            "You are a YouTube Growth Strategist. Analyze the following viewership data (Day-level only).\n"
            "NOTE: Hourly data is unavailable from the API. You must use niche heuristics (e.g., Prime Time 18:00-21:00) to recommend the hour.\n\n"
            f"DATA:\n{analytics_summary}\n\n"
            "STRATEGY: Pick the strongest day. If the niche is Fashion/Lifestyle, peak viewing is usually 19:00-20:00 local time.\n"
            "TASK: Return a JSON object with the recommended upload day and hour (24h format).\n"
            "FORMAT: { \"day\": \"Monday\", \"hour\": 18, \"reason\": \"Monday is the peak day; 18:00 is prime time for fashion indexing.\" }\n"
            "Return ONLY the JSON."
        )

        try:
            logger.info("🧠 Sending Analytics data to Gemini for optimization...")
            res_txt = self.router.generate(task_type="analytics", prompt=prompt, module_name="analytics_optimizer")
            if not res_txt: return None
            text = res_txt.strip().replace("```json", "").replace("```", "")
            data = json.loads(text)
            return data
        except Exception as e:
            logger.error(f"❌ Gemini Analysis Failed: {e}")
            return None

    def get_optimal_upload_time(self) -> Optional[Dict[str, Any]]:
        """
        Main method to get the optimization result.
        Checks cache first. If expired/missing, fetches fresh data.
        """
        now = datetime.datetime.utcnow().timestamp()
        
        # 1. Check Cache
        cached_result = self.cache.get("optimization_result")
        last_fetch = self.cache.get("last_fetch_timestamp", 0)
        
        is_expired = (now - last_fetch) > (CACHE_DURATION_DAYS * 86400)
        
        if cached_result and not is_expired:
            logger.info(f"✨ Using Cached Upload Optimization (Expires in {int((CACHE_DURATION_DAYS * 86400 - (now - last_fetch))/86400)} days)")
            return cached_result
            
        # 2. Fetch Fresh Data
        logger.info("🔄 Cache expired or missing. Running full optimization cycle...")
        
        raw_data = self.fetch_viewer_data()
        if not raw_data:
            return cached_result # Fallback to old cache if fetch fails
            
        result = self.analyze_with_gemini(raw_data)
        
        if result:
            self.cache["optimization_result"] = result
            self.cache["last_fetch_timestamp"] = now
            self._save_cache(self.cache)
            logger.info(f"✅ New Optimization Saved: {result}")
            return result
        
        return cached_result

    def calculate_next_publish_time(self, day_name: str, hour: int) -> Optional[str]:
        """
        Calculates the next ISO 8601 UTC timestamp for the given day and hour.
        Assumes the input day/hour refers to YouTube Analytics default timezone (PST/PDT).
        """
        try:
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            target_weekday = days.index(day_name.capitalize())
            
            # Use UTC now as base
            now_utc = datetime.datetime.utcnow()
            
            # Estimate PST offset (approx -8 for standard, -7 for daylight)
            # For simplicity & safety, we'll assume -7 to be safe (earlier in UTC) or -8.
            # A fixed offset of -8 means 14:00 PST = 22:00 UTC. 
            # If we are wrong by 1 hour (DST), it's fine for YouTube upload optimization.
            pst_offset = -8
            
            # Start with current UTC time
            # We need to find a UTC time where (UTC_time + pst_offset) has the target weekday and hour.
            # So: target_utc_hour = hour - pst_offset
            
            target_utc_hour = hour - pst_offset
            if target_utc_hour >= 24:
                target_utc_hour -= 24
                # Moves to next day relative to PST inputs
                # But weekday calculation needs care.
                pass 
                
            # Simpler approach:
            # 1. Create a naive datetime for "Next [Day] at [Hour]"
            # 2. Treat it as PST.
            # 3. Convert to UTC.
            
            today = datetime.datetime.utcnow().date() # Close enough to PST date usually
            
            # Find next occurrence of weekday
            days_ahead = target_weekday - today.weekday()
            if days_ahead <= 0: # Target day already happened this week or is today
                days_ahead += 7
            
            # However, if it is today, check if hour passed.
            # Since we switched to next week if <= 0, we miss Today's later slots.
            # Fix:
            days_ahead = target_weekday - today.weekday()
            if days_ahead < 0:
                days_ahead += 7
            
            target_date = today + datetime.timedelta(days=days_ahead)
            
            # Construct target PST time
            # Note: This is naive. Ideally use pytz but environment might not have it.
            # We assume PST is UTC-8.
            
            target_pst_dt = datetime.datetime(
                target_date.year, target_date.month, target_date.day,
                hour, 0, 0
            )
            
            # Convert to UTC (-8h reverse is +8h)
            target_utc_dt = target_pst_dt + datetime.timedelta(hours=8)
            
            # Ensure it is in the future relative to NOW
            if target_utc_dt < now_utc:
                target_utc_dt += datetime.timedelta(days=7)
                
            return target_utc_dt.isoformat() + "Z"
            
        except Exception as e:
            logger.error(f"❌ Date calculation failed: {e}")
            return None

# Singleton instance
optimizer = AnalyticsOptimizer()
