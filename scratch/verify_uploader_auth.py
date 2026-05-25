import os
import sys
import subprocess
from unittest.mock import patch, MagicMock

# Add Uploader_Modules to path
sys.path.append(os.path.join(os.getcwd(), 'Uploader_Modules'))

import uploader

def test_auth_call():
    token_file = "test_token.json"
    client_secret_file = "test_secret.json"
    
    # Mock subprocess.check_call
    with patch('subprocess.check_call') as mock_call:
        # Mock os.path.exists to return False for token and True for refreshing (simulating trigger)
        # We need to mock get_valid_credentials internal logic or just call the part that calls subprocess
        
        # Actually, let's just test the logic I added to uploader.py
        # It's inside get_valid_credentials
        
        # We need to mock several things to reach that point
        with patch('os.path.exists', return_value=True):
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_file') as mock_creds_load:
                mock_creds = MagicMock()
                mock_creds.valid = False
                mock_creds.expired = True
                mock_creds.refresh_token = "some_token"
                mock_creds.refresh.side_effect = Exception("Refresh failed") # Trigger the fallback
                mock_creds_load.return_value = mock_creds
                
                try:
                    uploader.get_valid_credentials(niche="General_Fallback")
                except Exception:
                    pass
                
                # Check if subprocess was called with correct args
                # uploader.py: subprocess.check_call(auth_cmd)
                # auth_cmd = [sys.executable, "scripts/auth_youtube.py", "--token", ..., "--secret", ...]
                
                called_args = mock_call.call_args[0][0]
                print(f"Called Args: {called_args}")
                assert "scripts/auth_youtube.py" in called_args
                assert "--token" in called_args
                assert "Credentials\\social_media\\General_Fallback\\token.json" in str(called_args)
                assert "--secret" in called_args
                assert "Credentials\\social_media\\General_Fallback\\client_secret.json" in str(called_args)
                print("SUCCESS: YouTube Auth call arguments verified.")

if __name__ == "__main__":
    test_auth_call()
