import unittest
from unittest.mock import MagicMock, patch
from Intelligence_Modules.gemini_governor import GeminiGovernor

class TestModelStability(unittest.TestCase):
    def setUp(self):
        self.governor = GeminiGovernor()
        # Reset session states for testing
        self.governor.session_locks = {}
        self.governor.session_costs = {}

    def test_stability_lock_consistency(self):
        """Verify that the same session/task returns the same model."""
        session_id = "test_mission_123"
        task_type = "reasoning"
        
        # Turn 1: Get a model
        model_1 = self.governor.get_available_model(task_type, session_id=session_id)
        self.assertIsNotNone(model_1)
        
        # Turn 2: Get a model for the same task/session
        model_2 = self.governor.get_available_model(task_type, session_id=session_id)
        
        # Should be identical due to lock
        self.assertEqual(model_1, model_2, "Stability Lock failed to maintain model consistency.")
        self.assertIn(session_id, self.governor.session_locks)

    def test_lock_break_on_failure(self):
        """Verify that the lock is broken if the model fails."""
        session_id = "test_mission_456"
        task_type = "reasoning"
        
        # Turn 1: Get and lock a model
        model_1 = self.governor.get_available_model(task_type, session_id=session_id)
        
        # Simulate failure of model_1
        with patch('google.generativeai.GenerativeModel') as mock_model:
            mock_model.return_value.generate_content.side_effect = Exception("Quota Exceeded")
            
            try:
                self.governor.generate(task_type, "test prompt", session_id=session_id)
            except:
                pass
        
        # Lock should be cleared now
        self.assertNotIn(task_type, self.governor.session_locks.get(session_id, {}))
        
        # Next call should pick a different model (or at least re-evaluate)
        # Note: In a real test, model_1 would be banned, making model_2 different.
        self.governor.mark_model_banned(model_1)
        model_2 = self.governor.get_available_model(task_type, session_id=session_id)
        self.assertNotEqual(model_1, model_2)

    def test_max_cost_guard_activation(self):
        """Verify that the system forces Lite models when budget is exceeded."""
        session_id = "expensive_mission"
        self.governor.session_costs[session_id] = self.governor.MAX_BUDGET + 1
        
        task_type = "reasoning"
        model = self.governor.get_available_model(task_type, session_id=session_id)
        
        # Should be a lite model
        self.assertIn("lite", model.lower(), "Max Cost Guard failed to force Lite model.")

if __name__ == "__main__":
    unittest.main()
